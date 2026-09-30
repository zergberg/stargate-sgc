import json

import pytest

from sgc.game import clock
from sgc.game import engine as eng
from sgc.game import rules
from sgc.game.state import from_dict, to_dict
from tests.test_game_engine import Rig, scen

CHECKIN = """
id = "t_checkin"
kind = "checkin"
[node.start]
text.full = "{team} checks in from {world}. All quiet."
default = "log"
[[node.start.choice]]
key = "log"
label = "Log it"
outcome = { end = true }
"""

UNDER_FIRE = """
id = "t_fire"
kind = "checkin"
mission_type = "survey"
[node.start]
text.full = "{team} is taking fire on {world}!"
situation = "under_fire"
default = "recall"
[[node.start.choice]]
key = "recall"
label = "Recall them"
outcome = { effects = ["recall {team}"], end = true }
[[node.start.choice]]
key = "hold"
label = "Hold position"
outcome = { effects = ["team {team} captured"], end = true }
[[node.start.choice]]
key = "reinforce"
label = "Reinforce"
requires = ["team_available"]
outcome = { effects = ["reinforce {team}"], end = true }
"""

DEBRIEF = """
id = "t_debrief"
kind = "debrief"
mission_type = "survey"
[node.start]
text.full = "{team} debrief: the locals call {designation} something else."
default = "log"
[[node.start.choice]]
key = "log"
label = "Log it"
outcome = { effects = ["reveal name {world} from locals", "unlock contact {world}"], end = true }
"""

FOLLOWED = """
id = "t_followed"
kind = "incoming"
on = "team_return"
[node.start]
text.full = "{team} is coming in hot from {world}, Jaffa right behind them."
situation = "hostiles_following"
default = "open_briefly"
[[node.start.choice]]
key = "open_briefly"
label = "Open briefly, then close"
outcome = { effects = ["personnel -5"], end = true }
[[node.start.choice]]
key = "keep_closed"
label = "Keep closed"
outcome = { effects = ["team {team} lost"], end = true }
"""


def checked_in(r) -> int:
    """Check-ins that came through: each reports CHECKIN's text."""
    return sum("checks in from" in line for line in r.logs)


def probed(r, i=5, **traits):
    w = r.world(i, **{"status": "probed", "env": "normal", "inhabitants": "none", **traits})
    return w


def test_missions_need_a_probed_world_a_free_team_and_the_right_specialty():
    r = Rig()
    w = r.world(5)
    assert r.e.assign(w.id, "SG-2", "survey").endswith("MUST BE PROBED FIRST")
    w.status = "probed"
    w.options.append("contact")
    assert r.e.mission_types(w.id, "SG-2") == ["survey"] and r.e.mission_types(w.id, "SG-1") == ["survey", "contact"]
    assert "CAN'T RUN A CONTACT" in r.e.assign(w.id, "SG-2", "contact")
    assert r.e.assign(w.id, "SG-2", "survey").startswith("SG-2 ASSIGNED: SURVEY")
    assert r.e.assign(w.id, "SG-2", "survey") == "SG-2 IS NOT AVAILABLE"
    assert r.c.teams["SG-2"].status == "offworld" and r.c.teams["SG-2"].mission == 1 and r.c.record["missions"] == 1


def test_a_survey_departs_checks_in_comes_home_and_debriefs():
    r = Rig(CHECKIN, DEBRIEF)
    w = probed(r, hidden_names={"locals": "Tel'kar"}, drone="malp")
    r.e.assign(w.id, "SG-4", "survey")
    m = r.c.mission(1)
    hours = (m.end - m.start) // 60
    assert 20 <= hours <= 29
    r.e.advance(1)
    assert r.c.gate_until == r.c.now - 1 + clock.GATE_MINUTES["depart"]
    checkins = [e.due - m.start for e in r.c.events if e.kind == "checkin"]
    assert checkins == [8 * 60]
    r.e.advance(m.end - r.c.now + 1)
    assert m.state == "complete" and checked_in(r) == (m.end - m.start - 1) // 480
    assert r.c.teams["SG-4"].status == "base" and r.c.teams["SG-4"].xp == 1 and r.c.teams["SG-4"].mission is None
    assert w.status == "surveyed" and r.c.record["surveyed"] == 1 and w.name == "Tel'kar"
    assert "contact" in w.options and w.drone is None
    assert r.c.stock["malp"] == 5                          # 4, +1 brought home; nothing is free at midnight
    assert any("SG-4 CHECKS IN FROM" in line.upper() for line in r.logs)
    assert any("TEL'KAR" in f for f in m.findings) and w.last_visit == m.end


