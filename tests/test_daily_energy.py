"""The plug's energy per day from the Energy dashboard's statistics, on a real recorder.

Kept apart from test_integration.py: the recorder has to be set up before Home Assistant is.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from freezegun.api import FrozenDateTimeFactory  # noqa: E402
from homeassistant.components.recorder import Recorder  # noqa: E402
from homeassistant.components.recorder.models import StatisticMeanType  # noqa: E402
from homeassistant.components.recorder.statistics import async_import_statistics  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402
from pytest_homeassistant_custom_component.components.recorder.common import (  # noqa: E402
    async_wait_recording_done,
)

DOMAIN = "washing_machine_monitor"
POWER = "sensor.plug_power"
ENERGY_TODAY = "sensor.plug_energy_today"


@pytest.fixture
async def mock_recorder_before_hass(async_setup_recorder_instance) -> None:
    """The recorder before Home Assistant."""


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


async def test_days_energy_comes_from_the_energy_dashboards_statistics(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The daily change of the plug's statistic on the Energy dashboard, as the dashboard shows it."""
    from unittest.mock import AsyncMock, MagicMock, patch

    freezer.move_to("2026-10-14 15:30:00+00:00")
    await hass.config.async_set_time_zone("UTC")
    hass.states.async_set(POWER, "1.5", {"unit_of_measurement": "W", "device_class": "power"})
    hass.config.components.add("energy")
    devices = [{"stat_consumption": "sensor.other_energy"}, {"stat_consumption": ENERGY_TODAY, "stat_rate": POWER}]
    manager = MagicMock(data={"energy_sources": [], "device_consumption": devices})
    with patch("homeassistant.components.energy.data.async_get_manager", AsyncMock(return_value=manager)):
        entry = MockConfigEntry(domain=DOMAIN, title="Washing machine", data={},
                                options={"power_entity_id": POWER, "threshold_w": 10, "finish_delay_min": 5, "min_wash_min": 10,
                                         "finished_hold_min": 60, "notify_services": [], "notify_title": "Washing done"})
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        # Hourly statistics from midnight on the 12th: 0.02 kWh an hour that day, then 0.01 an hour.
        rows, total = [], 0.0
        for hour in range(2 * 24 + 14):
            total += 0.02 if hour < 24 else 0.01
            rows.append({"start": datetime(2026, 10, 12, tzinfo=dt_util.UTC) + timedelta(hours=hour), "state": total, "sum": total})
        meta = {"mean_type": StatisticMeanType.NONE, "has_sum": True, "name": None, "source": "recorder",
                "statistic_id": ENERGY_TODAY, "unit_class": "energy", "unit_of_measurement": "kWh"}
        async_import_statistics(hass, meta, rows)
        await async_wait_recording_done(hass)

        energy = await entry.runtime_data.async_daily_energy(date(2026, 10, 11), date(2026, 10, 16))
    assert energy["statistic"] == ENERGY_TODAY
    days = energy["days"]
    assert list(days) == ["2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14"]  # never past today
    assert days["2026-10-11"] == 0.0  # no statistics yet: nothing used
    assert days["2026-10-12"] == pytest.approx(0.48)
    assert days["2026-10-13"] == pytest.approx(0.24)
    assert days["2026-10-14"] == pytest.approx(0.14)
