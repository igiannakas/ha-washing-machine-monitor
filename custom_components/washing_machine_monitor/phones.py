"""What the phones get: the "washing done" push, and a Live Activity while a wash runs.

No Home Assistant imports: the payloads are built here and tested directly. The keys are the Home
Assistant companion app's; Android reads the ones it has and ignores the rest.

The push shows the washing machine's icon on green in place of the app's logo, the title, "Unload
the clothes" under it, and the wash in numbers. Tapping it opens the dashboard path with the wash in
it (?wash=<when it finished, in Unix seconds>), and the card there opens that wash.

The Live Activity (iPhone, iOS 17.2 and later; a Live Update on Android 16): once a wash has run for
LIVE_AFTER with the machine drawing power, the lock screen shows "Washing machine on", a timer
counting up from the start (the phone keeps it going: no update is sent for it) and "since 11:31" on
the right. At the end it turns green and says it's done and what to do, quietly: the push is the
alert. It stays until someone opens the wash by tapping the push or it (the card tells the
integration), then goes from every phone; the next wash starting replaces it. Only the companion
app's notify actions (mobile_app_*) get it; any other notify action would show every update as a
message.

On an iPhone the timer takes the message line's place, and the title is set once, when the Live
Activity starts: the green one keeps LIVE_TITLE, so its line says the wash is done.
"""
from __future__ import annotations

from datetime import datetime, timedelta, tzinfo
from typing import Any

from .tracker import Wash, finished_message, format_duration

ICON = "mdi:washing-machine"
GREEN = "#4CAF50"  # finished, as on the card
ORANGE = "#FF9800"  # washing, as on the card
WHITE = "#FFFFFF"
UNLOAD = "Unload the clothes"
LIVE_TITLE = "Washing machine on"  # the Live Activity's title, from its start to the end
LINK_KEY = "wash"  # the card reads it from the address the push opens
# A false start (a door lock, the machine's switch-on surge) is over within seconds; a wash is still
# drawing power after this, so its Live Activity never comes and goes for nothing.
LIVE_AFTER = timedelta(minutes=2)


def is_phone(service: str) -> bool:
    """A companion app's notify action (mobile_app_<device>): the only kind that has Live Activities."""
    return service.removeprefix("notify.").startswith("mobile_app_")


def wash_id(wash: Wash) -> int:
    """A wash's name in links: when it finished, in whole Unix seconds (unique within a log)."""
    return int(wash.end.timestamp())


def wash_link(path: str, wash: Wash) -> str:
    """The dashboard path with the wash in it: /lovelace/washing-machine?wash=1792066915."""
    base, hash_mark, fragment = path.partition("#")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{LINK_KEY}={wash_id(wash)}{hash_mark}{fragment}"


def _link(data: dict[str, Any], url: str | None) -> None:
    if url:
        data["url"] = url  # iPhone
        data["clickAction"] = url  # Android


def finished_push(
    wash: Wash, *, title: str, tz: tzinfo, currency: str | None, path: str | None, tag: str, thread: str
) -> dict[str, Any]:
    """Washing done / Unload the clothes / Finished at 14:20 after 1 h 52 min · 0.84 kWh · 20p."""
    data: dict[str, Any] = {
        "tag": tag,
        "subtitle": UNLOAD,
        "notification_icon": ICON,
        "notification_icon_color": WHITE,
        "color": GREEN,
        "push": {"thread-id": thread},
    }
    _link(data, wash_link(path, wash) if path else None)
    return {"title": title, "message": finished_message(wash, tz, currency), "data": data}


def live_washing(started: datetime, *, tz: tzinfo, path: str | None, tag: str) -> dict[str, Any]:
    """Washing machine on / a timer counting up from the start (one in the past counts up) / since
    11:31 on the right. The message is for Android, which shows it beside the timer."""
    since = f"{started.astimezone(tz):%H:%M}"
    data: dict[str, Any] = {
        "tag": tag,
        "live_update": True,
        "chronometer": True,
        "when": int(started.timestamp()),
        "when_relative": False,
        "critical_text": f"since {since}",
        "notification_icon": ICON,
        "notification_icon_color": ORANGE,
    }
    _link(data, path)
    return {"title": LIVE_TITLE, "message": f"Washing since {since}", "data": data}


def live_done(wash: Wash, *, tz: tzinfo, path: str | None, tag: str) -> dict[str, Any]:
    """Washing machine on / Done after 1 h 52 min · Unload the clothes, the finish time on the right.
    No alert of its own (silent on iPhone, alert_once on Android): the push that goes with it is the
    alert."""
    data: dict[str, Any] = {
        "tag": tag,
        "live_update": True,
        "silent": True,
        "alert_once": True,
        "chronometer": False,
        "critical_text": f"{wash.end.astimezone(tz):%H:%M}",
        "notification_icon": ICON,
        "notification_icon_color": GREEN,
    }
    _link(data, wash_link(path, wash) if path else None)
    return {"title": LIVE_TITLE, "message": f"Done after {format_duration(wash.minutes)} · {UNLOAD}", "data": data}


def live_end(tag: str) -> dict[str, Any]:
    """Ends the Live Activity (and clears any notification with its tag)."""
    return {"message": "clear_notification", "data": {"tag": tag}}
