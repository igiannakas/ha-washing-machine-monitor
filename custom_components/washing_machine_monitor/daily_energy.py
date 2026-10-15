"""The plug's energy per day, as the Energy dashboard counts it.

The washing machine's entry under Settings → Energy → Individual devices names the statistic: the
entry whose power sensor is the monitor's, else the one energy sensor on the plug's device. Past
days are that statistic's daily change, exactly what the Energy dashboard shows. Today is its
change so far (to the latest 5-minute statistics) plus what the meter has counted since, so a new
meter reading shows at once.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    statistic_during_period,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

KWH = {"energy": "kWh"}
RESET_DROP = 0.9  # a reading under 90 % of the last statistics' is a reset (a daily counter at midnight)


def board_statistic(hass: HomeAssistant, prefs: dict[str, Any], power_entity_id: str) -> str | None:
    """The Energy dashboard's statistic for the plug, or None when it isn't on the dashboard."""
    devices = [d for d in prefs.get("device_consumption") or [] if d.get("stat_consumption")]
    for device in devices:
        if device.get("stat_rate") == power_entity_id:
            return device["stat_consumption"]
    reg = er.async_get(hass)
    power = reg.async_get(power_entity_id)
    if power is None or power.device_id is None:
        return None
    on_device = [
        d["stat_consumption"]
        for d in devices
        if (entry := reg.async_get(d["stat_consumption"])) is not None and entry.device_id == power.device_id
    ]
    return on_device[0] if len(on_device) == 1 else None


def live_kwh(state: State | None) -> float | None:
    """An energy sensor's reading in kWh, whatever energy unit it reports."""
    if state is None:
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    unit = state.attributes.get("unit_of_measurement")
    if not math.isfinite(value) or unit not in EnergyConverter.VALID_UNITS:
        return None
    return EnergyConverter.convert(value, unit, "kWh")


async def async_energy_per_day(hass: HomeAssistant, statistic: str, first: date, last: date) -> dict[str, float]:
    """kWh per local day from first to last (today at most), keyed by ISO date."""
    tz = dt_util.get_default_time_zone()
    today = dt_util.now(tz).date()
    last = min(last, today)
    if first > last:
        return {}

    def midnight(day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=tz)

    recorder = get_instance(hass)
    days = {(first + timedelta(days=n)).isoformat(): 0.0 for n in range((last - first).days + 1)}
    past_end = min(last + timedelta(days=1), today)
    if first < past_end:
        rows = await recorder.async_add_executor_job(
            statistics_during_period, hass, midnight(first), midnight(past_end), {statistic}, "day", KWH, {"change"}
        )
        for row in rows.get(statistic, []):
            key = dt_util.as_local(dt_util.utc_from_timestamp(row["start"])).date().isoformat()
            if key in days:
                days[key] = row.get("change") or 0.0
    if last == today:
        days[today.isoformat()] = await _async_today(hass, statistic, midnight(today))
    return days


async def _async_today(hass: HomeAssistant, statistic: str, start: datetime) -> float:
    recorder = get_instance(hass)
    so_far = await recorder.async_add_executor_job(
        statistic_during_period, hass, start, None, statistic, {"change"}, KWH
    )
    change = so_far.get("change") or 0.0
    live = live_kwh(hass.states.get(statistic))
    if live is None:
        return change
    recent = await recorder.async_add_executor_job(
        statistics_during_period, hass, dt_util.utcnow() - timedelta(hours=1), None, {statistic}, "5minute", KWH, {"state"}
    )
    rows = recent.get(statistic) or []
    anchor = rows[-1].get("state") if rows else None
    if anchor is None:
        return change
    return change + (live if live < anchor * RESET_DROP else max(0.0, live - anchor))
