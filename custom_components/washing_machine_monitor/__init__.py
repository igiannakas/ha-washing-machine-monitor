"""Washing Machine Monitor: washes, wash history and a "washing done" push from a smart plug."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device import async_entity_id_to_device_id
from homeassistant.helpers.helper_integration import (
    async_handle_source_entity_changes,
    async_remove_helper_devices,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .card import async_register_card
from .const import CONF_ENERGY, CONF_NOTIFY, CONF_POWER, DOMAIN, STORAGE_VERSION
from .monitor import WashConfigEntry, WashMonitor, async_send, live_tag
from .phones import is_phone, live_end
from .websocket import async_register_websocket

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the dashboard card (custom:washing-machine-card) and its data."""
    async_register_websocket(hass)
    await async_register_card(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.2: a wash's energy comes from the power readings, so the energy meter setting goes."""
    if entry.version == 1 and entry.minor_version < 2:
        hass.config_entries.async_update_entry(
            entry,
            data={k: v for k, v in entry.data.items() if k != CONF_ENERGY},
            options={k: v for k, v in entry.options.items() if k != CONF_ENERGY},
            minor_version=2,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WashConfigEntry) -> bool:
    power = entry.options[CONF_POWER]
    source_device_id = async_entity_id_to_device_id(hass, power)

    # The entities join the plug's device. A device of our own (made while the power sensor had
    # none) is removed once the sensor has one.
    if source_device_id:
        async_remove_helper_devices(
            hass, helper_config_entry_id=entry.entry_id, source_device_id=source_device_id, remove_all_devices=True
        )

    def set_power_entity(entity_id: str) -> None:
        """The power sensor was renamed: follow it."""
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_POWER: entity_id})

    # Follows the power sensor when it is renamed or moved to another device.
    entry.async_on_unload(
        async_handle_source_entity_changes(
            hass,
            helper_config_entry_id=entry.entry_id,
            set_source_entity_id_or_uuid=set_power_entity,
            source_device_id=source_device_id,
            source_entity_id_or_uuid=power,
        )
    )

    monitor = WashMonitor(hass, entry)
    entry.runtime_data = monitor
    await monitor.async_start()
    # Registered only once the log has been read: a failed read must never be followed by a save
    # of an empty log. It also runs if setting up the platforms fails. (Once stopped, the monitor
    # ignores readings that arrive before its listeners are removed.)
    entry.async_on_unload(monitor.async_stop)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    monitor.async_activate()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: WashConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WashConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Deleting the entry deletes its wash log and any repair it raised, and ends a Live Activity
    the phones still show (Apple would otherwise keep it for up to 8 hours)."""
    store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
    try:
        data = await store.async_load() or {}
    except (HomeAssistantError, NotImplementedError, OSError, ValueError):
        data = {}  # a log this version can't read: there is nothing to end from it
    if data.get("live"):
        phones = [s for s in entry.options.get(CONF_NOTIFY) or [] if is_phone(s)]
        await async_send(hass, entry.title, phones, live_end(live_tag(entry.entry_id)), "Live Activity update")
    await store.async_remove()
    ir.async_delete_issue(hass, DOMAIN, f"power_entity_missing_{entry.entry_id}")