def test_recon_teams_check_in_every_six_hours_and_finish_sooner():
    r = Rig(CHECKIN)
    w = probed(r)
    r.e.assign(w.id, "SG-2", "survey")
    m = r.c.mission(1)
    assert m.end - m.start <= round(24 * 1.2 * 0.75 * 60)
    r.e.advance(1)
    assert [e.due - m.start for e in r.c.events if e.kind == "checkin"] == [6 * 60]


def test_several_missions_run_at_once():
    r = Rig(CHECKIN)
    worlds = [probed(r, i) for i in (3, 4, 5)]
    for w, team in zip(worlds, ("SG-1", "SG-3", "SG-4")):
        r.e.assign(w.id, team, "survey")
    r.e.advance(3 * clock.DAY)
    assert [m.state for m in r.c.missions] == ["complete"] * 3
    assert all(w.status == "surveyed" for w in worlds) and r.c.record["surveyed"] == 3


def missed(monkeypatch, r, danger_miss=100):
    monkeypatch.setattr(eng, "MISS", (danger_miss,) * 4)
    w = probed(r)
    r.e.assign(w.id, "SG-3", "survey")
    r.e.advance(8 * 60 + 2)
    return w, r.c.mission(1)


def test_a_missed_check_in_raises_an_alarm_and_the_default_sends_a_malp(monkeypatch):
    r = Rig(CHECKIN)
    monkeypatch.setitem(eng.SEARCH, "malp", (100, 100))
    w, m = missed(monkeypatch, r)
    assert r.alarms == ["MISSED CHECK-IN"] and checked_in(r) == 0 and r.e.prompt.title == "MISSED CHECK-IN"
    assert [label for label, _ in r.e.prompt.options] == ["Send a MALP to search", "Send the nearest available team",
                                                         "Wait 12 hours"]
    assert not r.c.events.find(lambda e: e.kind == "team_return")
    r.e.advance(180)
    assert any("STANDING ORDER: SEND A MALP" in line for line in r.logs) and r.c.stock["malp"] == 3
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    r.e.advance(61)
    assert any("FOUND ON" in line for line in r.logs) and w.drone == "malp"
    r.e.advance(2 * clock.DAY)
    assert m.state == "complete"


@pytest.mark.parametrize("roll,status,state", [((0, 100), "injured", "aborted"), ((0, 0), "captured", "captured")])
def test_a_malp_search_can_find_the_team_hurt_or_gone(monkeypatch, roll, status, state):
    r = Rig(CHECKIN)
    monkeypatch.setitem(eng.SEARCH, "malp", roll)
    w, m = missed(monkeypatch, r)
    r.e.key("1")
    r.e.advance(75)                                        # the gate frees up, then an hour for the MALP
    tm = r.c.teams["SG-3"]
    assert tm.status == status and m.state == state and tm.mission is None
    if status == "captured":
        assert tm.idc == "compromised" and tm.where == w.id
    else:
        assert m.casualties == 1


def test_sending_a_team_after_a_missing_one(monkeypatch):
    r = Rig(CHECKIN)
    monkeypatch.setitem(eng.SEARCH, "team", (100, 100))
    w, m = missed(monkeypatch, r)
    r.e.key("2")
    assert r.c.teams["SG-1"].status == "offworld" and r.c.teams["SG-1"].where == w.id
    r.e.advance(6 * 60 + 15)
    assert r.c.teams["SG-1"].status == "base" and any("SG-3 FOUND" in line for line in r.logs)


