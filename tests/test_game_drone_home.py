import json

from sgc.game import clock, rules, schedule
from sgc.game import engine as eng
from sgc.game.engine import visuals
from sgc.game.state import from_dict, to_dict
from tests.test_game_engine import Rig
from tests.test_game_extended import collecting
from tests.test_game_missions import probed


def _parked(r, w, drone):
    """A drone that already has a check-in scheduled, as any real parked drone would."""
    r.c.events.push(r.c.now + 4 * clock.HOUR, "drone_checkin", {"world": w.id, "drone": drone})


def test_a_departing_team_sends_a_parked_malp_home_about_15_minutes_later():
    r = Rig()
    w = probed(r, drone="malp")
    _parked(r, w, "malp")
    stock = r.c.stock["malp"]
    before = r.c.now
    r.e.assign(w.id, "SG-2", "survey")
    m = r.c.mission(1)
    r.e.advance(1)
    [ev] = r.c.events.find(lambda e: e.kind == "drone_home")
    assert ev.due == before + eng.DRONE_HOME_MINUTES
    assert ev.data == {"world": w.id, "mission": m.id, "team": "SG-2", "drone": "malp"}
    r.e.advance(eng.DRONE_HOME_MINUTES)
    assert w.drone is None and r.c.stock["malp"] == stock + 1
    assert f"SG-2 SENT THE MALP HOME FROM {w.name.upper()}" in r.logs
    assert f"SG-2 SENT THE MALP HOME FROM {w.name.upper()}" in m.findings
    assert not r.c.events.find(lambda e: e.kind == "drone_checkin")


def test_a_departing_team_sends_a_parked_uav_home_about_15_minutes_later():
    r = Rig()
    w = probed(r, drone="uav")
    _parked(r, w, "uav")
    stock = r.c.stock["uav"]
    r.e.assign(w.id, "SG-2", "survey")
    m = r.c.mission(1)
    r.e.advance(eng.DRONE_HOME_MINUTES + 1)
    assert w.drone is None and r.c.stock["uav"] == stock + 1
    assert f"SG-2 SENT THE UAV HOME FROM {w.name.upper()}" in r.logs
    assert f"SG-2 SENT THE UAV HOME FROM {w.name.upper()}" in m.findings
    assert not r.c.events.find(lambda e: e.kind == "drone_checkin")


def test_an_extended_report_still_collecting_comes_home_as_a_full_return(monkeypatch):
    r, w = collecting(monkeypatch, features=("naquadah",))
    w.status = "probed"
    stock = r.c.stock["malp"]
    r.e.assign(w.id, "SG-2", "survey")
    m = r.c.mission(1)
    r.e.advance(eng.DRONE_HOME_MINUTES + 2)
    assert w.drone is None and r.c.stock["malp"] == stock + 1
    assert not r.c.events.find(lambda e: e.kind in ("uplink", "uplink_report") or e.data.get("op") == "uplink")
    assert w.seen["subsurface"] == "naquadah deposit"
    assert f"MALP EXTENDED REPORT FROM {w.name.upper()}" in m.findings
    assert any(line.startswith("SG-2 SENT THE MALP HOME FROM") for line in m.findings)


def test_no_drone_home_is_scheduled_without_a_parked_drone():
    r = Rig()
    w = probed(r)
    r.e.assign(w.id, "SG-2", "survey")
    r.e.advance(1)
    assert not r.c.events.find(lambda e: e.kind == "drone_home")


def test_nothing_happens_if_the_team_was_captured_before_the_drone_home_fires():
    r = Rig()
    w = probed(r, drone="malp")
    _parked(r, w, "malp")
    stock = r.c.stock["malp"]
    r.e.assign(w.id, "SG-2", "survey")
    r.e.advance(1)
    [ev] = r.c.events.find(lambda e: e.kind == "drone_home")
    rules.parse_effect("team {team} captured")(r.c, {"team": "SG-2", "world": w.name, "world_id": w.id})
    r.e.advance(ev.due - r.c.now + 1)
    assert w.drone == "malp" and r.c.stock["malp"] == stock
    assert not any("SENT THE MALP HOME" in line for line in r.logs)


