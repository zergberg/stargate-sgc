import pytest

from sgc.game import clock, rules
from sgc.game import engine as eng
from sgc.game.state import CapturedDrone
from tests.test_game_engine import Rig

RESCUE = """
id = "t_rescue"
kind = "debrief"
mission_type = "rescue"
[node.start]
text.full = "{team} brought {captive} home from {world}."
default = "file"
[[node.start.choice]]
key = "file"
label = "File it"
outcome = { effects = ["team {captive} base"], end = true }
"""

RECOVER = """
id = "t_recover"
kind = "debrief"
mission_type = "recover"
[node.start]
text.full = "{team} recovered our drone from {world}."
default = "file"
[[node.start.choice]]
key = "file"
label = "File it"
outcome = { effects = ["recover {world}"], end = true }
"""


@pytest.fixture(autouse=True)
def calm(monkeypatch):
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    monkeypatch.setattr(eng, "FOLLOWED", 0.0)


def probed(r, i=5, **traits):
    return r.world(i, **{"status": "probed", "env": "normal", "inhabitants": "none", **traits})


def test_mission_types_need_specialties_and_something_to_go_for():
    r = Rig()
    w = probed(r)
    w.options += ["trade", "raid", "study", "mine", "aid", "rescue", "recover"]
    assert r.e.mission_types(w.id, "SG-3") == ["survey", "raid", "mine"]
    assert r.e.mission_types(w.id, "SG-4") == ["survey", "study", "mine"]
    assert r.e.mission_types(w.id, "SG-1") == ["survey", "trade", "raid", "study", "mine", "aid"]
    r.c.teams["SG-2"].status, r.c.teams["SG-2"].where = "captured", w.id
    r.c.captured_drones.append(CapturedDrone("malp", w.id, r.c.now, located=True))
    assert r.e.mission_types(w.id, "SG-3")[-2:] == ["rescue", "recover"]
    assert r.e.mission_target(w.id, "rescue") == "SG-2" and r.e.mission_target(w.id, "recover") == "malp"
    assert r.e.mission_target(w.id, "survey") is None


def test_durations_follow_the_type_and_recon_strength(monkeypatch):
    r = Rig()
    monkeypatch.setattr(r.e.rng, "uniform", lambda a, b: 1.0)
    assert r.e._duration("SG-3", "raid") == 18 * 60 and r.e._duration("SG-3", "mine") == 48 * 60
    assert r.e._duration("SG-2", "mine") == 36 * 60 and r.e._duration("SG-1", "survey") == 21 * 60
    r.c.teams["SG-3"].secondary = "recon"
    assert r.e._duration("SG-3", "survey") == 21 * 60


def test_departing_for_a_goauld_world_draws_its_attention():
    r = Rig()
    w = probed(r, owner="Sokar", inhabitants="jaffa")
    r.e.assign(w.id, "SG-3", "survey")
    r.e.advance(1)
    assert r.c.factions["sokar"].attention == 5


def test_a_rescue_brings_the_captive_home():
    r = Rig(RESCUE)
    w = probed(r)
    rules.apply_all([rules.parse_effect("team {team} captured")], r.c, {"team": "SG-2", "world_id": w.id,
                                                                       "world": w.name})
    assert "rescue" in w.options
    assert r.e.assign(w.id, "SG-3", "rescue").startswith("SG-3 ASSIGNED: RESCUE")
    m = r.c.mission(1)
    assert m.target == "SG-2" and "rescue" not in r.e.mission_types(w.id, "SG-4")    # one rescue at a time
    r.e.advance(m.end - r.c.now + 60)
    assert m.state == "complete" and r.c.teams["SG-2"].status == "base" and r.c.teams["SG-2"].idc == "valid"


def test_a_recovery_brings_a_captured_drone_home():
    r = Rig(RECOVER)
    w = probed(r)
    r.c.captured_drones.append(CapturedDrone("malp", w.id, r.c.now, located=True))
    w.options.append("recover")
    stock = r.c.stock["malp"]
    r.e.assign(w.id, "SG-2", "recover")
    m = r.c.mission(1)
    assert m.target == "malp"
    r.e.advance(m.end - r.c.now + 60)
    assert r.c.captured_drones == [] and r.c.stock["malp"] == stock + 1


