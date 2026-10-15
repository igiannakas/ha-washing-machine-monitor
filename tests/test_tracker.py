"""The wash detector, the log helpers and the push wording. No Home Assistant needed."""
from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

_PATH = Path(__file__).parent.parent / "custom_components" / "washing_machine_monitor" / "tracker.py"
_spec = importlib.util.spec_from_file_location("wm_tracker", _PATH)
tracker = importlib.util.module_from_spec(_spec)
sys.modules["wm_tracker"] = tracker
_spec.loader.exec_module(tracker)

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
LONDON = timezone(timedelta(hours=1))  # BST


# The mechanics are tested at fixed settings, so retuning the defaults doesn't move them. The
# defaults themselves are tested on a real day's readings (test_the_defaults_on_a_real_day).
CFG = tracker.TrackerConfig(threshold_w=10.0, finish_delay=timedelta(minutes=5), min_wash=timedelta(minutes=10))


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def run(readings, price=0.2372, config=None):
    """Feed (minute, watts) readings; check for a finish after each and at the end."""
    t = tracker.WashTracker(config=config or CFG)
    results, starts = [], []
    for minute, watts in readings:
        due = t.finish_due()
        if due is not None and due <= at(minute):
            r = t.check(due, price)
            if r:
                results.append(r)
        if t.power(at(minute), watts):
            starts.append(minute)
    due = t.finish_due()
    if due is not None:
        r = t.check(due, price)
        if r:
            results.append(r)
    return t, starts, results


def test_a_typical_wash_with_pauses_is_one_wash():
    # Fill, heat, tumble with 1–3 minute pauses under 10 W, spin, then standby at 2 W.
    readings = [(0, 1.5), (1, 150), (3, 4), (5, 2100), (25, 120), (27, 5), (29, 140), (60, 3), (63, 160),
                (100, 450), (110, 2), (130, 1)]
    t, starts, results = run(readings)
    assert starts == [1]
    assert len(results) == 1 and results[0].counted
    wash = results[0].wash
    assert wash.start == at(1)
    assert wash.end == at(110)  # the last time it fell under 10 W, not when the delay ran out
    assert wash.minutes == 109
    assert not t.running
    # Every reading from the start to the end, the pauses included, held until the next one.
    watt_minutes = 150 * 2 + 4 * 2 + 2100 * 20 + 120 * 2 + 5 * 2 + 140 * 31 + 3 * 3 + 160 * 37 + 450 * 10
    assert wash.kwh == pytest.approx(watt_minutes / 60 / 1000)
    assert wash.cost == pytest.approx(wash.kwh * 0.2372)


def test_finish_waits_for_the_full_delay():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 750)
    t.power(at(20), 3)
    assert t.finish_due() == at(25)
    assert t.check(at(24.9), 0.25) is None
    assert t.running
    r = t.check(at(25), 0.25)
    assert r is not None and r.counted
    assert r.wash.kwh == pytest.approx(0.25) and r.wash.cost == pytest.approx(0.0625)


def test_draw_coming_back_cancels_the_pending_finish():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 200)
    t.power(at(10), 3)
    assert t.finish_due() == at(15)
    t.power(at(14), 300)
    assert t.finish_due() is None
    assert t.check(at(16), None) is None
    assert t.running


def test_short_runs_are_not_counted():
    _, starts, results = run([(0, 1), (1, 40), (3, 2), (20, 1)])
    assert starts == [1]
    assert len(results) == 1 and not results[0].counted  # 2 minutes < 10 minute minimum


def real_day(name: str) -> tuple[datetime, list[tuple[datetime, float | None]]]:
    """A day's power readings from tests/fixtures: (when, watts), None where there was no reading."""
    t0 = datetime(2026, 10, 14, 13, 31, 33, tzinfo=UTC)
    readings = []
    for line in (Path(__file__).parent / "fixtures" / name).read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        seconds, value = line.split(",")
        try:
            watts = float(value)
        except ValueError:
            watts = None
        readings.append((t0 + timedelta(seconds=float(seconds)), watts))
    return t0, readings


