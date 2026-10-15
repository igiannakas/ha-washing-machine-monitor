"""The card's data: the wash log for a period, the plug's energy per day, the current wash and the
entity ids; and the card telling the integration it opened a finished wash from its push."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .tracker import washes_between


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_washes)
    websocket_api.async_register_command(hass, ws_wash_seen)


def _monitor_for(hass: HomeAssistant, entity_id: str):
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or entry.config_entry_id is None:
        return None
    config_entry = hass.config_entries.async_get_entry(entry.config_entry_id)
    if config_entry is None or config_entry.domain != DOMAIN:
        return None
    return getattr(config_entry, "runtime_data", None)


def _when(value: str | None, default: datetime) -> datetime:
    """An ISO date-time; one without a time zone is taken in Home Assistant's own."""
    if not value:
        return default
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        raise ValueError(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.get_default_time_zone())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/washes",
        vol.Required("entity_id"): str,
        vol.Optional("start"): str,
        vol.Optional("end"): str,
    }
)
@websocket_api.async_response
async def ws_washes(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Washes that finished between start and end (default: the last 7 days), plus live context."""
    monitor = _monitor_for(hass, msg["entity_id"])
    if monitor is None:
        connection.send_error(msg["id"], "not_found", "Not a Washing Machine Monitor entity")
        return
    now = dt_util.utcnow()
    try:
        start = _when(msg.get("start"), now - timedelta(days=7))
        end = _when(msg.get("end"), now + timedelta(minutes=1))
    except ValueError:
        connection.send_error(msg["id"], "invalid_format", "start and end must be ISO date-times")
        return
    reg = er.async_get(hass)
    entities = {
        e.unique_id.removeprefix(f"{monitor.entry.entry_id}_"): e.entity_id
        for e in er.async_entries_for_config_entry(reg, monitor.entry.entry_id)
    }
    first = monitor.log[0].end.isoformat() if monitor.log else None
    tz = dt_util.get_default_time_zone()
    energy = await monitor.async_daily_energy(
        start.astimezone(tz).date(), (end - timedelta(microseconds=1)).astimezone(tz).date()
    )
    connection.send_result(
        msg["id"],
        {
            "title": monitor.entry.title,
            "entities": entities,
            "power_entity_id": monitor.power_entity_id,
            "price": await monitor.async_price(),
            "currency": hass.config.currency,
            "status": monitor.status,
            "current": monitor.current_wash(),
            "first_wash": first,
            # The plug's kWh per local day, from the Energy dashboard (None: not on it).
            "energy": energy,
            "washes": [w.as_dict() for w in washes_between(monitor.log, start, end)],
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/wash_seen",
        vol.Required("entity_id"): str,
        vol.Required("wash"): vol.All(int, vol.Range(min=0)),
    }
)
@callback
def ws_wash_seen(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The card opened a finished wash from its push or Live Activity ({"wash": when it finished, in
    Unix seconds}): its Live Activity goes from the phones. {"ended": whether it was showing}."""
    monitor = _monitor_for(hass, msg["entity_id"])
    if monitor is None:
        connection.send_error(msg["id"], "not_found", "Not a Washing Machine Monitor entity")
        return
    connection.send_result(msg["id"], {"ended": monitor.async_wash_seen(msg["wash"])})
