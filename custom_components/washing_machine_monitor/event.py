"""Wash: fires "finished" with the wash's times, energy and cost, for automations."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import EVENT_FINISHED
from .entity import WashEntity
from .monitor import WashConfigEntry
from .tracker import Wash


async def async_setup_entry(hass: HomeAssistant, entry: WashConfigEntry, add: AddEntitiesCallback) -> None:
    add([WashEvent(entry.runtime_data, "wash")])


class WashEvent(WashEntity, EventEntity):
    _attr_event_types = [EVENT_FINISHED]

    async def async_added_to_hass(self) -> None:
        # Only a finished wash changes this entity; the monitor's other updates don't.
        self.async_on_remove(self.monitor.async_add_finish_listener(self._finished))

    @callback
    def _finished(self, wash: Wash) -> None:
        self._trigger_event(EVENT_FINISHED, wash.as_dict())
        self.async_write_ha_state()