def test_the_defaults_on_a_real_day():
    """14 Oct 2026, the owner's machine: two programmes, the machine switched off for 6 minutes
    between them. With the default settings both are found whole, from the door locking to the drum
    stopping, through every fill and pause; the push goes out a finish delay later. A 16 W surge
    when the plug was switched on starts nothing that counts."""
    t0, readings = real_day("power-2026-10-14.csv")
    t = tracker.WashTracker()
    assert t.config == tracker.TrackerConfig(threshold_w=8.0, finish_delay=timedelta(minutes=3))
    ended, last = [], None
    for when, watts in readings:
        while (due := t.finish_due()) is not None and due <= when:
            ended.append((t.check(due, 0.2372), due))
        t.power(when, watts, last)
        last = when
    assert not t.running

    def s(seconds: float) -> datetime:
        return t0 + timedelta(seconds=seconds)

    washes = [(r.wash, due) for r, due in ended if r.counted]
    assert [(w.start, w.end) for w, _ in washes] == [(s(10786.3), s(12191.2)), (s(12701.2), s(15406.3))]
    assert [w.minutes for w, _ in washes] == [23, 45]
    assert [due - w.end for w, due in washes] == [tracker.FINISH_DELAY] * 2
    assert [round(w.kwh, 3) for w, _ in washes] == [0.021, 0.272]
    assert [(r.wash.start, r.counted) for r, _ in ended if not r.counted] == [(s(46.1), False)]


def test_threshold_is_exclusive():
    t = tracker.WashTracker(config=CFG)
    assert not t.power(at(0), 10.0)
    assert not t.running
    assert t.power(at(1), 10.1)


def test_unavailable_readings_change_nothing():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 500)
    assert not t.power(at(5), None)
    assert t.running and t.finish_due() == at(5) + tracker.MAX_BLIND  # only a very long outage ends it


def test_an_unavailable_plug_holds_the_finish_until_a_real_reading():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 500)
    t.power(at(30), 3)
    assert t.finish_due() == at(35)
    t.power(at(32), None)  # unavailable during the grace: nothing may end the wash now
    assert t.finish_due() == at(32) + tracker.MAX_BLIND
    assert t.check(at(40), None) is None and t.running
    # Back, and still low: it ends, at the time the draw first fell.
    t.power(at(50), 2)
    assert t.finish_due() == at(35)
    r = t.check(at(50), None)
    assert r.counted and r.wash.end == at(30)
    assert r.wash.kwh == pytest.approx(0.25)  # 500 W for 30 minutes; nothing after the draw fell


def test_an_unavailable_plug_that_comes_back_high_carries_on():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 500)
    t.power(at(30), 3)
    t.power(at(32), None)
    t.power(at(40), 800)  # the drum was spinning again: same wash
    assert t.running and t.finish_due() is None and t.state.started == at(0)


def test_energy_is_the_area_under_the_readings():
    # The wash on 14 October: no heating, ~20 W tumbling, then the spin at 244 W. The plug's meter
    # didn't report once while it ran; the power readings still add up to its energy.
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 20)
    t.power(at(18), 110)
    t.power(at(20.5), 244)
    t.power(at(23), 4.7)
    r = t.check(at(28), 0.25)
    assert r.counted and r.wash.minutes == 23
    assert r.wash.kwh == pytest.approx((20 * 18 + 110 * 2.5 + 244 * 2.5) / 60 / 1000)  # 0.021 kWh
    assert r.wash.cost == pytest.approx(r.wash.kwh * 0.25)


def test_energy_stops_where_the_wash_ends():
    # The finish delay's standby draw isn't part of the wash, even when it is counted meanwhile.
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 600)
    t.power(at(30), 8)
    t.power(at(34), 8.5)
    assert t.state.used_kwh == pytest.approx(0.3 + 8 * 4 / 60 / 1000)
    r = t.check(at(35), None)
    assert r.wash.kwh == pytest.approx(0.3) and r.wash.cost is None


def test_a_pause_that_turns_out_not_to_be_the_end_is_counted():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 600)
    t.power(at(10), 6)  # the drum pauses
    t.power(at(13), 600)  # and carries on: the pause is part of the wash
    assert t.state.below_since is None and t.state.below_kwh is None
    t.power(at(23), 2)
    r = t.check(at(28), None)
    assert r.wash.kwh == pytest.approx((600 * 20 + 6 * 3) / 60 / 1000)


def test_while_the_plug_is_unavailable_nothing_is_counted():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 1200)
    t.power(at(10), None)  # 20 minutes of unknown draw
    t.power(at(30), 1200)
    t.power(at(40), 2)
    r = t.check(at(45), None)
    assert r.wash.kwh == pytest.approx(1200 * 20 / 60 / 1000)


