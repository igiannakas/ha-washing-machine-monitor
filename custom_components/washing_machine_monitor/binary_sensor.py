"""Running: on from the first watt over the threshold until the wash has finished."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import WashEntity
from .monitor import WashConfigEntry


async def async_setup_entry(hass: HomeAssistant, entry: WashConfigEntry, add: AddEntitiesCallback) -> None:
    add([RunningSensor(entry.runtime_data, "running")])


class RunningSensor(WashEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _needs_power = True

    @property
    def is_on(self) -> bool:
        return self.monitor.tracker.running
