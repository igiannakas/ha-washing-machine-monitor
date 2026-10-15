"""End to end on a real Home Assistant (pytest-homeassistant-custom-component).

Skipped when that package isn't installed, so the HA-free tests still run anywhere.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from freezegun.api import FrozenDateTimeFactory  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

DOMAIN = "washing_machine_monitor"
POWER = "sensor.plug_power"
OPTIONS = {
    "power_entity_id": POWER,
    "threshold_w": 10,
    "finish_delay_min": 5,
    "min_wash_min": 10,
    "finished_hold_min": 60,
    "notify_services": ["mobile_app_ioannis_iphone", "notify.mobile_app_nancy_iphone"],
    "notify_title": "Washing done",
    "open_path": "/lovelace/washing-machine",
    "price": 0.25,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def power(hass: HomeAssistant, watts: float | str, unit: str = "W") -> None:
    hass.states.async_set(POWER, str(watts), {"unit_of_measurement": unit, "device_class": "power"})


async def setup(hass: HomeAssistant, **opts) -> MockConfigEntry:
    await hass.config.async_set_time_zone("UTC")
    hass.config.currency = "GBP"
    power(hass, 1.5)
    entry = MockConfigEntry(domain=DOMAIN, title="Washing machine", data={}, options={**OPTIONS, **opts}, unique_id=POWER)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: float) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_entities_start_idle(hass: HomeAssistant) -> None:
    await setup(hass)
    assert hass.states.get("binary_sensor.washing_machine_running").state == "off"
    assert hass.states.get("sensor.washing_machine_status").state == "idle"
    assert hass.states.get("sensor.washing_machine_washes").state == "0"
    assert hass.states.get("sensor.washing_machine_washes_today").state == "0"
    assert hass.states.get("sensor.washing_machine_washes_last_7_days").state == "0"
    assert hass.states.get("sensor.washing_machine_wash_time").state == "0"
    assert hass.states.get("sensor.washing_machine_last_wash").state == "unknown"
    assert hass.states.get("event.washing_machine_wash") is not None


async def test_a_wash_from_start_to_push(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    ioannis = async_mock_service(hass, "notify", "mobile_app_ioannis_iphone")
    nancy = async_mock_service(hass, "notify", "mobile_app_nancy_iphone")
    freezer.move_to("2026-10-14 12:00:00+00:00")
    await setup(hass)

    power(hass, 150)
    await hass.async_block_till_done()
    status = hass.states.get("sensor.washing_machine_status")
    assert status.state == "washing"
    assert status.attributes["kwh"] == 0.0
    assert hass.states.get("binary_sensor.washing_machine_running").state == "on"

    # Heating, then a pause under 10 W that is shorter than the finish delay. The energy so far
    # comes from the power readings: 150 W for 20 minutes.
    await advance(hass, freezer, 20)
    power(hass, 2100)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.washing_machine_status").attributes["kwh"] == 0.05
    await advance(hass, freezer, 30)
    power(hass, 4)
    await advance(hass, freezer, 3)
    power(hass, 300)
    await advance(hass, freezer, 59)
    assert hass.states.get("sensor.washing_machine_status").state == "washing"

    # The last spin ends; standby. Five minutes later it has finished.
    power(hass, 2)
    await advance(hass, freezer, 4)
    assert hass.states.get("sensor.washing_machine_status").state == "washing"
    await advance(hass, freezer, 1.1)

    assert hass.states.get("sensor.washing_machine_status").state == "finished"
    assert hass.states.get("binary_sensor.washing_machine_running").state == "off"
    assert hass.states.get("sensor.washing_machine_washes").state == "1"
    assert hass.states.get("sensor.washing_machine_washes_today").state == "1"
    assert hass.states.get("sensor.washing_machine_wash_time").state == "112"
    # 150 W for 20 min, 2100 W for 30, 4 W for 3, 300 W for 59; standby after the end isn't counted.
    kwh = (150 * 20 + 2100 * 30 + 4 * 3 + 300 * 59) / 60 / 1000
    last = hass.states.get("sensor.washing_machine_last_wash")
    assert float(last.state) == pytest.approx(kwh, abs=1e-3)
    assert last.attributes["minutes"] == 112
    assert last.attributes["cost"] == pytest.approx(kwh * 0.25, abs=1e-4)
    today = hass.states.get("sensor.washing_machine_washes_today")
    assert today.attributes["kwh"] == pytest.approx(kwh, abs=1e-3) and today.attributes["minutes"] == 112
    assert "kwh" not in hass.states.get("sensor.washing_machine_status").attributes
    ev = hass.states.get("event.washing_machine_wash")
    assert ev.attributes["event_type"] == "finished" and ev.attributes["minutes"] == 112

    assert len(ioannis) == 1 and len(nancy) == 1
    call = ioannis[0].data
    assert nancy[0].data == call
    assert call["title"] == "Washing done"
    assert call["message"] == "Finished at 13:52 after 1\u00a0h\u00a052\u00a0min · 1.40\u00a0kWh · 35p"
    assert call["data"]["subtitle"] == "Unload the clothes"
    assert call["data"]["notification_icon"] == "mdi:washing-machine" and call["data"]["color"] == "#4CAF50"
    # Tapping it opens the dashboard on this wash, named by when it finished.
    end = int(dt_util.parse_datetime("2026-10-14 13:52:00+00:00").timestamp())
    assert call["data"]["url"] == call["data"]["clickAction"] == f"/lovelace/washing-machine?wash={end}"
    assert "live_update" not in call["data"]  # the Live Activity is off unless chosen: one push only

    # An hour after the end it is idle again.
    await advance(hass, freezer, 60)
    assert hass.states.get("sensor.washing_machine_status").state == "idle"


async def test_short_runs_are_ignored(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    pushes = async_mock_service(hass, "notify", "mobile_app_ioannis_iphone")
    freezer.move_to("2026-10-14 12:00:00+00:00")
    await setup(hass, notify_services=["mobile_app_ioannis_iphone"])
    power(hass, 40)
    await advance(hass, freezer, 2)
    power(hass, 2)
    await advance(hass, freezer, 6)
    assert hass.states.get("sensor.washing_machine_status").state == "idle"
    assert hass.states.get("sensor.washing_machine_washes").state == "0"
    assert not pushes


async def test_unavailable_plug_never_ends_a_wash(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-10-14 12:00:00+00:00")
    entry = await setup(hass, notify_services=[])
    power(hass, 500)
    await advance(hass, freezer, 5)
    power(hass, "unavailable")
    await advance(hass, freezer, 30)
    assert entry.runtime_data.tracker.running  # the wash goes on; only the live entities wait
    power(hass, 450)
    await hass.async_block_till_done()
    status = hass.states.get("sensor.washing_machine_status")
    assert status.state == "washing" and status.attributes["started"].startswith("2026-10-14T12:00")


async def test_a_reload_mid_wash_keeps_the_wash(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-10-14 12:00:00+00:00")
    entry = await setup(hass, notify_services=[])
    power(hass, 500)
    await advance(hass, freezer, 30)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    status = hass.states.get("sensor.washing_machine_status")
    assert status.state == "washing"
    assert status.attributes["started"].startswith("2026-10-14T12:00")
    power(hass, 2)
    await advance(hass, freezer, 6)
    assert hass.states.get("sensor.washing_machine_washes").state == "1"
    last = hass.states.get("sensor.washing_machine_last_wash")
    assert last.attributes["minutes"] == 30 and float(last.state) == pytest.approx(0.25)  # 500 W for 30 min


async def test_websocket_gives_the_card_its_log(hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_ws_client) -> None:
    # The real clock (not a fixed date): the test's access token is issued at "now" and must not
    # look like it comes from the future, and the whole test stays under its 30-minute life.
    await setup(hass, notify_services=[])
    power(hass, 500)
    await advance(hass, freezer, 15)
    power(hass, 2)
    await advance(hass, freezer, 6)
    power(hass, 600)
    await hass.async_block_till_done()

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/washes", "entity_id": "sensor.washing_machine_status"})
    msg = await client.receive_json()
    assert msg["success"], msg
    r = msg["result"]
    assert r["title"] == "Washing machine"
    assert r["status"] == "washing"
    assert r["price"] == 0.25 and r["currency"] == "GBP"
    assert r["power_entity_id"] == POWER and "energy_entity_id" not in r
    assert r["energy"] is None  # the plug isn't on an Energy dashboard here
    assert r["entities"]["status"] == "sensor.washing_machine_status"
    assert r["entities"]["washes_today"] == "sensor.washing_machine_washes_today"
    assert r["current"]["kwh"] == 0.0
    assert len(r["washes"]) == 1 and r["washes"][0]["minutes"] == 15 and r["washes"][0]["kwh"] == pytest.approx(0.125, abs=1e-3)
    assert r["first_wash"] == r["washes"][0]["end"]

    start = (dt_util.utcnow() - timedelta(days=30)).isoformat()
    end = (dt_util.utcnow() - timedelta(days=20)).isoformat()
    await client.send_json({"id": 2, "type": f"{DOMAIN}/washes", "entity_id": "sensor.washing_machine_status", "start": start, "end": end})
    assert (await client.receive_json())["result"]["washes"] == []

    await client.send_json({"id": 3, "type": f"{DOMAIN}/washes", "entity_id": POWER})
    assert not (await client.receive_json())["success"]


async def test_energy_settings_price_is_used(hass: HomeAssistant) -> None:
    """No price of its own: the grid price from Settings > Energy (a fixed price or a price entity)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    grid = {"type": "grid", "stat_energy_from": "sensor.meter", "entity_energy_price": None, "number_energy_price": 0.2372}
    manager = MagicMock(data={"energy_sources": [{"type": "solar", "stat_energy_from": "sensor.pv"}, grid]})
    hass.config.components.add("energy")
    with patch("homeassistant.components.energy.data.async_get_manager", AsyncMock(return_value=manager)):
        entry = await setup(hass, notify_services=[], price=None)
        assert await entry.runtime_data.async_price() == pytest.approx(0.2372)
        grid.update(entity_energy_price="sensor.tariff", number_energy_price=None)
        hass.states.async_set("sensor.tariff", "0.31")
        assert await entry.runtime_data.async_price() == pytest.approx(0.31)
        manager.data = {"energy_sources": []}
        assert await entry.runtime_data.async_price() is None