@pytest.mark.parametrize("order,roll,status", [("wait", 0, "offworld"), ("wait", 60, "captured"),
                                               ("wait", 95, "lost")])
def test_waiting_twelve_hours(monkeypatch, order, roll, status):
    r = Rig(CHECKIN)
    r.e.set_order("missed_checkin", order)
    w, m = missed(monkeypatch, r)
    r.e.advance(180)
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    monkeypatch.setattr(r.e.rng, "random", lambda: roll / 100)
    r.e.advance(12 * 60)
    assert r.c.teams["SG-3"].status == status


def test_under_fire_the_standing_order_recalls_the_team(monkeypatch):
    r = Rig(UNDER_FIRE)
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    w = probed(r)
    r.e.assign(w.id, "SG-3", "survey")
    r.e.advance(8 * 60 + 2)
    assert r.alarms == ["CHECK-IN"] and "taking fire" in r.e.prompt.text
    r.e.advance(180)
    m = r.c.mission(1)
    assert m.state == "aborted" and r.c.teams["SG-3"].status == "base" and w.status == "probed"
    assert any("HOME EARLY" in line for line in r.logs)


def test_under_fire_reinforcements(monkeypatch):
    r = Rig(UNDER_FIRE)
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    r.e.set_order("under_fire", "reinforce")
    w = probed(r)
    r.e.assign(w.id, "SG-3", "survey")
    r.e.advance(8 * 60 + 2 + 180)
    assert r.c.teams["SG-1"].status == "offworld" and r.c.mission(1).state == "active"
    r.e.advance(6 * 60)
    assert r.c.teams["SG-1"].status == "base"


def test_under_fire_holding_position_can_cost_the_team(monkeypatch):
    r = Rig(UNDER_FIRE)
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    r.e.set_order("under_fire", "hold")
    w = probed(r)
    r.e.assign(w.id, "SG-3", "survey")
    r.e.advance(8 * 60 + 2 + 180)
    assert r.c.mission(1).state == "captured" and r.c.teams["SG-3"].idc == "compromised"


def test_hostiles_can_follow_a_team_home(monkeypatch):
    r = Rig(FOLLOWED)
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    monkeypatch.setattr(eng, "FOLLOWED", 1.0)
    w = probed(r)
    w.inhabitants = "jaffa"
    r.e.assign(w.id, "SG-3", "survey")
    r.e.advance(clock.DAY + 12 * 60)
    assert "INCOMING" in r.alarms and "PERSONNEL -5" in r.logs
    assert r.c.teams["SG-3"].status == "base"


def test_the_scene_shows_where_teams_are():
    r = Rig(CHECKIN, director=True)
    w = probed(r)
    r.e.assign(w.id, "SG-2", "survey")
    r.e.update(0.1)
    assert r.d.scene.teams["SG-2"] == f"AWAY: {w.name.upper()}"


INCOMING = """
id = "t_unknown_m"
kind = "incoming"
[node.start]
text.full = "No IDC."
situation = "unknown_idc"
default = "closed"
[[node.start.choice]]
key = "closed"
label = "Keep the iris closed"
outcome = { end = true }
[[node.start.choice]]
key = "open_guarded"
label = "Open under guard"
outcome = { end = true }
"""

BACK_HOME = """
id = "t_back_home"
kind = "checkin"
[node.start]
text.full = "{team} walks back through the gate early."
default = "log"
[[node.start.choice]]
key = "log"
label = "Log it"
outcome = { effects = ["team {team} base"], end = true }
"""


