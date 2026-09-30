import json

import pytest

from sgc.game import clock, rules
from sgc.game import engine as eng
from sgc.game.state import from_dict, to_dict
from tests.test_game_engine import PROBE, Rig


def step(steps, log):
    return next(st for st in steps if st.log == log)


def world(r, i=5, **traits):
    return r.world(i, **{"env": "normal", "inhabitants": "none", **traits})


# ---------------------------------------------------------------- one live dial

@pytest.mark.parametrize("drone,hold", [("malp", 15), ("uav", 25)])
def test_a_probe_resolves_in_one_dial_and_the_drone_stays_on_the_world(monkeypatch, drone, hold):
    monkeypatch.setitem(eng.DESTROYED, "normal", 0)
    r = Rig()
    w = world(r)
    (r.e.probe if drone == "malp" else r.e.send_uav)(w.id)
    r.e.advance(0)
    assert clock.GATE_MINUTES["probe" if drone == "malp" else "uav"] == hold
    assert r.c.gate_until == r.c.now + hold and w.drone is None       # still streaming
    assert rules.deployed(r.c, drone) == 1                            # out of stores all the while
    assert not r.c.events.find(lambda e: e.kind == "malp_return")
    r.e.advance(hold)
    assert w.drone == drone and w.status == "probed" and w.last_visit == r.c.now
    assert f"{drone.upper()} TELEMETRY FROM {w.name.upper()}" in r.logs
    assert not r.c.events.find(lambda e: e.kind in ("malp_return", "drone_report"))


def test_a_probe_s_report_is_not_on_the_queue_and_survives_a_save_mid_dial():
    r = Rig(PROBE)
    w = world(r)
    r.e.probe(w.id)
    r.e.advance(5)
    assert r.e.schedule_view() == []
    b = Rig(PROBE, campaign=from_dict(json.loads(json.dumps(to_dict(r.c)))))
    for x in (r, b):
        x.e.advance(clock.HOUR)
        x.e.save_now()
    assert r.saves[-1] == b.saves[-1] and b.c.worlds[w.id].drone == "malp"


def test_a_uav_reads_the_subsurface_and_a_plain_malp_probe_never_does(monkeypatch):
    monkeypatch.setitem(eng.DESTROYED, "normal", 0)
    r = Rig()
    a, b = world(r, 4, features=("naquadah",)), world(r, 5, features=("naquadah",))
    r.e.send_uav(a.id)
    r.e.probe(b.id)
    r.e.advance(clock.HOUR)
    assert a.seen["subsurface"] == "naquadah deposit" and "SUBSURFACE: naquadah deposit" in a.telemetry
    assert "subsurface" not in b.seen


# ---------------------------------------------------------------- losing the drone

@pytest.mark.parametrize("inhabitants,wreck", [("none", "crashed"), ("human", "crashed"), ("jaffa", "shot_down"),
                                               ("goauld", "shot_down")])
def test_a_destroyed_uav_leaves_a_wreck(monkeypatch, inhabitants, wreck):
    monkeypatch.setitem(eng.DESTROYED, "normal", 100)
    r = Rig()
    w = world(r, inhabitants=inhabitants)
    r.e.send_uav(w.id)
    r.e.advance(clock.HOUR)
    assert w.wreck == wreck and w.drone is None and r.c.stock["uav"] == 1
    assert any(line.startswith("UAV SIGNAL LOST ON") for line in r.logs)


def test_a_destroyed_malp_is_simply_gone(monkeypatch):
    monkeypatch.setitem(eng.DESTROYED, "normal", 100)
    r = Rig()
    w = world(r)
    r.e.probe(w.id)
    r.e.advance(clock.HOUR)
    assert w.wreck is None and w.drone is None and r.c.stock["malp"] == 3 and w.status == "probed"


