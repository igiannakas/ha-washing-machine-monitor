"""Constants for Washing Machine Monitor."""
from __future__ import annotations

from datetime import timedelta

from .tracker import FINISH_DELAY, MIN_WASH, THRESHOLD_W

DOMAIN = "washing_machine_monitor"

CONF_POWER = "power_entity_id"
CONF_ENERGY = "energy_entity_id"  # 1.1 and earlier: an energy meter; removed by the migration
CONF_THRESHOLD = "threshold_w"
CONF_FINISH_DELAY = "finish_delay_min"
CONF_MIN_WASH = "min_wash_min"
CONF_FINISHED_HOLD = "finished_hold_min"
CONF_NOTIFY = "notify_services"
CONF_NOTIFY_TITLE = "notify_title"
CONF_OPEN_PATH = "open_path"
CONF_PRICE = "price"
CONF_LIVE_ACTIVITY = "live_activity"

DEFAULT_NAME = "Washing machine"
# The detection defaults live with the detector (tracker.py), with how they were chosen.
DEFAULT_THRESHOLD = THRESHOLD_W
DEFAULT_FINISH_DELAY = FINISH_DELAY // timedelta(minutes=1)
DEFAULT_MIN_WASH = MIN_WASH // timedelta(minutes=1)
DEFAULT_FINISHED_HOLD = 60
DEFAULT_NOTIFY_TITLE = "Washing done"
DEFAULT_OPEN_PATH = "/lovelace/washing-machine"
DEFAULT_LIVE_ACTIVITY = False  # a Home Assistant Labs feature on the phone: chosen, not assumed

EVENT_FINISHED = "finished"

STORAGE_VERSION = 1
SAVE_DELAY_S = 10