def followed(monkeypatch, order):
    r = Rig(FOLLOWED, DEBRIEF, CHECKIN)
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    monkeypatch.setattr(eng, "FOLLOWED", 1.0)
    w = probed(r, inhabitants="jaffa", hidden_names={"locals": "Tel'kar"})
    r.e.set_order("hostiles_following", order)
    r.e.assign(w.id, "SG-3", "survey")
    m = r.c.mission(1)
    r.e.advance(m.end - r.c.now + 1)
    return r, w, m


def test_hostiles_following_come_first_and_the_team_waits_at_the_gate(monkeypatch):
    r, w, m = followed(monkeypatch, "open_briefly")
    tm = r.c.teams["SG-3"]
    assert r.alarms[-1] == "INCOMING" and r.c.alarms[0]["bind"]["mission"] == "1"
    assert tm.status == "offworld" and tm.mission == 1 and m.state == "active" and tm.xp == 0
    assert w.status == "probed" and not any("DEBRIEFED" in line for line in r.logs)
    assert r.e.assign(r.world(4, status="probed").id, "SG-3", "survey") == "SG-3 IS NOT AVAILABLE"
    b = Rig(FOLLOWED, DEBRIEF, CHECKIN, campaign=from_dict(json.loads(json.dumps(to_dict(r.c)))))
    assert b.e.prompt is not None and b.c.teams["SG-3"].status == "offworld"
    assert b.e.assign(b.world(4, status="probed").id, "SG-3", "survey") == "SG-3 IS NOT AVAILABLE"


def test_hostiles_following_opening_up_brings_the_team_home_then_debriefs(monkeypatch):
    r, w, m = followed(monkeypatch, "open_briefly")
    r.e.key("1")
    tm = r.c.teams["SG-3"]
    assert tm.status == "base" and tm.mission is None and tm.where == "" and tm.xp == 1
    assert m.state == "complete" and w.status == "surveyed" and r.c.record["surveyed"] == 1
    assert r.logs.index("PERSONNEL -5") < next(i for i, line in enumerate(r.logs) if "DEBRIEFED" in line)
    assert r.c.gate_until == r.c.now + clock.GATE_MINUTES["team_return"]      # the arrival itself holds the gate


def test_the_team_walking_in_never_shortens_a_longer_hold_on_the_gate(monkeypatch):
    r, w, m = followed(monkeypatch, "open_briefly")
    r.c.gate_until = r.c.now + 30                                               # something else holds the gate
    assert 30 > clock.GATE_MINUTES["team_return"]
    r.e.key("1")
    assert r.c.teams["SG-3"].status == "base" and r.c.gate_until == r.c.now + 30


def test_a_followed_recalled_team_comes_home_early_without_a_debrief(monkeypatch):
    r = Rig(FOLLOWED, DEBRIEF, CHECKIN)
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    monkeypatch.setattr(eng, "FOLLOWED", 1.0)
    w = probed(r, inhabitants="jaffa")
    r.e.assign(w.id, "SG-3", "survey")
    r.e.advance(60)
    m = r.c.mission(1)
    rules.recall(r.c, m)
    r.e.advance(1)
    assert r.c.alarms and r.c.teams["SG-3"].status == "offworld" and m.state == "aborted"
    r.e.key("1")
    tm = r.c.teams["SG-3"]
    assert tm.status == "base" and tm.mission is None and tm.xp == 0 and m.state == "aborted"
    assert any("HOME EARLY" in line for line in r.logs) and not any("DEBRIEFED" in line for line in r.logs)
    assert w.status == "probed"


def test_check_ins_due_together_go_in_the_order_they_came_due(monkeypatch):
    r = Rig(CHECKIN)
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    for i, team in zip((3, 4, 5, 6), ("SG-1", "SG-2", "SG-3", "SG-4")):
        r.e.assign(probed(r, i).id, team, "survey")
    r.e.advance(90)
    r.c.events.cancel(lambda e: e.kind == "checkin")
    t = r.c.now + 120
    r.c.events.push(t + 29, "checkin", {"mission": 4})      # pushed first, but due last
    for mid in (1, 2, 3):
        r.c.events.push(t, "checkin", {"mission": mid})
    r.c.events.push(t - 1, "incoming")                       # holds the gate until t + 29
    fired, orig = [], r.e._handlers["checkin"]
    r.e._handlers["checkin"] = lambda d: (fired.append((r.c.now - t, d["mission"])), orig(d))
    r.e.advance(200)
    assert fired == [(29, 1), (39, 2), (49, 3), (59, 4)]