def test_a_captured_drone_is_held_on_the_world(monkeypatch):
    monkeypatch.setitem(eng.DESTROYED, "normal", 0)
    monkeypatch.setitem(eng.CAPTURED, "jaffa", 100)
    r = Rig()
    w = world(r, inhabitants="jaffa")
    r.e.probe(w.id)
    r.e.advance(clock.HOUR)
    assert w.status == "hostile" and w.drone is None and w.wreck is None
    assert [(d.drone, d.world) for d in r.c.captured_drones] == [("malp", w.id)]


# ---------------------------------------------------------------- on screen

def test_the_malp_feed_fills_the_panel_one_row_at_a_time():
    r = Rig(director=True)
    w = world(r)
    seen = {"env": "breathable atmosphere", "life": "none detected", "features": "ruins"}
    steps = r.e._v_probe(w, "malp", seen, "ok")
    feed = step(steps, "TELEMETRY RECEIVED")
    assert feed.duration == eng.MALP_FEED_S == 20.0
    s = r.d.scene
    counts = []
    for p in (0.0, 0.3, 0.6, 0.9, 1.0):
        feed.update(s, p)
        counts.append(len(s.panel_rows))
    assert counts == [0, 1, 2, 3, 3] and s.panel_rows[0] == ("ENV", "breathable atmosphere")


def test_the_uav_feed_runs_ninety_seconds_with_its_flight_rows_drifting():
    r = Rig(director=True)
    w = world(r)
    seen = {"env": "breathable atmosphere", "life": "none detected", "inhabitants": "no settlements",
            "subsurface": "no anomalies"}
    feed = step(r.e._v_probe(w, "uav", seen, "ok"), "TELEMETRY RECEIVED")
    assert feed.duration == eng.UAV_FEED_S == 90.0
    s = r.d.scene
    feed.update(s, 0.1)
    early = list(s.panel_rows)
    feed.update(s, 0.9)
    late = list(s.panel_rows)
    assert [k for k, _ in early[:4]] == ["ALT", "HDG", "SPEED", "FUEL"] and early[:4] != late[:4]
    assert len(early) < len(late) == 4 + len(seen) and s.feed is not None
    feed.update(s, 1.0)
    assert s.feed is None


@pytest.mark.parametrize("drone", ["malp", "uav"])
def test_a_lost_drone_s_feed_stops_partway_with_signal_lost(drone):
    r = Rig(director=True)
    w = world(r)
    seen = {"env": "breathable atmosphere", "life": "none detected"}
    ok, lost = r.e._v_probe(w, drone, seen, "ok"), r.e._v_probe(w, drone, seen, "destroyed")
    full, cut = step(ok, "TELEMETRY RECEIVED").duration, step(lost, "TELEMETRY RECEIVED").duration
    assert 0.2 * full <= cut <= 0.7 * full
    s = r.d.scene
    if drone == "uav":
        step(lost, "UAV SIGNAL LOST").update(s, 1.0)
        assert s.feed.lost == 1.0 and s.panel_rows == [("SIGNAL", "LOST")]
    else:
        step(lost, "TELEMETRY RECEIVED").update(s, 1.0)
        assert s.panel_rows == [("ENV", "breathable atmosphere"), ("SIGNAL", "LOST")]   # its last reading
        step(r.e._v_probe(w, drone, seen, "captured"), "TELEMETRY RECEIVED").update(s, 1.0)
        assert s.panel_rows == [("SIGNAL", "LOST")]                                  # cut before anything
    assert Rig().e._v_probe(w, drone, seen, "ok") == []                     # headless: nothing to show


# ---------------------------------------------------------------- an older save

def test_an_older_save_s_pending_malp_return_still_resolves_the_old_way(monkeypatch):
    monkeypatch.setitem(eng.DESTROYED, "normal", 0)
    r = Rig()
    w = world(r)
    r.c.events.push(r.c.now + 90, "malp_return", {"world": w.id, "drone": "malp", "sent": r.c.now - 10})
    b = Rig(campaign=from_dict(json.loads(json.dumps(to_dict(r.c)))))
    b.e.advance(2 * clock.HOUR)
    v = b.c.worlds[w.id]
    assert v.drone == "malp" and v.status == "probed" and f"MALP TELEMETRY FROM {v.name.upper()}" in b.logs
