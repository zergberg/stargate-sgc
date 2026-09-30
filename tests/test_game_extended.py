import json
import random

import pytest

from sgc.game import clock, schedule
from sgc.game import engine as eng
from sgc.game.state import from_dict, to_dict
from sgc.game.world import World
from tests.test_game_engine import Rig

ALWAYS = {"calm": (100, 0, 0), "jaffa": (100, 0, 0), "goauld": (100, 0, 0)}


def collecting(monkeypatch, drone="malp", **traits):
    """An extended report whose live pass is in: the drone is on the world, collecting."""
    monkeypatch.setitem(eng.DESTROYED, "normal", 0)
    monkeypatch.setitem(eng.CAPTURED, "jaffa", 0)
    monkeypatch.setitem(eng.CAPTURED, "goauld", 0)
    r = Rig()
    w = r.world(5, **{"env": "normal", "inhabitants": "none", **traits})
    msg = (r.e.probe if drone == "malp" else r.e.send_uav)(w.id, extended=True)
    assert msg == f"{eng.EXTENDED[drone]} QUEUED FOR {w.name.upper()}"
    r.e.advance(clock.GATE_MINUTES["probe" if drone == "malp" else "uav"])
    assert w.drone == drone
    return r, w


def uplinked(monkeypatch, outcome, drone="malp", **traits):
    odds = {"full": (100, 0, 0), "partial": (0, 100, 0), "lost": (0, 0, 100)}[outcome]
    r, w = collecting(monkeypatch, drone, **traits)
    monkeypatch.setitem(eng.UPLINK_ODDS, drone, {"calm": odds, "jaffa": odds, "goauld": odds})
    r.e.advance(eng.UPLINK_HOURS[drone][1] * clock.HOUR + clock.HOUR)
    return r, w


# ---------------------------------------------------------------- scheduling

@pytest.mark.parametrize("drone", ["malp", "uav"])
def test_an_uplink_falls_due_in_its_window_and_joins_the_gate_queue(monkeypatch, drone):
    r, w = collecting(monkeypatch, drone)
    lo, hi = eng.UPLINK_HOURS[drone]
    [ev] = r.c.events.find(lambda e: e.kind == "uplink")
    assert r.c.now + lo * clock.HOUR <= ev.due <= r.c.now + hi * clock.HOUR
    [row] = r.e.schedule_view()
    window = f"{schedule.at(r.c.now + lo * clock.HOUR, r.c.now)}–{schedule.at(r.c.now + hi * clock.HOUR, r.c.now)}"
    assert row.what == f"{drone.upper()} UPLINK · {w.name.upper()}" and row.status == f"EXPECTED {window}"
    assert row.brief == f"{drone.upper()} UPLINK {window}" and not row.cancellable and not row.movable
    assert r.e.cancel(row.id, confirm=True) == "THE DRONE IS ALREADY COLLECTING"
    ev.due += 7                                            # the rolled time never shows
    assert r.e.schedule_view()[0].status == row.status
    r.c.gate_until = ev.due + 60                           # the gate is busy when it falls due
    r.e.advance(ev.due - r.c.now)
    [row] = r.e.schedule_view()
    assert row.kind == "dial_out" and row.what == f"{drone.upper()} UPLINK · {w.name.upper()}"
    assert row.movable and not row.cancellable and row.brief == f"1 {drone.upper()} UPLINK · {w.name.upper()}"
    assert r.e.cancel(row.id, confirm=True) == "THE DRONE IS ALREADY COLLECTING"


def test_an_uplink_can_be_moved_in_the_gate_queue(monkeypatch):
    r, w = collecting(monkeypatch)
    [ev] = r.c.events.find(lambda e: e.kind == "uplink")
    r.c.gate_until = ev.due + 60
    r.e.advance(ev.due - r.c.now)
    v = r.world(4, env="normal", inhabitants="none")
    r.e.probe(v.id)
    assert r.e.move(f"dial:malp:{v.id}", -1) == f"MALP TO {v.name.upper()}: NOW 1 IN THE GATE QUEUE"
    assert [i.id for i in r.e.schedule_view()] == [f"dial:malp:{v.id}", f"dial:uplink:{w.id}"]


