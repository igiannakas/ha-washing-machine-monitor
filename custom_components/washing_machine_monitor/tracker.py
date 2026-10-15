"""Wash detection and the wash log. No Home Assistant imports, so it is tested directly.

A wash starts when the plug draws more than the threshold. It ends once the draw has stayed at
or under the threshold for the finish delay: a washing machine drops to a few watts whenever the
drum pauses, soaks or drains, so a single dip must not end the wash. The wash's end time is the
moment the draw last fell under the threshold, not the moment the delay ran out. Runs shorter
than the minimum length (a door lock, a cancelled programme) are dropped and never counted.

While the plug's power reading is unavailable nothing ends: a pending finish waits for the next
real reading. If that reading is still low the wash ends at the time the draw first fell; if it
is high again the wash carries on. A plug that stays unavailable for MAX_BLIND ends the wash at
the last moment it was known to be running, flagged as uncertain (no push is sent for it).

A wash's energy is the area under the plug's power readings while it runs, the way Home
Assistant's Integral helper adds them up: each reading holds until the next one (plugs report when
the draw changes, and repeat it while it doesn't). Power readings arrive every few seconds, so
this places the energy exactly between the wash's start and its end; an energy meter reports far
less often and can't. A reading is counted at no more than a plug socket can deliver (MAX_W), so a
glitched reading can't inflate a wash. While the reading is unavailable, and while Home Assistant
isn't running, the draw is unknown and nothing is counted; a plug that goes quiet before it is
found unavailable counts only until its reading was last confirmed. The wash's energy stops where
the wash ends: what the machine draws during the finish delay isn't part of it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

STATUS_IDLE = "idle"
STATUS_WASHING = "washing"
STATUS_FINISHED = "finished"

MAX_W = 3700.0  # a 16 A socket at 230 V: no washing machine on a plug draws more
MAX_BLIND = timedelta(hours=6)  # longer than any programme: a plug offline this long ends the wash

# The default settings, measured on a washing machine on a smart plug over two programmes (14 Oct
# 2026, replayed in tests/test_tracker.py). Off: 0 W. On but idle, before a programme and after it:
# 2.5–5.5 W. Filling with water: 7.7–12 W. The drum: 12–53 W. Spin: 60–260 W. Heating: about 2.1 kW.
# THRESHOLD_W sits 2.5 W above idle, so a machine left on, or counting down a delayed start, never
# starts a wash; and under filling, so a fill doesn't look like a pause. The longest pause under it
# within a programme was 20 s: FINISH_DELAY outlasts that many times over, for programmes that rest
# the drum longer, and still sends the push soon after the last spin. A false start lasts seconds
# (a door lock, the machine's switch-on surge), the shortest wash measured 23 min, and a spin-only
# programme typically runs 10–15 min: MIN_WASH drops the first and counts the rest.
THRESHOLD_W = 8.0
FINISH_DELAY = timedelta(minutes=3)
MIN_WASH = timedelta(minutes=10)


@dataclass(frozen=True)
class TrackerConfig:
    """The thresholds that shape a wash."""

    threshold_w: float = THRESHOLD_W
    finish_delay: timedelta = FINISH_DELAY
    min_wash: timedelta = MIN_WASH
    max_blind: timedelta = MAX_BLIND


@dataclass
class Wash:
    """One finished wash."""

    start: datetime
    end: datetime
    kwh: float | None = None
    cost: float | None = None

    @property
    def minutes(self) -> int:
        return max(0, round((self.end - self.start).total_seconds() / 60))

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "minutes": self.minutes,
            "kwh": None if self.kwh is None else round(self.kwh, 3),
            "cost": None if self.cost is None else round(self.cost, 4),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Wash:
        return cls(
            start=datetime.fromisoformat(data["start"]),
            end=datetime.fromisoformat(data["end"]),
            kwh=data.get("kwh"),
            cost=data.get("cost"),
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


_TIMES = ("started", "below_since", "blind_since")


@dataclass
class TrackerState:
    """What survives a restart: the wash in progress, if any."""

    running: bool = False
    started: datetime | None = None
    below_since: datetime | None = None
    blind_since: datetime | None = None  # the power reading has been unavailable since then
    used_kwh: float = 0.0  # what the power readings add up to so far
    below_kwh: float | None = None  # used_kwh when the draw fell: the wash's energy if it ends there

    @property
    def blind(self) -> bool:
        return self.blind_since is not None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in _TIMES:
            d[k] = _iso(getattr(self, k))
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TrackerState:
        if not data:
            return cls()
        known = {k: data.get(k) for k in cls.__dataclass_fields__ if k in data}
        for k in _TIMES:
            if k in known:
                known[k] = _parse(known[k])
        known["running"] = bool(known.get("running"))
        # Logs from 1.1 and earlier counted from an energy meter, and None meant "no reading yet";
        # a finish they had pending ends with what was counted by then.
        known["used_kwh"] = float(known.get("used_kwh") or 0.0)
        if known.get("below_since") and known.get("below_kwh") is None:
            known["below_kwh"] = known["used_kwh"]
        return cls(**known)


@dataclass
class Finished:
    """The tracker's answer when a run ends: a counted wash, or a run too short to count.

    `uncertain`: the plug was unavailable for too long, so the end time is only the last moment
    the wash was known to run."""

    wash: Wash
    counted: bool
    uncertain: bool = False


@dataclass
class WashTracker:
    """Turns power readings into washes."""

    config: TrackerConfig = field(default_factory=TrackerConfig)
    state: TrackerState = field(default_factory=TrackerState)
    # The reading being counted until the next one, and since when. Not kept over a restart:
    # nothing is known about the draw while Home Assistant is down.
    _held: tuple[float, datetime] | None = field(default=None, init=False, repr=False)

    @property
    def running(self) -> bool:
        return self.state.running

    def power(self, now: datetime, watts: float | None, last_seen: datetime | None = None) -> bool:
        """Feed a power reading (W) taken at `now`. True when a wash starts.

        `last_seen`: when the previous reading was last confirmed by the plug. An unknown reading
        (plug unavailable) counts the previous one only until then, and never ends or splits a
        wash: a pending finish waits for the next real reading.
        """
        s = self.state
        if watts is None:
            self.count(min(last_seen, now) if last_seen else now)
            self._held = None
            if s.running and s.blind_since is None:
                s.blind_since = now
            return False
        self.count(now)
        s.blind_since = None
        if watts > self.config.threshold_w:
            s.below_since = s.below_kwh = None
            started = not s.running
            if started:
                self.state = TrackerState(running=True, started=now)
            self._held = (watts, now)
            return started
        if s.running:
            if s.below_since is None:
                s.below_since, s.below_kwh = now, s.used_kwh
            self._held = (watts, now)
        return False

    def count(self, now: datetime) -> None:
        """Add the held reading's energy up to `now`."""
        if not self.state.running or self._held is None:
            return
        watts, since = self._held
        hours = (now - since).total_seconds() / 3600
        if hours > 0:
            self.state.used_kwh += min(max(watts, 0.0), MAX_W) * hours / 1000
            self._held = (watts, now)

    def finish_due(self) -> datetime | None:
        """When the wash ends if nothing changes: the finish delay after the draw fell; while the
        power reading is unavailable, only once it has been unavailable for max_blind."""
        s = self.state
        if not s.running:
            return None
        if s.blind_since is not None:
            return s.blind_since + self.config.max_blind
        if s.below_since is None:
            return None
        return s.below_since + self.config.finish_delay

    def check(self, now: datetime, price: float | None) -> Finished | None:
        """End the wash if it is due."""
        due = self.finish_due()
        if due is None or now < due:
            return None
        s = self.state
        uncertain = s.blind_since is not None
        start = s.started or s.below_since or s.blind_since
        end = s.below_since or s.blind_since
        # Counting stopped when the plug went unavailable; the finish delay isn't part of the wash.
        used = s.below_kwh if s.below_since is not None and s.below_kwh is not None else s.used_kwh
        wash = Wash(start=start, end=end, kwh=used, cost=None if price is None else used * price)
        self.state = TrackerState()
        self._held = None
        return Finished(wash=wash, counted=(end - start) >= self.config.min_wash, uncertain=uncertain)