def test_a_plug_that_goes_quiet_counts_only_until_it_was_last_heard():
    # Found unavailable two hours after its last report: those hours are unknown, not 2 kW.
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 2000)
    t.power(at(130), None, last_seen=at(10))
    assert t.state.used_kwh == pytest.approx(2000 * 10 / 60 / 1000)
    t.power(at(140), None, last_seen=at(200))  # never past the moment it was found
    assert t.state.used_kwh == pytest.approx(2000 * 10 / 60 / 1000)


def test_a_wash_saved_by_1_1_mid_finish_ends_with_what_it_had_counted():
    old = {"running": True, "started": at(0).isoformat(), "below_since": at(30).isoformat(), "blind_since": None, "used_kwh": 0.4}
    t = tracker.WashTracker(config=CFG, state=tracker.TrackerState.from_dict(old))
    t.power(at(32), 3)
    r = t.check(at(35), None)
    assert r.wash.end == at(30) and r.wash.kwh == pytest.approx(0.4)


def test_a_restart_counts_on_from_the_first_reading_after_it():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 900)
    t.count(at(10))  # Home Assistant stopping: counted up to then, and saved
    restored = tracker.WashTracker(config=CFG, state=tracker.TrackerState.from_dict(t.state.as_dict()))
    assert restored.running and restored.state.used_kwh == pytest.approx(0.15)
    restored.power(at(12), 900)  # the draw while it was down is unknown
    restored.power(at(22), 3)
    r = restored.check(at(27), None)
    assert r.wash.start == at(0) and r.wash.kwh == pytest.approx(0.3)


def test_readings_are_counted_at_no_more_than_a_socket_delivers():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 500)
    t.power(at(10), 65000)  # a glitched reading
    t.power(at(11), 500)
    t.power(at(20), -40)  # and a negative one
    t.power(at(21), 500)
    t.power(at(30), 2)
    r = t.check(at(35), None)
    assert r.wash.kwh == pytest.approx((500 * 28 + tracker.MAX_W * 1) / 60 / 1000)


def test_an_uncertain_end_counts_until_the_plug_went_away():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 600)
    t.power(at(30), None)
    r = t.check(at(30) + tracker.MAX_BLIND, 0.2)
    assert r.uncertain and r.wash.kwh == pytest.approx(0.3) and r.wash.cost == pytest.approx(0.06)


def test_nothing_is_counted_between_washes():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 5)
    t.power(at(60), 5)
    assert t.state.used_kwh == 0.0
    t.power(at(61), 600)
    t.power(at(71), 2)
    t.check(at(76), None)
    t.power(at(200), 600)  # the next wash starts from nothing
    t.power(at(210), 2)
    assert t.check(at(215), None).wash.kwh == pytest.approx(0.1)


def test_units():
    assert tracker.to_watts(1.85, "kW") == pytest.approx(1850)
    assert tracker.to_watts(12, "W") == 12 and tracker.to_watts(12, None) == 12
    assert tracker.to_watts(1, "°C") is None


def test_state_survives_a_restart():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 500)
    t.power(at(50), 2)
    saved = t.state.as_dict()
    t2 = tracker.WashTracker(config=CFG, state=tracker.TrackerState.from_dict(saved))
    assert t2.running and t2.finish_due() == at(55)
    r = t2.check(at(70), 0.2)
    assert r.counted and r.wash.start == at(0) and r.wash.end == at(50)
    assert r.wash.kwh == pytest.approx(500 * 50 / 60 / 1000)


def test_a_wash_in_progress_saved_by_1_1_loads():
    # 1.1 kept an energy meter's readings; those are dropped, what it had counted is kept.
    old = {"running": True, "started": at(0).isoformat(), "below_since": None, "blind_since": None,
           "meter": "sensor.plug_energy", "start_kwh": 10.0, "last_kwh": 10.2, "last_kwh_at": at(20).isoformat(),
           "used_kwh": 0.2, "pending": None, "pending_kwh": None, "pending_at": None}
    s = tracker.TrackerState.from_dict(old)
    assert s.running and s.started == at(0) and s.used_kwh == pytest.approx(0.2)
    assert tracker.TrackerState.from_dict({**old, "used_kwh": None}).used_kwh == 0.0


def test_state_round_trips():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 500)
    t.power(at(5), 4)
    t.power(at(6), None)
    back = tracker.TrackerState.from_dict(t.state.as_dict())
    assert back == t.state and back.blind and back.below_kwh == pytest.approx(500 * 5 / 60 / 1000)