def test_a_captured_drone_is_held_and_its_goauld_notices(monkeypatch):
    monkeypatch.setitem(eng.DESTROYED, "normal", 0)
    monkeypatch.setitem(eng.CAPTURED, "jaffa", 100)
    r = Rig()
    w = r.world(5, env="normal", inhabitants="jaffa", owner="Heru'ur")
    r.e.probe(w.id)
    r.e.advance(200)
    assert r.c.captured_drones[0].drone == "malp" and r.c.captured_drones[0].world == w.id
    assert w.status == "hostile" and r.c.factions["heruur"].attention == 10


def test_a_uav_flight_locates_a_drone_held_on_that_world(monkeypatch):
    monkeypatch.setitem(eng.DESTROYED, "normal", 0)
    r = Rig()
    r.c.upgrades.add("uav_program")
    r.c.stock["uav"] = 1
    w = r.world(5, env="normal", inhabitants="none")
    r.c.captured_drones.append(CapturedDrone("malp", w.id, 0))
    r.e.send_uav(w.id)
    r.e.advance(120)
    assert r.c.captured_drones[0].located and "recover" in w.options


def test_uavs_need_the_program_and_recalls_cost_wear():
    r = Rig()
    r.c.upgrades.discard("uav_program")                # the rig gives it; a new campaign doesn't have it
    r.c.stock["uav"] = 1
    w = r.world(5)
    assert r.e.send_uav(w.id) == "NEEDS THE UAV PROGRAM" and r.c.stock["uav"] == 1
    v = r.world(6, drone="malp")
    r.c.funding = 4
    assert r.e.recall_drone(v.id) == "NOT ENOUGH FUNDING FOR THE RECALL (5)"
    r.c.funding = 100
    assert r.e.recall_drone(v.id).startswith("RECALL QUEUED") and r.c.funding == 95


def test_debriefs_count_missions_and_analysts_add_an_intel_roll(monkeypatch):
    monkeypatch.setattr(eng, "INTEL_ROLL", 1.0)
    for analysts, extra in ((False, 1), (True, 2)):
        r = Rig()
        if analysts:
            r.c.upgrades.add("database_analysts")
        w = probed(r)
        n = len(r.c.worlds)
        r.e.assign(w.id, "SG-2", "survey")
        m = r.c.mission(1)
        r.e.advance(m.end - r.c.now + 60)
        assert len(r.c.worlds) == n + extra and r.c.ledger["missions"] == 1


def test_trade_and_aid_end_in_contact():
    r = Rig()
    w = probed(r, inhabitants="human")
    w.options.append("aid")
    r.c.teams["SG-4"].secondary = "medical"
    r.e.assign(w.id, "SG-4", "aid")
    m = r.c.mission(1)
    r.e.advance(m.end - r.c.now + 60)
    assert w.status == "contact"


def test_purchases_commissioning_and_training_through_the_engine():
    r = Rig()
    r.c.upgrades.discard("uav_program")
    assert r.e.buy("malp").startswith("MALP PURCHASED") and r.logs[-1].startswith("MALP PURCHASED") and r.saves
    n = len(r.saves)
    assert r.e.buy("uav") == "NEEDS THE UAV PROGRAM" and len(r.saves) == n
    assert r.e.commission("medical").startswith("SG-5 COMMISSIONED")
    assert r.e.train("SG-3", "medical").startswith("SG-3 TRAINING")
    assert r.e.set_reserve("malp", 3) == "MALP RESERVE: 3" and r.c.reserve["malp"] == 3
    assert r.c.funding == 500 - 20 - 200 - 100


def test_a_cancelled_recall_refunds_its_wear():
    r = Rig()
    r.c.gate_until = r.c.now + 600                      # the gate is busy: the recall waits in the queue
    v = r.world(6, drone="uav")
    r.c.funding = 100
    r.e.recall_drone(v.id)
    assert r.c.funding == 85
    assert r.e.cancel(f"dial:recall:{v.id}", confirm=True).startswith("CANCELLED")
    assert r.c.funding == 100 and v.drone == "uav"


def test_a_recall_with_no_drone_left_to_fetch_refunds_its_wear():
    r = Rig()
    v = r.world(6, drone="malp")
    r.c.funding = 100
    r.e.recall_drone(v.id)
    v.drone = None                                      # taken on the world before the gate dialed
    stock = r.c.stock["malp"]
    r.e.advance(30)
    assert r.c.funding == 100 and r.c.stock["malp"] == stock