def test_nothing_happens_if_the_mission_is_no_longer_active_when_due():
    """A recall (or anything else that ends the mission early) leaves drone_home with nothing to do: any
    drone still parked then comes home with the team instead, through the ordinary arrival, not this event."""
    r = Rig()
    w = probed(r, drone="malp")
    _parked(r, w, "malp")
    stock = r.c.stock["malp"]
    r.e.assign(w.id, "SG-2", "survey")
    m = r.c.mission(1)
    r.e.advance(1)
    [ev] = r.c.events.find(lambda e: e.kind == "drone_home")
    m.state = "aborted"
    r.e.advance(ev.due - r.c.now)
    assert w.drone == "malp" and r.c.stock["malp"] == stock
    assert not any("SENT THE MALP HOME" in line for line in r.logs)


def test_nothing_happens_if_the_drone_is_already_gone_when_due():
    r = Rig()
    w = probed(r, drone="malp")
    _parked(r, w, "malp")
    r.e.assign(w.id, "SG-2", "survey")
    r.e.advance(1)
    [ev] = r.c.events.find(lambda e: e.kind == "drone_home")
    rules.parse_effect("drone {world} captured")(r.c, {"world": w.name, "world_id": w.id})
    r.e.advance(ev.due - r.c.now)
    assert w.drone is None
    assert not any("SENT THE MALP HOME" in line for line in r.logs)


def test_the_queue_row_is_uncancellable():
    r = Rig()
    w = probed(r, drone="malp")
    r.e.assign(w.id, "SG-2", "survey")
    r.e.advance(1)
    [ev] = r.c.events.find(lambda e: e.kind == "drone_home")
    when = schedule.at(ev.due, r.c.now)
    [row] = [i for i in r.e.schedule_view() if i.kind == "drone_home"]
    assert row.id == f"home:{w.id}" and row.what == f"SG-2 SENDING MALP HOME · {w.name.upper()}"
    assert not row.cancellable and not row.movable
    assert row.brief == f"SG-2 SENDING MALP HOME {when}"
    assert r.e.cancel(row.id, confirm=True) == schedule.DRONE_HOME_REASON
    assert r.e.move(row.id, 1) == schedule.NOT_MOVABLE


def test_a_save_round_trip_mid_wait():
    r = Rig()
    w = probed(r, drone="uav")
    _parked(r, w, "uav")
    r.e.assign(w.id, "SG-2", "survey")
    r.e.advance(1)
    assert r.c.events.find(lambda e: e.kind == "drone_home")
    b = Rig(campaign=from_dict(json.loads(json.dumps(to_dict(r.c)))))
    assert to_dict(b.c) == to_dict(r.c)
    stock = b.c.stock["uav"]
    [ev] = b.c.events.find(lambda e: e.kind == "drone_home")
    b.e.advance(ev.due - b.c.now)
    assert b.c.worlds[w.id].drone is None and b.c.stock["uav"] == stock + 1
    assert not b.c.events.find(lambda e: e.kind == "drone_checkin")


def test_a_wreck_waits_for_mission_end_even_though_the_drone_goes_home_early(monkeypatch):
    monkeypatch.setitem(eng.SALVAGE, "crashed", 100)
    r = Rig()
    w = probed(r, drone="malp", wreck="crashed")
    r.c.funding = 500
    r.e.assign(w.id, "SG-2", "survey")
    m = r.c.mission(1)
    r.e.advance(eng.DRONE_HOME_MINUTES + 5)
    assert w.drone is None and w.wreck == "crashed"              # the drone went home; the wreck is untouched
    r.e.advance(m.end - r.c.now + clock.HOUR)
    assert m.state == "complete" and w.wreck is None
    assert "UAV WRECK SALVAGED — REPAIRED FOR 30" in m.findings


def test_the_scene_plays_idc_and_the_drone_coming_through():
    r = Rig(director=True)
    w = r.world(5)
    logs = [st.log for st in visuals.v_drone_home(r.d.scene, w, "SG-2", "malp") if st.log]
    assert "IDC RECEIVED — SG-2" in logs and any("COMING HOME" in line for line in logs)
    logs_uav = [st.log for st in visuals.v_drone_home(r.d.scene, w, "SG-2", "uav") if st.log]
    assert "IDC RECEIVED — SG-2" in logs_uav and "UAV RETURNING THROUGH THE GATE" in logs_uav
