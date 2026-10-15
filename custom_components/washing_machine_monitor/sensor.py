"""Status, wash counts, the last wash and the running totals kept for long-term statistics."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import WashEntity
from .monitor import WashConfigEntry, WashMonitor
from .tracker import STATUS_FINISHED, STATUS_IDLE, STATUS_WASHING


@dataclass(frozen=True, kw_only=True)
class WashSensorDescription(SensorEntityDescription):
    value: Callable[[WashMonitor], Any]
    attrs: Callable[[WashMonitor], dict[str, Any] | None] = lambda m: None


def _status_attrs(m: WashMonitor) -> dict[str, Any]:
    cur = m.current_wash()
    if cur:
        return cur
    last = m.last_wash
    return {"finished": last.end.isoformat()} if last else {}


def _last_attrs(m: WashMonitor) -> dict[str, Any]:
    last = m.last_wash
    if not last:
        return {}
    d = last.as_dict()
    return {"started": d["start"], "finished": d["end"], "minutes": d["minutes"], "cost": d["cost"]}


SENSORS: tuple[WashSensorDescription, ...] = (
    WashSensorDescription(
        key="status",
        device_class=SensorDeviceClass.ENUM,
        options=[STATUS_IDLE, STATUS_WASHING, STATUS_FINISHED],
        value=lambda m: m.status,
        attrs=_status_attrs,
    ),
    # Counts of finished washes, with their energy, cost and minutes as attributes.
    WashSensorDescription(
        key="washes_today",
        value=lambda m: m.totals_on_last_days(1)["washes"],
        attrs=lambda m: {k: v for k, v in m.totals_on_last_days(1).items() if k != "washes"},
    ),
    WashSensorDescription(
        key="washes_last_7_days",
        value=lambda m: m.totals_on_last_days(7)["washes"],
        attrs=lambda m: {k: v for k, v in m.totals_on_last_days(7).items() if k != "washes"},
    ),
    WashSensorDescription(
        key="last_wash",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value=lambda m: m.last_wash.kwh if m.last_wash else None,
        attrs=_last_attrs,
    ),
    # Running totals: Home Assistant keeps their statistics for good, so washes and wash time
    # per day, week or month can be charted for any period.
    WashSensorDescription(
        key="washes",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value=lambda m: m.total_washes,
    ),
    WashSensorDescription(
        key="wash_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value=lambda m: m.total_minutes,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: WashConfigEntry, add: AddEntitiesCallback) -> None:
    add([(WashStatusSensor if d.key == "status" else WashSensor)(entry.runtime_data, d) for d in SENSORS])


class WashSensor(WashEntity, SensorEntity):
    entity_description: WashSensorDescription

    def __init__(self, monitor: WashMonitor, description: WashSensorDescription) -> None:
        super().__init__(monitor, description.key)
        self.entity_description = description
        self._needs_power = description.key == "status"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.monitor)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attrs(self.monitor)


class WashStatusSensor(WashSensor):
    # The running wash's energy so far moves in 0.01 kWh steps for the card. The recorder still
    # writes a row per step, but leaves the value out: each finished wash keeps its own.
    _unrecorded_attributes = frozenset({"kwh"})