# --- The log --------------------------------------------------------------------------------


def local_day(moment: datetime, tz: tzinfo) -> date:
    return moment.astimezone(tz).date()


def washes_between(log: list[Wash], start: datetime, end: datetime) -> list[Wash]:
    """Washes that finished in [start, end)."""
    return [w for w in log if start <= w.end < end]


def totals_on_days(log: list[Wash], first: date, last: date, tz: tzinfo) -> dict[str, Any]:
    """Washes, energy, cost and minutes of the washes that finished on the local days first..last.

    Energy and cost add up the washes that have them, and are None when none does.
    """
    ws = [w for w in log if first <= local_day(w.end, tz) <= last]
    kwh = [w.kwh for w in ws if w.kwh is not None]
    cost = [w.cost for w in ws if w.cost is not None]
    return {
        "washes": len(ws),
        "kwh": round(sum(kwh), 3) if kwh else None,
        "cost": round(sum(cost), 4) if cost else None,
        "minutes": sum(w.minutes for w in ws),
    }


# --- Units and words ------------------------------------------------------------------------

POWER_UNITS = {"W": 1.0, "kW": 1000.0, "mW": 0.001, "MW": 1_000_000.0}
CURRENCY_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$"}
MINOR_UNITS = {"GBP": "p", "EUR": "c", "USD": "¢"}


def to_watts(value: float, unit: str | None) -> float | None:
    """A power reading in W; None for a unit that isn't power."""
    factor = POWER_UNITS.get(unit or "W")
    return None if factor is None else value * factor


NBSP = " "  # between a number and its unit: a line never breaks inside "0.84 kWh"


def format_duration(minutes: int) -> str:
    """52 min · 1 h 52 min · 2 h, with no-break spaces: it never splits over two lines."""
    h, m = divmod(max(0, int(minutes)), 60)
    if not h:
        return f"{m}{NBSP}min"
    return f"{h}{NBSP}h{NBSP}{m}{NBSP}min" if m else f"{h}{NBSP}h"


def format_money(value: float, currency: str | None) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency or "")
    return f"{symbol}{value:.2f}" if symbol else f"{value:.2f}{NBSP}{currency}" if currency else f"{value:.2f}"


def format_wash_cost(value: float, currency: str | None) -> str:
    """One wash's cost: pence under a pound (6p; under a penny 0.5p), from a pound up £1.20. As the
    dashboard's cost per hour. Currencies without a minor unit here keep their major unit."""
    unit = MINOR_UNITS.get(currency or "")
    if unit is None or value >= 0.995:
        return format_money(value, currency)
    pence = max(0.0, value) * 100
    return f"{pence:.1f}{unit}" if 0 < pence < 0.95 else f"{int(pence + 0.5)}{unit}"


def finished_message(wash: Wash, tz: tzinfo, currency: str | None) -> str:
    """Finished at 14:20 after 1 h 52 min · 0.84 kWh · 20p."""
    parts = [f"Finished at {wash.end.astimezone(tz):%H:%M} after {format_duration(wash.minutes)}"]
    if wash.kwh is not None:
        parts.append(f"{wash.kwh:.2f}{NBSP}kWh")
    if wash.cost is not None:
        parts.append(format_wash_cost(wash.cost, currency))
    return " · ".join(parts)
