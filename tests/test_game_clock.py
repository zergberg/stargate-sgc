import json

import pytest

from sgc.game import clock
from sgc.game.clock import Event, Scheduler


def test_pace_and_override():
    assert clock.seconds_per_hour("relaxed") == 120 and clock.seconds_per_hour("standard") == 60
    assert clock.seconds_per_hour("busy") == 10 and clock.seconds_per_hour("busy", 300) == 300
    assert clock.to_minutes(60, 60) == 60 and clock.to_minutes(10, 10) == 60 and clock.to_minutes(1, 120) == 0.5
    assert clock.to_seconds(60, 120) == 120


def test_decision_window_is_three_hours_and_at_least_a_minute():
    assert clock.window(60) == 180 and clock.window(120) == 180
    assert clock.window(10) == 360 and clock.to_seconds(clock.window(10), 10) == 60


def test_stamps():
    assert clock.START == 480 and clock.stamp(480) == "DAY 1 · 08:00"
    assert clock.stamp(11 * clock.DAY + 14 * 60 + 30.7) == "DAY 12 · 14:30"
    assert clock.short(clock.DAY + 5) == "D2 00:05" and clock.day(clock.DAY - 1) == 1


def test_events_pop_in_due_order_then_fifo():
    s = Scheduler()
    s.push(50, "b")
    s.push(10, "a")
    s.push(50, "c", {"world": "P3X-866"})
    assert s.peek().kind == "a" and len(s) == 3
    assert s.pop().kind == "a"
    assert [s.pop().kind, s.pop().kind] == ["b", "c"] and len(s) == 0 and s.peek() is None


def test_cancel_and_find():
    s = Scheduler()
    for i in range(5):
        s.push(i * 10, "checkin", {"mission": i % 2})
    assert [e.due for e in s.find(lambda e: e.data["mission"] == 1)] == [10, 30]
    assert s.cancel(lambda e: e.data["mission"] == 1) == 2
    assert [e.due for e in s] == [0, 20, 40]


def test_saving_mid_flight_gives_an_identical_future():
    a = Scheduler()
    for due, kind in [(30, "x"), (10, "y"), (30, "z")]:
        a.push(due, kind, {"n": due})
    a.pop()
    b = Scheduler.from_list(json.loads(json.dumps(a.to_list())), a.seq)
    assert b == a
    for s in (a, b):
        s.push(30, "late")
    assert [(e.kind, e.seq) for e in a] == [(e.kind, e.seq) for e in b] == [("x", 0), ("z", 2), ("late", 3)]


@pytest.mark.parametrize("bad", [[{"due": "1", "seq": 0, "kind": "x", "data": {}}],
                                 [{"due": 1, "seq": 0, "kind": 3, "data": {}}],
                                 [{"due": 1, "seq": True, "kind": "x", "data": {}}],
                                 ["nope"]])
def test_from_list_rejects_malformed_events(bad):
    with pytest.raises(ValueError):
        Scheduler.from_list(bad, 5)


def test_gate_is_one_resource():
    assert not hasattr(clock, "gate_free_at") and not hasattr(Scheduler, "pop_due")      # the engine never used them
    assert max(clock.GATE_MINUTES.values()) <= 30          # so a check-in waits 30 game minutes at most
    assert Event(1, 0, "a") < Event(1, 1, "b") < Event(2, 0, "c")


def test_remove_takes_matching_events_and_returns_them():
    s = Scheduler()
    a = s.push(10, "dial_out", {"n": 1})
    s.push(20, "checkin")
    assert s.remove(lambda e: e is a) == [a]
    assert [e.kind for e in s] == ["checkin"] and s.remove(lambda e: False) == []


def test_swap_trades_two_events_places_in_place_and_survives_a_save():
    s = Scheduler()
    a, b, c = s.push(10, "a"), s.push(10, "b"), s.push(30, "c")
    s.swap(a, c)
    assert [(e.kind, e.due, e.seq) for e in s] == [("c", 10, 0), ("b", 10, 1), ("a", 30, 2)] and s.seq == 3
    t = Scheduler.from_list(json.loads(json.dumps(s.to_list())), s.seq)
    assert t == s and [t.pop().kind for _ in range(3)] == ["c", "b", "a"]
    assert [s.pop().kind for _ in range(3)] == ["c", "b", "a"]
    with pytest.raises(ValueError):
        s.swap(a, Event(1, 99, "stray"))
