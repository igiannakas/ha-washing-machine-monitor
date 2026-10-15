"""The Home Assistant side of a monitored washing machine.

Feeds the plug's power readings to the tracker, keeps the wash log in Home Assistant's
storage (every wash, kept for good), sends the "washing done" push and the Live Activity (see
phones.py) and tells the entities to update. A missing power sensor raises a repair.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .const import (
    CONF_FINISH_DELAY,
    CONF_FINISHED_HOLD,
    CONF_LIVE_ACTIVITY,
    CONF_MIN_WASH,
    CONF_NOTIFY,
    CONF_NOTIFY_TITLE,
    CONF_OPEN_PATH,
    CONF_POWER,
    CONF_PRICE,
    CONF_THRESHOLD,
    DEFAULT_FINISH_DELAY,
    DEFAULT_FINISHED_HOLD,
    DEFAULT_LIVE_ACTIVITY,
    DEFAULT_MIN_WASH,
    DEFAULT_NOTIFY_TITLE,
    DEFAULT_THRESHOLD,
    DOMAIN,
    SAVE_DELAY_S,
    STORAGE_VERSION,
)
from .daily_energy import async_energy_per_day, board_statistic
from .phones import LIVE_AFTER, finished_push, is_phone, live_done, live_end, live_washing, wash_id
from .tracker import (
    STATUS_FINISHED,
    STATUS_IDLE,
    STATUS_WASHING,
    TrackerConfig,
    TrackerState,
    Wash,
    WashTracker,
    to_watts,
    totals_on_days,
)

_LOGGER = logging.getLogger(__name__)

LIVE_WASHING = "washing"
LIVE_DONE = "done"


def live_tag(entry_id: str) -> str:
    """The Live Activity's tag: letters, digits and underscores, under the 64 characters allowed."""
    return f"{DOMAIN}_{entry_id}_live"


async def async_send(hass: HomeAssistant, title: str, services: list[str], payload: dict[str, Any], what: str) -> None:
    """Send a payload to each notify action. A missing action is skipped (with a warning for the
    push, quietly for Live Activity updates), and one that fails never stops the others."""
    for service in services:
        name = service.removeprefix("notify.")
        if not hass.services.has_service("notify", name):
            (_LOGGER.warning if what == "push" else _LOGGER.debug)(
                "%s: notify.%s doesn't exist; no %s sent there", title, name, what
            )
            continue
        try:
            # Not blocking: a slow phone never holds up the others.
            await hass.services.async_call("notify", name, payload, blocking=False)
        except (HomeAssistantError, vol.Invalid):
            _LOGGER.exception("%s: the %s to notify.%s failed", title, what, name)


def _value(state: State | None) -> float | None:
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def read_watts(state: State | None) -> float | None:
    """A power sensor's reading in W, whatever power unit it reports."""
    v = _value(state)
    return None if v is None else to_watts(v, state.attributes.get("unit_of_measurement"))


def price_per_kwh(state: State | None) -> float | None:
    """A price entity's value per kWh, whatever energy unit its unit is per (GBP/kWh, EUR/MWh)."""
    v = _value(state)
    if v is None:
        return None
    unit = str(state.attributes.get("unit_of_measurement") or "")
    per = unit.rsplit("/", 1)[-1].strip() if "/" in unit else "kWh"
    if per not in EnergyConverter.VALID_UNITS:
        return v  # no energy unit to convert from: taken as per kWh, like Home Assistant does
    return v * EnergyConverter.convert(1, "kWh", per)


def price_from_energy_prefs(hass: HomeAssistant, prefs: dict[str, Any]) -> float | None:
    """The grid import price in Settings > Energy: a fixed price or a price entity."""
    flows: list[dict[str, Any]] = []
    for src in prefs.get("energy_sources", []):
        if src.get("type") != "grid":
            continue
        if src.get("stat_energy_from"):
            flows.append(src)  # current format
        flows.extend(src.get("flow_from", []))  # older format
    for f in flows:
        if f.get("entity_energy_price"):
            return price_per_kwh(hass.states.get(f["entity_energy_price"]))
        if isinstance(f.get("number_energy_price"), int | float):
            return float(f["number_energy_price"])
    return None