def test_a_drone_lost_on_the_live_pass_has_no_extended_report(monkeypatch):
    monkeypatch.setitem(eng.DESTROYED, "normal", 100)
    r = Rig()
    w = r.world(5, env="normal", inhabitants="none")
    r.e.probe(w.id, extended=True)
    r.e.advance(clock.HOUR)
    assert not r.c.events.find(lambda e: e.kind == "uplink") and r.e.schedule_view() == []


# ---------------------------------------------------------------- the odds

@pytest.mark.parametrize("drone,inhabitants,env,odds", [
    ("malp", "none", "normal", (85, 10, 5)), ("malp", "human", "normal", (85, 10, 5)),
    ("malp", "unas", "normal", (85, 10, 5)), ("malp", "ally", "normal", (85, 10, 5)),
    ("malp", "jaffa", "normal", (75, 10, 15)), ("malp", "goauld", "normal", (65, 10, 25)),
    ("uav", "none", "normal", (75, 20, 5)), ("uav", "jaffa", "normal", (60, 20, 20)),
    ("uav", "goauld", "normal", (45, 20, 35)),
    ("malp", "none", "radiation", (75, 10, 15)), ("uav", "goauld", "extreme", (35, 20, 45)),
    ("malp", "none", "toxic", (85, 10, 5)),
])
def test_the_uplink_odds_table(drone, inhabitants, env, odds):
    w = World("P3X-774", (1, 2, 3, 4, 5, 6), env=env, inhabitants=inhabitants)
    assert eng.uplink_odds(w, drone) == odds
    full, partial, lost = odds
    assert eng.uplink_outcome(w, drone, full - 0.01) == "full"
    assert eng.uplink_outcome(w, drone, full) == "partial"
    assert eng.uplink_outcome(w, drone, full + partial - 0.01) == "partial"
    assert eng.uplink_outcome(w, drone, full + partial) == "lost"


def test_seeded_rolls_land_near_the_table():
    rng = random.Random(1)
    w = World("P3X-774", (1, 2, 3, 4, 5, 6), inhabitants="jaffa")
    rolls = [eng.uplink_outcome(w, "uav", rng.random() * 100) for _ in range(4000)]
    for outcome, want in zip(("full", "partial", "lost"), (60, 20, 20)):
        assert abs(100 * rolls.count(outcome) / len(rolls) - want) < 3, outcome


# ---------------------------------------------------------------- outcomes

def test_a_full_malp_uplink_gives_full_readings_and_the_soil_samples(monkeypatch):
    r, w = uplinked(monkeypatch, "full", features=("ruins", "naquadah"))
    assert w.seen["features"] == "ruins, naquadah traces"          # full detail on Officer
    assert w.seen["subsurface"] == "buried structures, naquadah deposit"
    assert f"MALP EXTENDED REPORT FROM {w.name.upper()}" in r.logs and w.drone == "malp"
    assert f"UPLINK TO THE MALP ON {w.name.upper()}" in r.logs
    assert any("extended report" in text for _, text in w.reports)


def test_a_full_uav_uplink_names_the_world_finds_our_drones_and_rolls_for_an_address(monkeypatch):
    from sgc.game.state import CapturedDrone
    monkeypatch.setattr(eng, "INTEL_ROLL", 1.0)
    r, w = collecting(monkeypatch, "uav", inhabitants="human", hidden_names={"locals": "Tel'kar",
                                                                             "comms": "Telkaria"})
    r.c.captured_drones.append(CapturedDrone("malp", w.id, 0))
    monkeypatch.setitem(eng.UPLINK_ODDS, "uav", ALWAYS)
    n = len(r.c.worlds)
    r.e.advance(6 * clock.HOUR)
    assert w.name == "Telkaria" and r.c.captured_drones[0].located and len(r.c.worlds) == n + 1


