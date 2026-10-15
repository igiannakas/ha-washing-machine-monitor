"""Set up a washing machine from its smart plug, and tune it afterwards."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

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
    DEFAULT_NAME,
    DEFAULT_NOTIFY_TITLE,
    DEFAULT_OPEN_PATH,
    DEFAULT_THRESHOLD,
    DOMAIN,
)
from .tracker import POWER_UNITS

_NOT_PEOPLE = {"notify", "send_message", "persistent_notification"}


def _notify_options(hass: HomeAssistant) -> list[selector.SelectOptionDict]:
    """The notify actions that reach a phone or a person, e.g. mobile_app_nancy_iphone."""
    names = sorted(n for n in hass.services.async_services_for_domain("notify") if n not in _NOT_PEOPLE)
    label = lambda n: n.removeprefix("mobile_app_").replace("_", " ").title()  # noqa: E731
    return [selector.SelectOptionDict(value=n, label=f"{label(n)} (notify.{n})") for n in names]


def _minutes(minimum: float, maximum: float) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=minimum, max=maximum, step=1, unit_of_measurement="min", mode="box")
    )


def _schema(hass: HomeAssistant, values: dict[str, Any], setup: bool) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if setup:
        fields[vol.Required("name", default=values.get("name", DEFAULT_NAME))] = str
    fields.update(
        {
            vol.Required(CONF_POWER, default=values.get(CONF_POWER, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            vol.Required(CONF_THRESHOLD, default=values.get(CONF_THRESHOLD, DEFAULT_THRESHOLD)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=200, step=1, unit_of_measurement="W", mode="box")
            ),
            vol.Required(CONF_FINISH_DELAY, default=values.get(CONF_FINISH_DELAY, DEFAULT_FINISH_DELAY)): _minutes(1, 60),
            vol.Required(CONF_MIN_WASH, default=values.get(CONF_MIN_WASH, DEFAULT_MIN_WASH)): _minutes(0, 120),
            vol.Required(CONF_FINISHED_HOLD, default=values.get(CONF_FINISHED_HOLD, DEFAULT_FINISHED_HOLD)): _minutes(0, 720),
            vol.Optional(CONF_NOTIFY, default=values.get(CONF_NOTIFY, [])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=_notify_options(hass), multiple=True, custom_value=True)
            ),
            vol.Required(
                CONF_LIVE_ACTIVITY, default=values.get(CONF_LIVE_ACTIVITY, DEFAULT_LIVE_ACTIVITY)
            ): selector.BooleanSelector(),
            vol.Required(CONF_NOTIFY_TITLE, default=values.get(CONF_NOTIFY_TITLE, DEFAULT_NOTIFY_TITLE)): str,
            # Suggested (not a default) so it can be cleared: no path, no link in the push.
            vol.Optional(
                CONF_OPEN_PATH,
                description={"suggested_value": values.get(CONF_OPEN_PATH, DEFAULT_OPEN_PATH if setup else None)},
            ): str,
            vol.Optional(CONF_PRICE, description={"suggested_value": values.get(CONF_PRICE)}): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, step="any", mode="box")
            ),
        }
    )
    return vol.Schema(fields)


def _validate(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Problems with the chosen sensors and phones, as {field: error key}."""
    errors: dict[str, str] = {}
    power = hass.states.get(data[CONF_POWER])
    if power is not None and power.attributes.get("unit_of_measurement") not in POWER_UNITS:
        errors[CONF_POWER] = "not_power"
    missing = [s for s in data.get(CONF_NOTIFY) or [] if not hass.services.has_service("notify", s.removeprefix("notify."))]
    if missing:
        errors[CONF_NOTIFY] = "notify_not_found"
    path = data.get(CONF_OPEN_PATH)
    if path and not path.startswith("/"):
        errors[CONF_OPEN_PATH] = "bad_path"
    if not str(data.get(CONF_NOTIFY_TITLE, "")).strip():
        errors[CONF_NOTIFY_TITLE] = "title_required"
    return errors


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    """Phones as bare service names (mobile_app_nancy_iphone), no empty optional fields."""
    out = {k: v for k, v in data.items() if v not in ("", None)}
    out[CONF_NOTIFY] = [s.removeprefix("notify.") for s in data.get(CONF_NOTIFY) or []]
    out[CONF_NOTIFY_TITLE] = str(data.get(CONF_NOTIFY_TITLE, "")).strip()
    return out


class WashingMachineConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry per washing machine, each with its own plug."""

    VERSION = 1
    MINOR_VERSION = 2  # 1.2: no energy meter (a wash's energy comes from the power readings)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._async_abort_entries_match({CONF_POWER: user_input[CONF_POWER]})
            errors = _validate(self.hass, user_input)
            name = str(user_input.get("name", "")).strip()
            if not name:
                errors["name"] = "name_required"
            if not errors:
                data = _clean(user_input)
                data.pop("name", None)
                return self.async_create_entry(title=name, data={}, options=data)
        return self.async_show_form(
            step_id="user", data_schema=_schema(self.hass, user_input or {}, setup=True), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return WashingMachineOptionsFlow()


class WashingMachineOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            others = [
                e for e in self.hass.config_entries.async_entries(DOMAIN)
                if e.entry_id != self.config_entry.entry_id and e.options.get(CONF_POWER) == user_input[CONF_POWER]
            ]
            errors = _validate(self.hass, user_input)
            if others:
                errors[CONF_POWER] = "already_used"
            if not errors:
                return self.async_create_entry(data=_clean(user_input))
        values = user_input or {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(self.hass, values, setup=False), errors=errors)