class WashMonitor:
    """One washing machine: its tracker, wash log, timers and listeners."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        # Atomic writes: the wash log is kept for good, so a crash mid-write must never truncate it.
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}", atomic_writes=True
        )
        self.tracker = WashTracker(config=self._tracker_config())
        self.log: list[Wash] = []
        self.total_washes = 0
        self.total_minutes = 0
        self.power_available = False
        self.finished_at: datetime | None = None  # when the last wash was found to have finished
        # What the phones' Live Activity shows: LIVE_WASHING, LIVE_DONE or None. Kept over a
        # restart, so a restart neither starts it again (an alert) nor leaves one behind.
        self.live: str | None = None
        self._loaded = False  # the log was read: only then may it be written
        self._ready = False  # entities exist and Home Assistant has started: finishes may run
        self._finishing = False  # a finish task is queued or running
        self._stopped = False
        self._listeners: list[CALLBACK_TYPE] = []
        self._finish_listeners: list[Callable[[Wash], None]] = []
        self._finish_timer: CALLBACK_TYPE | None = None
        self._hold_timer: CALLBACK_TYPE | None = None
        self._live_timer: CALLBACK_TYPE | None = None

    # --- settings -----------------------------------------------------------------------

    def opt(self, key: str, default: Any = None) -> Any:
        if key in self.entry.options:
            return self.entry.options[key]
        return self.entry.data.get(key, default)

    @property
    def power_entity_id(self) -> str:
        return self.opt(CONF_POWER)

    def _tracker_config(self) -> TrackerConfig:
        return TrackerConfig(
            threshold_w=float(self.opt(CONF_THRESHOLD, DEFAULT_THRESHOLD)),
            finish_delay=timedelta(minutes=float(self.opt(CONF_FINISH_DELAY, DEFAULT_FINISH_DELAY))),
            min_wash=timedelta(minutes=float(self.opt(CONF_MIN_WASH, DEFAULT_MIN_WASH))),
        )

    @property
    def finished_hold(self) -> timedelta:
        return timedelta(minutes=float(self.opt(CONF_FINISHED_HOLD, DEFAULT_FINISHED_HOLD)))

    def _phone_services(self) -> list[str]:
        """The companion app's notify actions among those picked: the ones Live Activities reach."""
        return [s for s in self.opt(CONF_NOTIFY) or [] if is_phone(s)]

    @property
    def phones(self) -> list[str]:
        """The phones that get the Live Activity: none while it is turned off."""
        return self._phone_services() if self.opt(CONF_LIVE_ACTIVITY, DEFAULT_LIVE_ACTIVITY) else []

    async def _async_energy_prefs(self) -> dict[str, Any] | None:
        """Settings > Energy, or None without the Energy integration."""
        if "energy" not in self.hass.config.components:
            return None
        from homeassistant.components.energy.data import async_get_manager

        manager = await async_get_manager(self.hass)
        return manager.data or {}

    async def async_price(self) -> float | None:
        """Price per kWh: the entry's own setting, else the grid price in Settings > Energy."""
        own = self.opt(CONF_PRICE)
        if isinstance(own, int | float):
            return float(own)
        prefs = await self._async_energy_prefs()
        return None if prefs is None else price_from_energy_prefs(self.hass, prefs)

    async def async_daily_energy(self, first: date, last: date) -> dict[str, Any] | None:
        """The plug's kWh per day from the Energy dashboard: {"statistic", "days"}, or None when
        the plug isn't on the dashboard (or the recorder isn't running)."""
        if "recorder" not in self.hass.config.components:
            return None
        prefs = await self._async_energy_prefs()
        statistic = None if prefs is None else board_statistic(self.hass, prefs, self.power_entity_id)
        if statistic is None:
            return None
        return {"statistic": statistic, "days": await async_energy_per_day(self.hass, statistic, first, last)}

    # --- lifecycle ----------------------------------------------------------------------

    async def async_start(self) -> None:
        """Load the log and the wash in progress, and start listening. Nothing finishes yet."""
        # A log that can't be read (a newer version's, or damaged) fails the setup and is left
        # exactly as it is: nothing is written until it has been read.
        data = await self._store.async_load() or {}
        try:
            self.tracker.state = TrackerState.from_dict(data.get("tracker"))
            self.log = [Wash.from_dict(w) for w in data.get("washes", [])]
            self.total_washes = int(data.get("total_washes", len(self.log)))
            self.total_minutes = int(data.get("total_minutes", sum(w.minutes for w in self.log)))
            self.finished_at = datetime.fromisoformat(data["finished_at"]) if data.get("finished_at") else None
            self.live = data.get("live") if data.get("live") in (LIVE_WASHING, LIVE_DONE) else None
        except (KeyError, TypeError, ValueError) as err:
            raise ConfigEntryError(
                f"The wash log in .storage/{self._store.key} can't be read ({err!r}); it has been left untouched"
            ) from err
        self._loaded = True

        on_unload = self.entry.async_on_unload
        on_unload(async_track_state_change_event(self.hass, self.power_entity_id, self._on_reading))
        on_unload(async_track_time_change(self.hass, self._on_midnight, hour=0, minute=0, second=2))
        # Home Assistant shutting down doesn't unload entries: stop the timers and save then too.
        on_unload(self.hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, self._on_hass_stop))
        # Counting starts again from the current reading: the draw while Home Assistant was away
        # is unknown.
        self._feed(dt_util.utcnow(), self.hass.states.get(self.power_entity_id))

    @callback
    def async_activate(self) -> None:
        """Called once the entities exist. Finishes (and their push) run from when Home Assistant
        has started, so a finish that fell due while it was down still reaches the event entity
        and the phones."""
        self.entry.async_on_unload(async_at_started(self.hass, self._on_started))

    @callback
    def _on_started(self, _hass: HomeAssistant) -> None:
        if self._stopped:
            return
        self._ready = True
        self._check_power_entity()
        # A Live Activity left over from before the restart: one turned off since goes, and so does
        # a washing one without a wash (none running, none waiting to finish). A done one stays until
        # someone opens the wash; a washing one stays while the wash carries on or its finish, still
        # to come, turns it green.
        if self.live and (not self.phones or (self.live == LIVE_WASHING and not self.tracker.running)):
            self._end_live()
        self._feed(dt_util.utcnow(), self.hass.states.get(self.power_entity_id))
        self._schedule()
        self._notify_listeners()

    async def _on_hass_stop(self, _event: Event) -> None:
        self._stopped = True
        self._cancel(finish=True, hold=True, live=True)
        self.tracker.count(dt_util.utcnow())
        await self._async_save()

    async def async_stop(self) -> None:
        """Unloading: stop the timers, drop the repair and save. Listeners go with the entry."""
        self._stopped = True
        self._cancel(finish=True, hold=True, live=True)
        self.tracker.count(dt_util.utcnow())
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        await self._async_save()

    def _data(self) -> dict[str, Any]:
        return {
            "tracker": self.tracker.state.as_dict(),
            "washes": [w.as_dict() for w in self.log],
            "total_washes": self.total_washes,
            "total_minutes": self.total_minutes,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "live": self.live,
        }

    async def _async_save(self) -> None:
        if not self._loaded:
            return
        try:
            await self._store.async_save(self._data())
        except (OSError, HomeAssistantError):
            _LOGGER.exception("%s: could not save the wash log", self.entry.title)

    # --- readings and timers ------------------------------------------------------------

    @callback
    def _on_reading(self, event: Event[EventStateChangedData]) -> None:
        old = event.data["old_state"]
        self._feed(dt_util.utcnow(), event.data["new_state"], old.last_reported if old else None)
        if self._ready:
            self._check_power_entity()
        self._schedule()

    @property
    def issue_id(self) -> str:
        return f"power_entity_missing_{self.entry.entry_id}"

    def _check_power_entity(self) -> None:
        """A repair while the power sensor is missing (deleted or renamed) or disabled."""
        if self.hass.states.get(self.power_entity_id) is not None:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            return
        reg = er.async_get(self.hass).async_get(self.power_entity_id)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="power_entity_disabled" if reg and reg.disabled_by else "power_entity_missing",
            translation_placeholders={"entity_id": self.power_entity_id, "name": self.entry.title},
        )

    def _feed(self, now: datetime, state: State | None, last_seen: datetime | None = None) -> None:
        if self._stopped:
            return
        watts = read_watts(state)
        before = self._shown()
        self.power_available = watts is not None
        if self.tracker.power(now, watts, last_seen):
            _LOGGER.debug("%s: wash started", self.entry.title)
            if self.live == LIVE_DONE:
                self._end_live()  # a new wash: the last one is out of the machine
        self._update_live(now)
        after = self._shown()
        # Saved when the wash moves on (and as its energy grows, a step at a time), so a crash
        # loses little; the entities redraw when what they show changes.
        if after[:3] != before[:3] and self._loaded:
            self._store.async_delay_save(self._data, SAVE_DELAY_S)
        if after[0] != before[0] or after[2:] != before[2:]:
            self._notify_listeners()

    def _shown(self) -> tuple[Any, ...]:
        """running, (below, blind), the energy so far as shown, plug available."""
        s = self.tracker.state
        return (s.running, (s.below_since, s.blind_since), self._kwh_so_far(), self.power_available)

    def _kwh_so_far(self) -> float | None:
        """The running wash's energy, as the status shows it (to 0.01 kWh)."""
        return round(self.tracker.state.used_kwh, 2) if self.tracker.running else None

    # --- the Live Activity ----------------------------------------------------------------

    def _update_live(self, now: datetime) -> None:
        """Start the Live Activity once the wash is clearly under way: LIVE_AFTER in, and drawing
        power now (not in a pause or with the plug unavailable)."""
        s = self.tracker.state
        if (
            not self._ready
            or self.live == LIVE_WASHING
            or not s.running
            or s.started is None
            or s.below_since is not None
            or s.blind_since is not None
            or now - s.started < LIVE_AFTER
            or not self.phones
        ):
            return
        self.live = LIVE_WASHING
        self._send_live(
            live_washing(s.started, tz=dt_util.get_default_time_zone(), path=self.opt(CONF_OPEN_PATH),
                         tag=live_tag(self.entry.entry_id)),
            self.phones,
        )

    def _finish_live(self, wash: Wash, shown: bool) -> None:
        """At the end of a run: green, and it stays until someone opens the wash from the push or
        from it (async_wash_seen), or the next wash starts. Gone at once for a run too short to count
        or an uncertain end, which get no push."""
        if not self.live:
            return
        if shown and self.phones:
            self.live = LIVE_DONE
            self._send_live(
                live_done(wash, tz=dt_util.get_default_time_zone(), path=self.opt(CONF_OPEN_PATH),
                          tag=live_tag(self.entry.entry_id)),
                self.phones,
            )
        else:
            self._end_live()

    @callback
    def async_wash_seen(self, wash: int) -> bool:
        """Someone opened the finished wash from its push or its Live Activity: the Live Activity has
        done its job and goes from every phone. Only for the last wash while it shows as done, so an
        old push opened later never ends the next wash's. True when it ended."""
        last = self.last_wash
        if self.live != LIVE_DONE or last is None or wash_id(last) != wash:
            return False
        self._end_live()
        return True

    def _end_live(self) -> None:
        """End it on every phone picked, also when the Live Activity has just been turned off."""
        self.live = None
        self._send_live(live_end(live_tag(self.entry.entry_id)), self._phone_services())

    def _send_live(self, payload: dict[str, Any], services: list[str]) -> None:
        """Queued in order: a start, its end and the next start reach each phone in that order."""
        if services:
            self.entry.async_create_task(
                self.hass, async_send(self.hass, self.entry.title, services, payload, "Live Activity update"),
                eager_start=False,
            )
        if self._loaded:
            self._store.async_delay_save(self._data, SAVE_DELAY_S)

    def _cancel(self, finish: bool = False, hold: bool = False, live: bool = False) -> None:
        if finish and self._finish_timer:
            self._finish_timer()
            self._finish_timer = None
        if hold and self._hold_timer:
            self._hold_timer()
            self._hold_timer = None
        if live and self._live_timer:
            self._live_timer()
            self._live_timer = None

    @callback
    def _schedule(self) -> None:
        """Arm the finish timer for a pending finish, the timer that ends "finished", and the one
        that starts the Live Activity: a plug with a steady draw may send no reading just then."""
        if not self._ready or self._stopped:
            return
        self._cancel(finish=True, hold=True, live=True)
        now = dt_util.utcnow()
        s = self.tracker.state
        if s.running and s.started and self.live != LIVE_WASHING and s.started + LIVE_AFTER > now and self.phones:
            self._live_timer = async_track_point_in_utc_time(self.hass, self._on_live_timer, s.started + LIVE_AFTER)
        hold_ends = self._hold_ends()
        if hold_ends and not self.tracker.running and hold_ends > now:
            self._hold_timer = async_track_point_in_utc_time(self.hass, self._on_hold_over, hold_ends)
        due = self.tracker.finish_due()
        if due is not None and due > now:
            self._finish_timer = async_track_point_in_utc_time(self.hass, self._on_finish_timer, due)
        elif due is not None:
            self._start_finish()

    def _hold_ends(self) -> datetime | None:
        return self.finished_at + self.finished_hold if self.finished_at and self.log else None

    @callback
    def _start_finish(self) -> None:
        """Queue the finish once. Not eager: it re-schedules when done, so nothing in the caller
        may run after it."""
        if self._finishing:
            return
        self._finishing = True
        self.entry.async_create_task(self.hass, self._async_finish(), eager_start=False)

    @callback
    def _on_finish_timer(self, _now: datetime) -> None:
        self._finish_timer = None
        self._start_finish()

    @callback
    def _on_live_timer(self, now: datetime) -> None:
        self._live_timer = None
        self._update_live(now)

    @callback
    def _on_hold_over(self, _now: datetime) -> None:
        self._hold_timer = None
        self._notify_listeners()

    @callback
    def _on_midnight(self, _now: datetime) -> None:
        self._notify_listeners()  # "today" and "last 7 days" move on

    async def _async_finish(self) -> None:
        try:
            await self._async_finish_wash()
        finally:
            self._finishing = False

    async def _async_finish_wash(self) -> None:
        if self._stopped:
            return
        price = await self.async_price()
        if self._stopped:  # stopped while the price was looked up: leave the wash for next time
            return
        now = dt_util.utcnow()
        result = self.tracker.check(now, price)
        if result is None:
            return
        wash = result.wash
        if result.counted:
            self.log.append(wash)
            self.total_washes += 1
            self.total_minutes += wash.minutes
            self.finished_at = now
            _LOGGER.debug("%s: wash finished: %s", self.entry.title, wash.as_dict())
        else:
            _LOGGER.debug("%s: a %s-minute run was too short to count", self.entry.title, wash.minutes)
        await self._async_save()
        self._schedule()
        self._notify_listeners()
        if result.counted:
            for cb in list(self._finish_listeners):
                cb(wash)
            if result.uncertain:
                # The plug was unavailable for hours: when it really finished is unknown, so no
                # "washing done" push for a moment that may be long past.
                _LOGGER.warning(
                    "%s: the plug was unavailable for %s; the wash was ended at %s, the last time it was known to run",
                    self.entry.title, self.tracker.config.max_blind, wash.end.isoformat(),
                )
            else:
                await self._async_push(wash)
        self._finish_live(wash, shown=result.counted and not result.uncertain)

    async def _async_push(self, wash: Wash) -> None:
        services: list[str] = self.opt(CONF_NOTIFY) or []
        if not services:
            return
        payload = finished_push(
            wash,
            title=self.opt(CONF_NOTIFY_TITLE, DEFAULT_NOTIFY_TITLE),
            tz=dt_util.get_default_time_zone(),
            currency=self.hass.config.currency,
            path=self.opt(CONF_OPEN_PATH),
            tag=f"{DOMAIN}_{self.entry.entry_id}",
            thread=DOMAIN,
        )
        await async_send(self.hass, self.entry.title, services, payload, "push")

    # --- what the entities show ---------------------------------------------------------

    @property
    def last_wash(self) -> Wash | None:
        return self.log[-1] if self.log else None

    @property
    def status(self) -> str:
        if self.tracker.running:
            return STATUS_WASHING
        hold_ends = self._hold_ends()
        if hold_ends and dt_util.utcnow() < hold_ends:
            return STATUS_FINISHED
        return STATUS_IDLE

    def totals_on_last_days(self, days: int) -> dict[str, Any]:
        """Washes, kWh, cost and minutes for today (days=1) or the last N days including today."""
        tz = dt_util.get_default_time_zone()
        today = dt_util.now(tz).date()
        return totals_on_days(self.log, today - timedelta(days=days - 1), today, tz)

    def current_wash(self) -> dict[str, Any] | None:
        s = self.tracker.state
        if not s.running:
            return None
        return {"started": s.started.isoformat() if s.started else None, "kwh": self._kwh_so_far()}

    @callback
    def async_add_listener(self, cb: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def async_add_finish_listener(self, cb: Callable[[Wash], None]) -> CALLBACK_TYPE:
        self._finish_listeners.append(cb)
        return lambda: self._finish_listeners.remove(cb)

    @callback
    def _notify_listeners(self) -> None:
        for cb in list(self._listeners):
            cb()


type WashConfigEntry = ConfigEntry[WashMonitor]