def test_a_garbled_uplink_keeps_only_the_live_pass(monkeypatch):
    r, w = uplinked(monkeypatch, "partial", features=("ruins", "naquadah"))
    assert f"MALP UPLINK GARBLED — {w.name.upper()}" in r.logs and "subsurface" not in w.seen
    assert w.drone == "malp"


@pytest.mark.parametrize("drone,inhabitants,drone_after,wreck,captured", [
    ("malp", "none", None, None, False), ("uav", "none", None, "crashed", False),
    ("malp", "jaffa", None, None, True), ("uav", "goauld", None, None, True),
])
def test_a_lost_uplink(monkeypatch, drone, inhabitants, drone_after, wreck, captured):
    r, w = uplinked(monkeypatch, "lost", drone, inhabitants=inhabitants)
    assert w.drone is drone_after and w.wreck == wreck
    assert bool(r.c.captured_drones) is captured and (w.status == "hostile") is captured
    if drone == "uav" and not captured:
        assert any(line in r.logs for line in (f"UAV DOWN ON {w.name.upper()} — OUT OF FUEL",
                                                f"UAV DOWN ON {w.name.upper()} — CRASHED"))


# ---------------------------------------------------------------- a team gets there first

def test_a_team_arriving_first_brings_the_drone_and_its_data_home(monkeypatch):
    r, w = collecting(monkeypatch, features=("naquadah",))
    w.status = "probed"
    stock = r.c.stock["malp"]
    r.e.assign(w.id, "SG-2", "survey")
    m = r.c.mission(1)
    m.end = r.c.now + 2 * clock.HOUR                    # home before the uplink
    r.e.advance(1)
    r.c.events.cancel(lambda e: e.kind == "checkin")
    r.e.advance(3 * clock.HOUR)
    assert m.state == "complete" and w.drone is None and r.c.stock["malp"] == stock + 1
    assert not r.c.events.find(lambda e: e.kind in ("uplink", "uplink_report") or e.data.get("op") == "uplink")
    assert w.seen["subsurface"] == "naquadah deposit"
    assert "SG-2 BROUGHT THE MALP AND ITS DATA HOME" in m.findings
    assert f"MALP EXTENDED REPORT FROM {w.name.upper()}" in m.findings


# ---------------------------------------------------------------- the save and the screen

def test_every_stage_of_an_extended_report_survives_a_save(monkeypatch):
    r, w = collecting(monkeypatch)
    [ev] = r.c.events.find(lambda e: e.kind == "uplink")

    def same():
        b = Rig(campaign=from_dict(json.loads(json.dumps(to_dict(r.c)))))
        assert to_dict(b.c) == to_dict(r.c)
    same()                                                 # collecting
    r.c.gate_until = ev.due + 30
    r.e.advance(ev.due - r.c.now)
    assert r.c.events.find(lambda e: e.kind == "dial_out" and e.data["op"] == "uplink")
    same()                                                 # the uplink waits for the gate
    r.e.advance(35)
    assert r.c.events.find(lambda e: e.kind == "uplink_report")
    same()                                                 # the uplink's gate is open


def test_the_uplink_scene_fills_the_panel_or_finds_no_carrier():
    r = Rig(director=True)
    w = r.world(5)
    seen = {"env": "breathable atmosphere", "life": "none detected", "subsurface": "no anomalies"}
    step = next(st for st in r.e._v_uplink(w, "full", seen) if st.log == "EXTENDED DATA RECEIVED")
    assert step.duration == eng.UPLINK_FEED_S == 10.0
    s = r.d.scene
    step.update(s, 1.0)
    assert s.panel_rows == [("ENV", "breathable atmosphere"), ("LIFE", "none detected"),
                            ("SUBSURFACE", "no anomalies")]
    lost = next(st for st in r.e._v_uplink(w, "lost", {}) if st.log == "NO CARRIER")
    lost.update(s, 0.5)
    assert s.panel_rows == [("SIGNAL", "LOST")]