def test_hostiles_following_keeping_the_iris_closed_loses_the_team_and_the_mission(monkeypatch):
    r, w, m = followed(monkeypatch, "keep_closed")
    r.e.advance(200)
    tm = r.c.teams["SG-3"]
    assert tm.status == "lost" and tm.xp == 0 and tm.mission is None and r.c.record["teams_lost"] == 1
    assert m.state == "lost" and m.casualties == 1 and w.status == "probed" and r.c.record["surveyed"] == 0
    assert not any("DEBRIEFED" in line for line in r.logs)
    assert not r.c.events.find(lambda e: e.data.get("mission") == 1)


def under_fire_at_the_last_check_in(monkeypatch):
    """SG-2 (recon, check-ins every 6 hours) is due home 19 hours out; the 18-hour check-in comes under fire."""
    r = Rig(CHECKIN)
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    w = probed(r)
    r.e.assign(w.id, "SG-2", "survey")
    m = r.c.mission(1)
    m.end = m.start + 19 * 60
    r.e.advance(13 * 60)
    r.e.scenarios = {**r.e.scenarios, **scen(UNDER_FIRE)}
    r.e.scenarios.pop("t_checkin")
    r.e.advance(m.start + 18 * 60 + 5 - r.c.now)
    assert r.c.alarms and r.c.alarms[0]["bind"]["mission"] == "1"
    return r, w, m


def test_a_team_waits_for_an_open_under_fire_decision_before_coming_home(monkeypatch):
    r, w, m = under_fire_at_the_last_check_in(monkeypatch)
    r.e.advance(70)                                        # past the due-home time, decision still open
    tm = r.c.teams["SG-2"]
    assert r.c.alarms and tm.status == "offworld" and m.state == "active" and tm.xp == 0
    r.e.key("2")                                           # hold position: captured
    assert tm.status == "captured" and m.state == "captured" and tm.where == w.id
    r.e.advance(60)
    assert tm.status == "captured" and not any("DEBRIEFED" in line for line in r.logs)


def test_an_under_fire_recall_after_the_due_time_still_brings_the_team_home(monkeypatch):
    r, w, m = under_fire_at_the_last_check_in(monkeypatch)
    r.e.advance(70)
    r.e.key("1")                                           # recall
    r.e.advance(60)
    tm = r.c.teams["SG-2"]
    assert tm.status == "base" and tm.mission is None and m.state == "aborted"


def test_no_check_in_is_scheduled_until_an_under_fire_decision_is_made(monkeypatch):
    r = Rig(UNDER_FIRE)
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    w = probed(r)
    r.e.assign(w.id, "SG-3", "survey")
    r.e.advance(8 * 60 + 2)
    assert r.c.alarms and not r.c.events.find(lambda e: e.kind == "checkin")
    r.e.advance(60)
    r.e.key("3")                                           # reinforce: SG-3 stays out
    nxt = r.c.events.find(lambda e: e.kind == "checkin")
    assert len(nxt) == 1 and nxt[0].due == r.c.now + 8 * 60