async def test_config_flow_creates_an_entry(hass: HomeAssistant) -> None:
    power(hass, 1)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    assert "energy_entity_id" not in result["data_schema"].schema
    live = next(k for k in result["data_schema"].schema if k == "live_activity")
    assert live.default() is False  # a Labs feature on the phone: chosen, not assumed
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Washing machine", "power_entity_id": POWER, "threshold_w": 10,
         "finish_delay_min": 5, "min_wash_min": 10, "finished_hold_min": 60, "notify_services": [], "notify_title": "Washing done"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Washing machine"
    assert result["options"]["power_entity_id"] == POWER
    await hass.async_block_till_done()

    # The same plug can't be added twice.
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    again = await hass.config_entries.flow.async_configure(
        again["flow_id"],
        {"name": "Again", "power_entity_id": POWER, "threshold_w": 10, "finish_delay_min": 5, "min_wash_min": 10,
         "finished_hold_min": 60, "notify_title": "Washing done"},
    )
    assert again["type"] is FlowResultType.ABORT


async def test_options_flow_changes_settings(hass: HomeAssistant) -> None:
    async_mock_service(hass, "notify", "mobile_app_ioannis_iphone")
    async_mock_service(hass, "notify", "mobile_app_nancy_iphone")
    entry = await setup(hass, notify_services=[])
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**OPTIONS, "threshold_w": 15, "notify_services": ["mobile_app_ioannis_iphone"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.tracker.config.threshold_w == 15


async def test_a_kilowatt_power_sensor(hass: HomeAssistant) -> None:
    await setup(hass, notify_services=[])
    power(hass, 0.008, "kW")  # 8 W: still idle
    await hass.async_block_till_done()
    assert hass.states.get("sensor.washing_machine_status").state == "idle"
    power(hass, 0.15, "kW")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.washing_machine_status").state == "washing"


async def test_an_entry_from_1_1_loses_its_energy_meter(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("UTC")
    power(hass, 1.5)
    entry = MockConfigEntry(
        domain=DOMAIN, title="Washing machine", data={}, version=1, minor_version=1, unique_id=POWER,
        options={**OPTIONS, "energy_entity_id": "sensor.plug_energy"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == 2
    assert entry.options == OPTIONS


async def test_a_reading_that_is_not_a_number_counts_as_unavailable(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-10-14 12:00:00+00:00")
    await setup(hass, notify_services=[])
    power(hass, 600)
    await advance(hass, freezer, 10)
    power(hass, 600)  # the plug repeats its reading: counted up to here
    power(hass, "nan")
    await advance(hass, freezer, 10)
    assert hass.states.get("sensor.washing_machine_status").state == "unavailable"
    power(hass, 2)
    await advance(hass, freezer, 6)
    assert float(hass.states.get("sensor.washing_machine_last_wash").state) == pytest.approx(0.1)


async def test_live_entities_follow_the_plug(hass: HomeAssistant) -> None:
    await setup(hass, notify_services=[])
    power(hass, "unavailable")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.washing_machine_status").state == "unavailable"
    assert hass.states.get("binary_sensor.washing_machine_running").state == "unavailable"
    assert hass.states.get("sensor.washing_machine_washes").state == "0"
    power(hass, 2)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.washing_machine_status").state == "idle"


async def test_a_missing_phone_is_skipped(hass: HomeAssistant, freezer: FrozenDateTimeFactory, caplog) -> None:
    ioannis = async_mock_service(hass, "notify", "mobile_app_ioannis_iphone")
    freezer.move_to("2026-10-14 12:00:00+00:00")
    await setup(hass)  # Nancy's phone isn't registered in this test
    power(hass, 500)
    await advance(hass, freezer, 20)
    power(hass, 2)
    await advance(hass, freezer, 6)
    assert len(ioannis) == 1
    assert "notify.mobile_app_nancy_iphone doesn't exist" in caplog.text


async def test_a_missing_power_sensor_raises_a_repair(hass: HomeAssistant, issue_registry) -> None:
    entry = await setup(hass, notify_services=[])
    issue_id = f"power_entity_missing_{entry.entry_id}"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    hass.states.async_remove(POWER)
    await hass.async_block_till_done()
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.translation_placeholders["entity_id"] == POWER
    power(hass, 3)
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    hass.states.async_remove(POWER)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_washes_today_moves_on_at_midnight(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-10-14 23:20:00+00:00")
    await setup(hass, notify_services=[], finished_hold_min=10)
    power(hass, 500)
    await advance(hass, freezer, 20)
    power(hass, 2)
    await advance(hass, freezer, 6)  # finished 23:40, counted at 23:46
    assert hass.states.get("sensor.washing_machine_washes_today").state == "1"
    await advance(hass, freezer, 20)  # past midnight
    assert hass.states.get("sensor.washing_machine_washes_today").state == "0"
    assert hass.states.get("sensor.washing_machine_washes_last_7_days").state == "1"
    assert hass.states.get("sensor.washing_machine_status").state == "idle"


async def test_finished_survives_a_reload_then_turns_idle(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-10-14 12:00:00+00:00")
    entry = await setup(hass, notify_services=[])
    power(hass, 500)
    await advance(hass, freezer, 20)
    power(hass, 2)
    await advance(hass, freezer, 6)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.washing_machine_status").state == "finished"
    assert hass.states.get("sensor.washing_machine_washes").state == "1"
    await advance(hass, freezer, 60)
    assert hass.states.get("sensor.washing_machine_status").state == "idle"


async def test_time_unloaded_is_not_counted(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-10-14 12:00:00+00:00")
    entry = await setup(hass, notify_services=[])
    power(hass, 500)
    await advance(hass, freezer, 20)
    assert await hass.config_entries.async_unload(entry.entry_id)  # counted up to here
    await advance(hass, freezer, 10)  # the draw meanwhile is unknown
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await advance(hass, freezer, 10)
    power(hass, 2)
    await advance(hass, freezer, 6)
    last = hass.states.get("sensor.washing_machine_last_wash")
    assert last.attributes["minutes"] == 40 and float(last.state) == pytest.approx(500 * 30 / 60 / 1000)


async def test_diagnostics(hass: HomeAssistant) -> None:
    from custom_components.washing_machine_monitor.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = await setup(hass, notify_services=[])
    d = await async_get_config_entry_diagnostics(hass, entry)
    assert d["status"] == "idle" and d["power_available"] is True
    assert d["options"]["power_entity_id"] == POWER and d["price"] == 0.25
    assert d["power"]["state"] == "1.5" and d["latest_washes"] == []


async def test_config_flow_checks_its_input(hass: HomeAssistant) -> None:
    async_mock_service(hass, "notify", "mobile_app_ioannis_iphone")
    hass.states.async_set("sensor.thermometer", "21", {"unit_of_measurement": "°C"})
    base = {"name": "Washing machine", "power_entity_id": "sensor.thermometer", "threshold_w": 10,
            "finish_delay_min": 5, "min_wash_min": 10, "finished_hold_min": 60, "notify_title": "Washing done",
            "notify_services": ["notify.mobile_app_nobody"], "open_path": "lovelace/washing-machine"}
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], base)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"power_entity_id": "not_power", "notify_services": "notify_not_found", "open_path": "bad_path"}
    power(hass, 1)
    fixed = {**base, "power_entity_id": POWER, "notify_services": ["notify.mobile_app_ioannis_iphone"], "open_path": "/lovelace/washing-machine"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], fixed)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["notify_services"] == ["mobile_app_ioannis_iphone"]  # stored without "notify."


# --- The review's findings, each pinned by a test --------------------------------------------


async def test_an_unavailable_plug_during_the_grace_never_ends_or_splits_a_wash(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    pushes = async_mock_service(hass, "notify", "mobile_app_ioannis_iphone")
    freezer.move_to("2026-10-14 12:00:00+00:00")
    await setup(hass, notify_services=["mobile_app_ioannis_iphone"])
    power(hass, 500)
    await advance(hass, freezer, 30)
    power(hass, 3)
    await advance(hass, freezer, 2)
    power(hass, "unavailable")  # the drum spins up again while the plug is off the network
    await advance(hass, freezer, 10)
    assert hass.states.get("sensor.washing_machine_washes").state == "0" and not pushes
    power(hass, 800)  # back: still the same wash
    await advance(hass, freezer, 20)
    power(hass, 2)
    await advance(hass, freezer, 6)
    assert hass.states.get("sensor.washing_machine_washes").state == "1"
    assert hass.states.get("sensor.washing_machine_last_wash").attributes["minutes"] == 62
    assert len(pushes) == 1


async def test_a_finish_that_fell_due_while_down_still_fires_and_pushes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    pushes = async_mock_service(hass, "notify", "mobile_app_ioannis_iphone")
    freezer.move_to("2026-10-14 12:00:00+00:00")
    entry = await setup(hass, notify_services=["mobile_app_ioannis_iphone"])
    power(hass, 500)
    await advance(hass, freezer, 40)
    power(hass, 2)
    await advance(hass, freezer, 1)
    assert await hass.config_entries.async_unload(entry.entry_id)  # Home Assistant goes down
    await advance(hass, freezer, 20)  # the finish fell due meanwhile
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ev = hass.states.get("event.washing_machine_wash")
    assert ev.attributes["event_type"] == "finished" and ev.attributes["minutes"] == 40
    assert hass.states.get("sensor.washing_machine_washes").state == "1"
    assert len(pushes) == 1


async def test_a_price_entity_in_another_energy_unit(hass: HomeAssistant) -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    hass.states.async_set("sensor.spot", "120", {"unit_of_measurement": "EUR/MWh"})
    hass.states.async_set("sensor.per_wh", "0.0003", {"unit_of_measurement": "GBP/Wh"})
    grid = {"type": "grid", "stat_energy_from": "sensor.meter", "entity_energy_price": "sensor.spot", "number_energy_price": None}
    manager = MagicMock(data={"energy_sources": [grid]})
    hass.config.components.add("energy")
    with patch("homeassistant.components.energy.data.async_get_manager", AsyncMock(return_value=manager)):
        entry = await setup(hass, notify_services=[], price=None)
        assert await entry.runtime_data.async_price() == pytest.approx(0.12)
        grid["entity_energy_price"] = "sensor.per_wh"
        assert await entry.runtime_data.async_price() == pytest.approx(0.3)


async def test_the_push_link_can_be_turned_off(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    pushes = async_mock_service(hass, "notify", "mobile_app_ioannis_iphone")
    freezer.move_to("2026-10-14 12:00:00+00:00")
    opts = {k: v for k, v in OPTIONS.items() if k != "open_path"}
    hass.config.currency = "GBP"
    power(hass, 1)
    entry = MockConfigEntry(domain=DOMAIN, title="Washing machine", data={}, options={**opts, "notify_services": ["mobile_app_ioannis_iphone"]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    power(hass, 500)
    await advance(hass, freezer, 20)
    power(hass, 2)
    await advance(hass, freezer, 6)
    assert "url" not in pushes[0].data["data"] and "clickAction" not in pushes[0].data["data"]


async def test_one_failing_phone_does_not_stop_the_others(hass: HomeAssistant, freezer: FrozenDateTimeFactory, caplog) -> None:
    import voluptuous as vol

    strict = async_mock_service(hass, "notify", "mobile_app_ioannis_iphone", schema=vol.Schema({vol.Required("target"): str}))
    nancy = async_mock_service(hass, "notify", "mobile_app_nancy_iphone")
    freezer.move_to("2026-10-14 12:00:00+00:00")
    await setup(hass)
    power(hass, 500)
    await advance(hass, freezer, 20)
    power(hass, 2)
    await advance(hass, freezer, 6)
    assert not strict and len(nancy) == 1
    assert "the push to notify.mobile_app_ioannis_iphone failed" in caplog.text


async def test_a_failed_save_still_updates_and_pushes(hass: HomeAssistant, freezer: FrozenDateTimeFactory, caplog) -> None:
    from unittest.mock import patch

    pushes = async_mock_service(hass, "notify", "mobile_app_ioannis_iphone")
    freezer.move_to("2026-10-14 12:00:00+00:00")
    entry = await setup(hass, notify_services=["mobile_app_ioannis_iphone"])
    power(hass, 500)
    await advance(hass, freezer, 20)
    power(hass, 2)
    with patch.object(entry.runtime_data._store, "async_save", side_effect=OSError("disk full")):
        await advance(hass, freezer, 6)
    assert hass.states.get("sensor.washing_machine_washes").state == "1" and len(pushes) == 1
    assert "could not save the wash log" in caplog.text


async def test_entities_join_the_plugs_device_and_follow_a_rename(hass: HomeAssistant, device_registry, entity_registry) -> None:
    plug_entry = MockConfigEntry(domain="mqtt")
    plug_entry.add_to_hass(hass)
    plug = device_registry.async_get_or_create(config_entry_id=plug_entry.entry_id, identifiers={("mqtt", "plug")}, name="Washing machine")
    reg = entity_registry.async_get_or_create("sensor", "mqtt", "plug_power", device_id=plug.id, config_entry=plug_entry, suggested_object_id="plug_power")
    assert reg.entity_id == POWER
    hass.config.currency = "GBP"
    power(hass, 1)
    entry = MockConfigEntry(domain=DOMAIN, title="Washer", data={}, options={**OPTIONS, "notify_services": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # Named after the plug's device, and shown on it.
    status = entity_registry.async_get("sensor.washing_machine_status")
    assert status is not None and status.device_id == plug.id
    assert not dr_devices_of(device_registry, entry.entry_id)  # no device of our own

    entity_registry.async_update_entity(POWER, new_entity_id="sensor.plug_power_renamed")
    await hass.async_block_till_done()
    assert entry.options["power_entity_id"] == "sensor.plug_power_renamed"


def dr_devices_of(device_registry, entry_id: str) -> list:
    from homeassistant.helpers import device_registry as dr

    return dr.async_entries_for_config_entry(device_registry, entry_id)


async def test_a_disabled_power_sensor_says_so(hass: HomeAssistant, issue_registry, entity_registry) -> None:
    entity_registry.async_get_or_create("sensor", "mqtt", "plug_power", suggested_object_id="plug_power")
    entry = await setup(hass, notify_services=[])
    entity_registry.async_update_entity(POWER, disabled_by=er_disabled_user())
    hass.states.async_remove(POWER)
    await hass.async_block_till_done()
    issue = issue_registry.async_get_issue(DOMAIN, f"power_entity_missing_{entry.entry_id}")
    assert issue is not None and issue.translation_key == "power_entity_disabled"
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, f"power_entity_missing_{entry.entry_id}") is None


def er_disabled_user():
    from homeassistant.helpers import entity_registry as er

    return er.RegistryEntryDisabler.USER


async def test_websocket_takes_local_date_times(hass: HomeAssistant, hass_ws_client) -> None:
    await setup(hass, notify_services=[])
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/washes", "entity_id": "sensor.washing_machine_status", "start": "2026-10-01T00:00:00", "end": "2026-10-08T00:00:00"})
    msg = await client.receive_json()
    assert msg["success"] and msg["result"]["washes"] == []
    await client.send_json({"id": 2, "type": f"{DOMAIN}/washes", "entity_id": "sensor.washing_machine_status", "start": "yesterday"})
    msg = await client.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "invalid_format"


async def test_options_can_clear_optional_fields(hass: HomeAssistant) -> None:
    async_mock_service(hass, "notify", "mobile_app_ioannis_iphone")
    async_mock_service(hass, "notify", "mobile_app_nancy_iphone")
    entry = await setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    cleared = {k: v for k, v in OPTIONS.items() if k not in ("price", "open_path")}
    result = await hass.config_entries.options.async_configure(result["flow_id"], cleared)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert "price" not in entry.options and "open_path" not in entry.options
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**cleared, "notify_title": "  "})
    assert result["errors"] == {"notify_title": "title_required"}


# --- The second review's findings ---------------------------------------------------------------


async def test_an_unreadable_log_fails_setup_and_is_left_untouched(hass: HomeAssistant, hass_storage) -> None:
    from homeassistant.config_entries import ConfigEntryState

    hass.config.currency = "GBP"
    power(hass, 1)
    entry = MockConfigEntry(domain=DOMAIN, title="Washing machine", data={}, options={**OPTIONS, "notify_services": []})
    key = f"{DOMAIN}.{entry.entry_id}"
    good = [{"start": "2026-10-01T10:00:00+00:00", "end": "2026-10-01T11:00:00+00:00", "minutes": 60, "kwh": 0.5, "cost": 0.1}] * 50
    stored = {"version": 1, "minor_version": 1, "key": key, "data": {"washes": [*good, {"start": "nonsense"}], "total_washes": 51}}
    hass_storage[key] = stored
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert hass_storage[key] == stored  # not one wash lost


async def test_a_log_from_a_newer_version_is_left_untouched(hass: HomeAssistant, hass_storage) -> None:
    hass.config.currency = "GBP"
    power(hass, 1)
    entry = MockConfigEntry(domain=DOMAIN, title="Washing machine", data={}, options={**OPTIONS, "notify_services": []})
    key = f"{DOMAIN}.{entry.entry_id}"
    stored = {"version": 2, "minor_version": 1, "key": key, "data": {"washes": [], "total_washes": 300}}
    hass_storage[key] = stored
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert hass_storage[key] == stored


async def test_a_plug_that_stays_offline_ends_the_wash_without_a_push(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    pushes = async_mock_service(hass, "notify", "mobile_app_ioannis_iphone")
    freezer.move_to("2026-10-14 12:00:00+00:00")
    await setup(hass, notify_services=["mobile_app_ioannis_iphone"])
    power(hass, 500)
    await advance(hass, freezer, 50)
    power(hass, "unavailable")
    await advance(hass, freezer, 6 * 60 + 1)
    assert hass.states.get("sensor.washing_machine_washes").state == "1"
    assert hass.states.get("sensor.washing_machine_last_wash").attributes["minutes"] == 50
    assert not pushes


async def test_the_config_flow_wants_a_name(hass: HomeAssistant) -> None:
    power(hass, 1)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "  ", "power_entity_id": POWER, "threshold_w": 10, "finish_delay_min": 5, "min_wash_min": 10,
         "finished_hold_min": 60, "notify_title": "Washing done"},
    )
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"name": "name_required"}


# --- The plug's energy per day, from the Energy dashboard ---------------------------------------

ENERGY_TODAY = "sensor.plug_energy_today"


def board(hass: HomeAssistant, devices: list[dict]):
    """Settings > Energy with these individual devices."""
    from unittest.mock import AsyncMock, MagicMock, patch

    hass.config.components.add("energy")
    manager = MagicMock(data={"energy_sources": [], "device_consumption": devices})
    return patch("homeassistant.components.energy.data.async_get_manager", AsyncMock(return_value=manager))


async def test_the_plug_is_found_on_the_energy_dashboard_by_its_device(hass: HomeAssistant, entity_registry) -> None:
    from homeassistant.helpers import device_registry as dr

    from custom_components.washing_machine_monitor.daily_energy import board_statistic

    plug = MockConfigEntry(domain="mqtt")
    plug.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=plug.entry_id, identifiers={("mqtt", "plug")})
    entity_registry.async_get_or_create("sensor", "mqtt", "power", device_id=device.id, suggested_object_id="plug_power")
    entity_registry.async_get_or_create("sensor", "mqtt", "energy", device_id=device.id, suggested_object_id="plug_energy_today")
    found = {"device_consumption": [{"stat_consumption": "sensor.fridge_energy"}, {"stat_consumption": ENERGY_TODAY}]}
    assert board_statistic(hass, found, POWER) == ENERGY_TODAY
    assert board_statistic(hass, {"device_consumption": [{"stat_consumption": "sensor.fridge_energy"}]}, POWER) is None
    assert board_statistic(hass, {}, "sensor.unknown_power") is None


async def test_today_counts_the_meters_reading_since_the_last_statistics(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """A new meter reading shows at once, not after the next 5-minute statistics; a daily counter that
    reset at midnight counts from zero."""
    from datetime import date
    from unittest.mock import MagicMock, patch

    from custom_components.washing_machine_monitor import daily_energy

    freezer.move_to("2026-10-14 15:30:00+00:00")
    await hass.config.async_set_time_zone("UTC")
    recorder = MagicMock()
    recorder.async_add_executor_job = lambda fn, *args: hass.async_add_executor_job(fn, *args)
    anchor = {"value": 0.20}

    def during(hass_, start, end, ids, period, units, types):
        assert period == "5minute" and types == {"state"} and units == {"energy": "kWh"}
        return {ENERGY_TODAY: [{"start": 0.0, "state": anchor["value"]}]}

    def so_far(hass_, start, end, statistic, types, units):
        assert types == {"change"} and units == {"energy": "kWh"}
        return {"change": 0.20}

    with (
        patch.object(daily_energy, "get_instance", return_value=recorder),
        patch.object(daily_energy, "statistics_during_period", during),
        patch.object(daily_energy, "statistic_during_period", so_far),
    ):
        hass.states.async_set(ENERGY_TODAY, "260", {"unit_of_measurement": "Wh"})
        days = await daily_energy.async_energy_per_day(hass, ENERGY_TODAY, date(2026, 10, 14), date(2026, 10, 14))
        assert days == {"2026-10-14": pytest.approx(0.26)}
        anchor["value"] = 0.9  # yesterday's last reading; the counter reset at midnight
        hass.states.async_set(ENERGY_TODAY, "0.05", {"unit_of_measurement": "kWh"})
        days = await daily_energy.async_energy_per_day(hass, ENERGY_TODAY, date(2026, 10, 14), date(2026, 10, 14))
        assert days == {"2026-10-14": pytest.approx(0.25)}
        hass.states.async_set(ENERGY_TODAY, "unavailable")
        days = await daily_energy.async_energy_per_day(hass, ENERGY_TODAY, date(2026, 10, 14), date(2026, 10, 14))
        assert days == {"2026-10-14": pytest.approx(0.20)}


async def test_no_energy_dashboard_entry_means_no_day_totals(hass: HomeAssistant) -> None:
    from datetime import date

    with board(hass, [{"stat_consumption": "sensor.fridge_energy"}]):
        entry = await setup(hass, notify_services=[])
        hass.config.components.add("recorder")
        assert await entry.runtime_data.async_daily_energy(date(2026, 10, 1), date(2026, 10, 7)) is None


# --- the Live Activity -------------------------------------------------------------------------

LIVE_OPTIONS = {"live_activity": True, "notify_services": ["mobile_app_ioannis_iphone", "mobile_app_nancy_iphone", "telegram_home"]}


def phones(hass: HomeAssistant) -> dict[str, list]:
    return {n: async_mock_service(hass, "notify", n) for n in ("mobile_app_ioannis_iphone", "mobile_app_nancy_iphone", "telegram_home")}


def live(calls: list) -> list[str]:
    """What a phone was sent, in order: washing, done, end, or push."""
    out = []
    for c in calls:
        d = c.data.get("data", {})
        if c.data.get("message") == "clear_notification":
            out.append("end")
        elif d.get("live_update"):
            out.append("done" if d.get("silent") else "washing")
        else:
            out.append("push")
    return out


async def test_the_live_activity_follows_a_wash(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    sent = phones(hass)
    freezer.move_to("2026-10-15 10:15:20+00:00")
    entry = await setup(hass, **LIVE_OPTIONS)
    power(hass, 19)  # the door locks: a wash starts
    await advance(hass, freezer, 1.5)
    assert all(not c for c in sent.values())  # not yet: a false start is over by now
    await advance(hass, freezer, 0.6)  # 2 minutes in, drawing power with no new reading
    start = sent["mobile_app_ioannis_iphone"][0].data
    assert start["title"] == "Washing" and start["message"] == "Started 10:15"
    assert start["data"]["when"] == int(dt_util.parse_datetime("2026-10-15 10:15:20+00:00").timestamp())
    assert start["data"]["tag"] == f"washing_machine_monitor_{entry.entry_id}_live"
    assert not sent["telegram_home"]  # only the companion app has Live Activities

    power(hass, 2100)
    await advance(hass, freezer, 60)
    power(hass, 2)  # the drum stops; finished after the delay
    await advance(hass, freezer, 5.1)
    for n in ("mobile_app_ioannis_iphone", "mobile_app_nancy_iphone"):
        assert live(sent[n]) == ["washing", "push", "done"]
    assert live(sent["telegram_home"]) == ["push"]
    done = sent["mobile_app_nancy_iphone"][2].data
    assert done["title"] == "Washing done" and done["data"]["critical_text"] == "11:17"
    assert entry.runtime_data.live == "done"

    await advance(hass, freezer, 180)  # Finished is long over: green until someone opens the wash
    assert hass.states.get("sensor.washing_machine_status").state == "idle"
    assert live(sent["mobile_app_ioannis_iphone"]) == ["washing", "push", "done"]
    assert entry.runtime_data.live == "done" and live(sent["telegram_home"]) == ["push"]


async def test_no_live_activity_for_a_false_start(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    sent = phones(hass)
    freezer.move_to("2026-10-15 10:00:00+00:00")
    await setup(hass, **LIVE_OPTIONS)
    power(hass, 19)  # a door lock for 15 seconds
    await advance(hass, freezer, 0.25)
    power(hass, 4.5)
    await advance(hass, freezer, 10)
    assert all(not c for c in sent.values())


async def test_a_short_run_ends_its_live_activity(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    sent = phones(hass)
    freezer.move_to("2026-10-15 10:00:00+00:00")
    await setup(hass, **LIVE_OPTIONS)
    power(hass, 150)
    await advance(hass, freezer, 3)  # started, then the programme is cancelled
    power(hass, 2)
    await advance(hass, freezer, 6)
    assert live(sent["mobile_app_ioannis_iphone"]) == ["washing", "end"]  # not counted: no push, gone


async def test_the_next_wash_ends_the_last_ones_live_activity(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    sent = phones(hass)
    freezer.move_to("2026-10-15 10:00:00+00:00")
    await setup(hass, **LIVE_OPTIONS)
    power(hass, 150)
    await advance(hass, freezer, 30)
    power(hass, 2)
    await advance(hass, freezer, 6)
    assert live(sent["mobile_app_ioannis_iphone"]) == ["washing", "push", "done"]
    power(hass, 150)  # the next load, within the hour of Finished
    await hass.async_block_till_done()
    assert live(sent["mobile_app_ioannis_iphone"]) == ["washing", "push", "done", "end"]
    await advance(hass, freezer, 2.1)
    assert live(sent["mobile_app_ioannis_iphone"]) == ["washing", "push", "done", "end", "washing"]


async def test_a_restart_mid_wash_keeps_the_live_activity_quiet(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    sent = phones(hass)
    freezer.move_to("2026-10-15 10:00:00+00:00")
    entry = await setup(hass, **LIVE_OPTIONS)
    power(hass, 150)
    await advance(hass, freezer, 5)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    power(hass, 160)
    await advance(hass, freezer, 5)
    assert live(sent["mobile_app_ioannis_iphone"]) == ["washing"]  # started once, not again
    power(hass, 2)
    await advance(hass, freezer, 6)
    assert live(sent["mobile_app_ioannis_iphone"]) == ["washing", "push", "done"]


async def test_turning_the_live_activity_off_ends_it(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    sent = phones(hass)
    freezer.move_to("2026-10-15 10:00:00+00:00")
    entry = await setup(hass, **LIVE_OPTIONS)
    power(hass, 150)
    await advance(hass, freezer, 3)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**OPTIONS, **LIVE_OPTIONS, "live_activity": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert live(sent["mobile_app_ioannis_iphone"]) == ["washing", "end"]
    power(hass, 160)  # the wash carries on, and still gets its push
    await advance(hass, freezer, 20)
    power(hass, 2)
    await advance(hass, freezer, 6)
    assert live(sent["mobile_app_ioannis_iphone"]) == ["washing", "end", "push"]


async def test_deleting_the_washing_machine_ends_its_live_activity(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    sent = phones(hass)
    freezer.move_to("2026-10-15 10:00:00+00:00")
    entry = await setup(hass, **LIVE_OPTIONS)
    power(hass, 150)
    await advance(hass, freezer, 3)
    await entry.runtime_data._store.async_save(entry.runtime_data._data())  # as the delayed save would
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert live(sent["mobile_app_ioannis_iphone"]) == ["washing", "end"]


async def test_opening_the_wash_ends_its_live_activity(hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_ws_client) -> None:
    """Tapping the push or the Live Activity opens the wash on the card, which says so: only then
    does the Live Activity go, from every phone at once. An old push opened later never ends a
    newer wash's."""
    # The real clock (not a fixed date), and the card connects within the test access token's
    # 30-minute life: a 12-minute wash, then the finish delay.
    sent = phones(hass)
    entry = await setup(hass, **LIVE_OPTIONS)
    power(hass, 150)
    await advance(hass, freezer, 12)
    power(hass, 2)
    await advance(hass, freezer, 6)
    assert live(sent["mobile_app_ioannis_iphone"]) == ["washing", "push", "done"]
    wash = int(entry.runtime_data.last_wash.end.timestamp())
    client = await hass_ws_client(hass)

    async def seen(msg_id: int, wash_id: int) -> dict:
        await client.send_json({"id": msg_id, "type": f"{DOMAIN}/wash_seen", "entity_id": "sensor.washing_machine_status", "wash": wash_id})
        reply = await client.receive_json()
        assert reply["success"]
        await hass.async_block_till_done()
        return reply["result"]

    assert await seen(1, wash - 3600) == {"ended": False}  # another wash: left alone
    assert live(sent["mobile_app_ioannis_iphone"]) == ["washing", "push", "done"]
    assert await seen(2, wash) == {"ended": True}
    for name in ("mobile_app_ioannis_iphone", "mobile_app_nancy_iphone"):
        assert live(sent[name]) == ["washing", "push", "done", "end"]
    assert entry.runtime_data.live is None
    assert hass.states.get("sensor.washing_machine_status").state == "finished"  # the card still shows it
    assert await seen(3, wash) == {"ended": False}  # already gone
    await client.send_json({"id": 4, "type": f"{DOMAIN}/wash_seen", "entity_id": "sensor.nothing", "wash": wash})
    assert (await client.receive_json())["error"]["code"] == "not_found"
    await advance(hass, freezer, 60)  # Finished ends: nothing more is sent
    assert live(sent["mobile_app_ioannis_iphone"]) == ["washing", "push", "done", "end"]
