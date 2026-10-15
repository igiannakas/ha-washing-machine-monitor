"""Diagnostics: the settings, the wash in progress, the plug's readings and the latest washes."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, State

from .monitor import WashConfigEntry


def _state(state: State | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {"state": state.state, "attributes": dict(state.attributes), "last_changed": state.last_changed.isoformat()}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: WashConfigEntry) -> dict[str, Any]:
    monitor = entry.runtime_data
    return {
        "options": dict(entry.options),
        "status": monitor.status,
        "power_available": monitor.power_available,
        "tracker": monitor.tracker.state.as_dict(),
        "finish_due": due.isoformat() if (due := monitor.tracker.finish_due()) else None,
        "price": await monitor.async_price(),
        "total_washes": monitor.total_washes,
        "total_minutes": monitor.total_minutes,
        "washes_logged": len(monitor.log),
        "latest_washes": [w.as_dict() for w in monitor.log[-20:]],
        "power": _state(hass.states.get(monitor.power_entity_id)),
    }