def test_a_stale_mission_alarm_is_withdrawn_when_it_comes_up_and_on_load(monkeypatch):
    r = Rig(UNDER_FIRE, INCOMING)
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    w = probed(r)
    r.e.assign(w.id, "SG-3", "survey")
    r.e.advance(clock.DAY + 12 * 60)
    m = r.c.mission(1)
    assert m.state == "aborted" and r.c.teams["SG-3"].status == "base" and not r.c.alarms
    stale = {"type": "node", "scenario": "t_fire", "node": "start", "bind": r.e._mbind(m), "deadline": None,
             "title": "CHECK-IN", "text": "Stale."}
    r.c.alarms.append(dict(stale))
    b = Rig(UNDER_FIRE, campaign=from_dict(json.loads(json.dumps(to_dict(r.c)))))
    assert b.c.alarms == [] and any("WITHDRAWN" in line for line in b.logs)
    r.c.alarms.clear()
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(1)
    r.c.alarms.append(dict(stale))
    r.e.key("1")
    assert r.c.alarms == [] and r.c.teams["SG-3"].status == "base" and any("WITHDRAWN" in line for line in r.logs)


def test_the_timeout_names_the_order_that_actually_ran(monkeypatch):
    r = Rig(CHECKIN)
    for t in ("SG-1", "SG-2", "SG-4"):
        r.c.teams[t].status = "injured"
        r.c.teams[t].until = r.c.now + 5 * clock.DAY
    r.e.set_order("missed_checkin", "team")
    w, m = missed(monkeypatch, r)
    r.c.stock["malp"] = 0
    r.e.advance(180)
    assert any("STANDING ORDER: WAIT 12 HOURS" in line for line in r.logs)
    assert any("WAITING 12 HOURS" in line for line in r.logs)


def test_a_malp_search_to_a_world_with_a_drone_keeps_the_malp(monkeypatch):
    r = Rig(CHECKIN)
    monkeypatch.setitem(eng.SEARCH, "malp", (100, 100))
    w, m = missed(monkeypatch, r)
    w.drone = "uav"
    before = r.c.stock["malp"]
    r.e.key("1")
    r.e.advance(75)
    assert w.drone == "uav" and r.c.stock["malp"] == before


def test_a_scenario_that_sends_the_team_home_clears_where_it_was(monkeypatch):
    r = Rig(BACK_HOME)
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    w = probed(r)
    r.e.assign(w.id, "SG-3", "survey")
    r.e.advance(8 * 60 + 2)
    tm = r.c.teams["SG-3"]
    assert tm.status == "base" and tm.where == "" and r.c.mission(1).state == "aborted"


def test_a_check_in_waits_at_most_thirty_minutes_behind_queued_dial_outs(monkeypatch):
    r = Rig(CHECKIN)
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    r.e.assign(probed(r).id, "SG-3", "survey")
    r.e.advance(1)
    m = r.c.mission(1)
    due = r.c.events.find(lambda e: e.kind == "checkin")[0].due
    r.e.advance(due - 5 - r.c.now)
    r.c.stock["malp"] = 6
    for i in range(6, 12):
        r.e.probe(probed(r, i).id)
    r.e.advance(1)                                        # the first probe takes the gate
    r.e.advance(due + 30 - r.c.now)
    assert checked_in(r) == 1                             # at due + 5, between the first and second probes
    assert len(r.c.events.find(lambda e: e.kind == "dial_out")) == 3


@pytest.mark.parametrize("stop", [1, 5 * 60, 8 * 60 + 5, 20 * 60, 30 * 60])
def test_saving_and_loading_mid_mission_gives_an_identical_future(monkeypatch, stop):
    monkeypatch.setattr(eng, "FOLLOWED", 0.5)
    monkeypatch.setattr(eng, "MISS", (10,) * 4)
    texts = (CHECKIN, UNDER_FIRE, DEBRIEF, FOLLOWED)
    a = Rig(*texts)
    a.e.assign(probed(a, inhabitants="jaffa").id, "SG-3", "survey")
    a.e.assign(probed(a, 4).id, "SG-2", "survey")
    a.e.advance(stop)
    a.e.save_now()
    b = Rig(*texts, campaign=from_dict(json.loads(json.dumps(a.saves[-1]))))
    assert b.c.alarms == a.c.alarms
    for r in (a, b):
        r.e.advance(3 * clock.DAY)
        r.e.save_now()
    assert a.saves[-1] == b.saves[-1]
    assert all(m.state != "active" for m in a.c.missions)
