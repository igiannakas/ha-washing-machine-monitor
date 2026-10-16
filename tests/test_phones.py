"""What the phones get: the push and the Live Activity payloads. No Home Assistant needed."""
from __future__ import annotations

import importlib
import sys
import types
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

# phones.py imports the tracker relatively: load both as a package of their own, without the
# integration's __init__ (which needs Home Assistant).
_PKG = Path(__file__).parent.parent / "custom_components" / "washing_machine_monitor"
_pkg = types.ModuleType("wm_phones_pkg")
_pkg.__path__ = [str(_PKG)]
sys.modules["wm_phones_pkg"] = _pkg
phones = importlib.import_module("wm_phones_pkg.phones")
tracker = importlib.import_module("wm_phones_pkg.tracker")

LONDON = timezone(timedelta(hours=1))  # BST
NB = " "
WASH = tracker.Wash(
    start=datetime(2026, 10, 15, 10, 15, 20, tzinfo=UTC),
    end=datetime(2026, 10, 15, 12, 21, 55, 664979, tzinfo=UTC),
    kwh=0.327,
    cost=0.0776,
)
END = int(WASH.end.timestamp())  # 1792066915: the wash's name in the link


def test_the_push_looks_like_the_machine_and_says_what_to_do() -> None:
    p = phones.finished_push(WASH, title="Washing done", tz=LONDON, currency="GBP", path="/lovelace/washing-machine",
                             tag="washing_machine_monitor_E1", thread="washing_machine_monitor")
    assert p["title"] == "Washing done"
    assert p["message"] == f"Finished at 13:21 after 2{NB}h{NB}7{NB}min · 0.33{NB}kWh · 8p"
    assert p["data"] == {
        "tag": "washing_machine_monitor_E1",
        "subtitle": "Unload the clothes",
        "notification_icon": "mdi:washing-machine",
        "notification_icon_color": "#FFFFFF",
        "color": "#4CAF50",
        "push": {"thread-id": "washing_machine_monitor"},
        "url": f"/lovelace/washing-machine?wash={END}",
        "clickAction": f"/lovelace/washing-machine?wash={END}",
    }


def test_no_path_no_link() -> None:
    p = phones.finished_push(WASH, title="Done", tz=LONDON, currency="GBP", path=None, tag="t", thread="x")
    assert "url" not in p["data"] and "clickAction" not in p["data"]


def test_the_link_names_the_wash_by_its_end() -> None:
    assert phones.wash_link("/lovelace/washing-machine", WASH) == f"/lovelace/washing-machine?wash={END}"
    assert phones.wash_link("/lovelace/0?edit=1", WASH) == f"/lovelace/0?edit=1&wash={END}"
    assert phones.wash_link("/lovelace/0#laundry", WASH) == f"/lovelace/0?wash={END}#laundry"
    assert END == 1792066915  # whole seconds, as the card compares them


def test_the_live_activity_counts_up_from_the_start() -> None:
    """Washing machine on, the phone's own timer counting up, since 11:15 on the right: nothing to
    re-send while the wash runs."""
    p = phones.live_washing(WASH.start, tz=LONDON, path="/lovelace/washing-machine", tag="washing_machine_monitor_E1_live")
    assert p["title"] == "Washing machine on"
    assert p["message"] == "Washing since 11:15"  # Android shows it beside the timer; on an iPhone the timer is the line
    assert p["data"] == {
        "tag": "washing_machine_monitor_E1_live",
        "live_update": True,
        "chronometer": True,
        "when": int(WASH.start.timestamp()),  # in the past: the app counts up from it
        "when_relative": False,
        "critical_text": "since 11:15",
        "notification_icon": "mdi:washing-machine",
        "notification_icon_color": "#FF9800",
        "url": "/lovelace/washing-machine",
        "clickAction": "/lovelace/washing-machine",
    }


def test_done_is_quiet_and_green_and_links_to_the_wash() -> None:
    p = phones.live_done(WASH, tz=LONDON, path="/lovelace/washing-machine", tag="T")
    # An iPhone keeps the title the Live Activity started with: the line says the wash is done.
    assert p["title"] == "Washing machine on"
    assert p["message"] == f"Done after 2{NB}h{NB}7{NB}min · Unload the clothes"
    d = p["data"]
    assert d["live_update"] and d["silent"] and d["alert_once"]  # the push is the alert
    assert d["chronometer"] is False and d["critical_text"] == "13:21"
    assert d["notification_icon_color"] == "#4CAF50" and d["tag"] == "T"
    assert d["url"] == f"/lovelace/washing-machine?wash={END}"


def test_ending_it() -> None:
    assert phones.live_end("T") == {"message": "clear_notification", "data": {"tag": "T"}}


def test_only_the_companion_app_has_live_activities() -> None:
    assert phones.is_phone("mobile_app_nancy_iphone") and phones.is_phone("notify.mobile_app_nancy_iphone")
    assert not phones.is_phone("telegram_home") and not phones.is_phone("persistent_notification")