def test_wash_round_trips_through_storage():
    w = tracker.Wash(start=at(0), end=at(112), kwh=0.8431, cost=0.19998)
    back = tracker.Wash.from_dict(w.as_dict())
    assert back.start == w.start and back.end == w.end and back.minutes == 112
    assert back.kwh == 0.843 and back.cost == 0.2


def test_totals_add_up_known_energy_only():
    tz = UTC
    day = datetime(2026, 10, 14, tzinfo=UTC).date()
    log = [tracker.Wash(start=at(0), end=at(100), kwh=0.8, cost=0.2), tracker.Wash(start=at(200), end=at(260), kwh=None, cost=None)]
    eq = tracker.totals_on_days(log, day, day, tz)
    assert eq == {"washes": 2, "kwh": 0.8, "cost": 0.2, "minutes": 160}
    assert tracker.totals_on_days([], day, day, tz) == {"washes": 0, "kwh": None, "cost": None, "minutes": 0}


def test_counts_use_local_days_and_finish_times():
    # 23:30 local on the 13th is 22:30 UTC; 00:30 local on the 14th is 23:30 UTC on the 13th.
    late = tracker.Wash(start=datetime(2026, 10, 13, 21, 0, tzinfo=UTC), end=datetime(2026, 10, 13, 22, 30, tzinfo=UTC))
    after_midnight = tracker.Wash(start=datetime(2026, 10, 13, 22, 0, tzinfo=UTC), end=datetime(2026, 10, 13, 23, 30, tzinfo=UTC))
    log = [late, after_midnight]
    d13, d14 = date(2026, 10, 13), date(2026, 10, 14)
    assert tracker.totals_on_days(log, d13, d13, LONDON)["washes"] == 1
    assert tracker.totals_on_days(log, d14, d14, LONDON)["washes"] == 1
    assert tracker.totals_on_days(log, d13 - timedelta(days=6), d14, LONDON)["washes"] == 2
    assert tracker.washes_between(log, late.end, after_midnight.end) == [late]


def nb(text: str) -> str:
    """Wording as written in a test, with the no-break spaces that keep a number with its unit."""
    return text.replace("~", "\u00a0")


def test_push_wording():
    w = tracker.Wash(start=datetime(2026, 10, 14, 11, 28, tzinfo=UTC), end=datetime(2026, 10, 14, 13, 20, tzinfo=UTC), kwh=0.843, cost=0.2)
    assert tracker.finished_message(w, LONDON, "GBP") == nb("Finished at 14:20 after 1~h~52~min · 0.84~kWh · 20p")
    w.kwh, w.cost = None, None
    assert tracker.finished_message(w, LONDON, "GBP") == nb("Finished at 14:20 after 1~h~52~min")
    assert tracker.format_duration(45) == nb("45~min") and tracker.format_duration(120) == nb("2~h")
    assert tracker.format_money(1.5, "SEK") == nb("1.50~SEK") and tracker.format_money(1.5, None) == "1.50"


def test_a_plug_that_never_comes_back_ends_the_wash_uncertainly():
    t = tracker.WashTracker(config=CFG)
    t.power(at(0), 500)
    t.power(at(40), None)
    assert t.check(at(40) + tracker.MAX_BLIND - timedelta(minutes=1), 0.25) is None
    r = t.check(at(40) + tracker.MAX_BLIND, 0.25)
    assert r.uncertain and r.counted and r.wash.end == at(40) and r.wash.minutes == 40
    t.power(at(400), 500)  # a fresh wash afterwards starts cleanly
    assert t.state.blind_since is None and t.state.started == at(400)


@pytest.mark.parametrize(
    ("value", "currency", "text"),
    [
        pytest.param(0.0641, "GBP", "6p", id="pence"),
        pytest.param(0.065, "GBP", "7p", id="half-up like the card"),
        pytest.param(0.0049, "GBP", "0.5p", id="under a penny"),
        pytest.param(0.0, "GBP", "0p", id="nothing"),
        pytest.param(0.994, "GBP", "99p", id="just under a pound"),
        pytest.param(0.996, "GBP", "£1.00", id="a pound"),
        pytest.param(1.234, "GBP", "£1.23", id="pounds"),
        pytest.param(0.12, "EUR", "12c", id="cents"),
        pytest.param(0.12, "SEK", "0.12~SEK", id="no minor unit known"),
    ],
)
def test_one_washs_cost_is_in_pence(value: float, currency: str, text: str) -> None:
    assert tracker.format_wash_cost(value, currency) == nb(text)
