"""Shared base for the monitor's entities: they join the plug's own device."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .monitor import WashMonitor


class WashEntity(Entity):
    """An entity that redraws whenever the monitor changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # Live entities (running, status) are unavailable while the plug's power reading is; the
    # counts and the log don't depend on it.
    _needs_power = False

    def __init__(self, monitor: WashMonitor, key: str) -> None:
        self.monitor = monitor
        self._attr_translation_key = key
        self._attr_unique_id = f"{monitor.entry.entry_id}_{key}"
        # Show up on the smart plug's device page, next to its power and energy readings; a power
        # sensor without a device gets a device of its own, named after the entry.
        self.device_entry = async_entity_id_to_device(monitor.hass, monitor.power_entity_id)
        if self.device_entry is None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, monitor.entry.entry_id)},
                name=monitor.entry.title,
                entry_type=DeviceEntryType.SERVICE,
            )

    @property
    def available(self) -> bool:
        return self.monitor.power_available if self._needs_power else True

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.monitor.async_add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
