import json
import random
import tomllib

import pytest

from sgc import sequences as sq
from sgc.addresses import AddressPicker, load_canon
from sgc.config import Config
from sgc.director import Director
from sgc.events import REGISTRY
from sgc.game import clock
from sgc.game import engine as eng
from sgc.game.content import parse_scenario
from sgc.game.engine import Engine
from sgc.game.state import from_dict, new_campaign, to_dict

UNKNOWN = """
id = "t_unknown"
kind = "incoming"
goauld = "any"
visual = "incoming"
[node.start]
text.full = "No IDC. Jaffa, probably."
situation = "unknown_idc"
default = "closed"
[[node.start.choice]]
key = "closed"
label = "Keep the iris closed"
outcome = { visual = "iris_hold", effects = ["security -5"], end = true }
[[node.start.choice]]
key = "open_guarded"
label = "Open under guard"
outcome = { effects = ["personnel -10"], end = true }
[[node.start.choice]]
key = "locked"
label = "Call in our allies"
requires = ["ally.asgard"]
outcome = { end = true }
"""

DOOM = """
id = "t_doom"
kind = "incoming"
[node.start]
text.full = "It's a trap."
default = "open"
[[node.start.choice]]
key = "open"
label = "Open the iris"
outcome = { visual = "firefight", effects = ["game_over The Jaffa took the SGC."] }
[[node.start.choice]]
key = "hold"
label = "Hold"
outcome = { end = true }
"""

PROBE = """
id = "t_probe"
kind = "probe"
when = ["world {world} env normal"]
[node.start]
text.full = "Telemetry from {designation}: green across the board."
default = "log"
[[node.start.choice]]
key = "log"
label = "Log it"
outcome = { effects = ["reveal name {world} from ruins"], end = true }
"""


def scen(*texts):
    out = {}
    for i, t in enumerate(texts):
        sc = parse_scenario(tomllib.loads(t), f"t{i}.toml")
        out[sc.id] = sc
    return out


class Rig:
    def __init__(self, *texts, campaign=None, director=False, pace=None, difficulty="officer"):
        self.c = campaign or new_campaign("campaign", difficulty, 7)
        if campaign is None:
            self.c.events.cancel(lambda e: e.kind == "incoming")      # tests trigger incoming themselves
            self.c.stock["uav"] = 2
            self.c.upgrades.add("uav_program")
        self.saves, self.ended, self.logs, self.alarms, self.victories = [], [], [], [], []
        self.d = Director(Config(), random.Random(1), AddressPicker(load_canon(), 0.6, random.Random(1)),
                          REGISTRY) if director else None
        self.e = Engine(self.c, scen(*texts), self.d, pace_override=pace,
                        save=lambda c: self.saves.append(to_dict(c)), on_end=self.ended.append,
                        log=self.logs.append, on_alarm=lambda t, x: self.alarms.append(t),
                        on_victory=self.victories.append)

    def world(self, i=5, **traits):
        w = list(self.c.worlds.values())[i]
        for k, v in traits.items():
            setattr(w, k, v)
        return w


def test_the_clock_runs_by_pace_and_recovers_the_base():
    r = Rig()
    r.c.meters["security"] = 50
    r.e.update(60)                                        # standard: a real minute is a game hour
    assert r.c.minutes == 540 and r.c.meters["security"] == 50
    r.e.advance(5 * 60)                                   # 14:00 is 6-hourly
    assert r.c.meters["security"] == 51 and r.c.events.peek().kind == "recovery_tick"
    fast = Rig(pace=10)
    fast.e.update(10)
    assert fast.c.minutes == 540


def test_the_gate_is_one_resource():
    r = Rig(UNKNOWN)
    r.c.gate_until = r.c.now + 20
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(10)
    assert r.alarms == []
    r.e.advance(15)
    assert r.alarms == ["INCOMING"] and r.c.gate_until == r.c.now + clock.GATE_MINUTES["incoming"] - 5


def test_probe_to_telemetry_report():
    r = Rig(PROBE)
    w = r.world(env="normal", inhabitants="none", features=("ruins",), hidden_names={"locals": "Tel'kar"})
    assert r.e.probe(w.id) == f"MALP QUEUED FOR {w.id}" and r.c.stock["malp"] == 3
    assert r.e.probe(w.id).startswith("A DRONE IS ALREADY BOUND")
    r.e.advance(1)
    assert r.c.gate_until == r.c.now - 1 + clock.GATE_MINUTES["probe"]
    assert any("MALP SENT TO" in line for line in r.logs)
    r.e.advance(clock.GATE_MINUTES["probe"])                  # the gate shuts: the drone reported live
    assert w.status == "probed" and w.drone == "malp" and w.seen["env"] == "breathable atmosphere"
    assert w.seen["features"] == "ruins" and w.last_visit is not None
    assert any("green across the board" in text for _, text in w.reports)
    assert w.name == "Tel'kar" and w.names[0][1] == "inscriptions in the ruins"
    assert r.c.record["probes"] == 1 and r.e.probe(w.id).startswith("A MALP IS ALREADY ON")


def test_no_lock_marks_the_world_lost_and_keeps_the_malp():
    r = Rig()
    w = r.world(env="no_lock")
    r.e.probe(w.id)
    r.e.advance(1)
    assert w.status == "lost" and r.c.stock["malp"] == 4 and any("NO LOCK" in line for line in r.logs)


@pytest.mark.parametrize("table,key,status,drone", [("DESTROYED", "extreme", "probed", None),
                                                    ("CAPTURED", "jaffa", "hostile", None)])
def test_probes_can_be_destroyed_or_captured(monkeypatch, table, key, status, drone):
    monkeypatch.setitem(getattr(eng, table), key, 100)
    r = Rig()
    w = r.world(env="extreme" if key == "extreme" else "normal", inhabitants=key if key == "jaffa" else "none")
    r.e.probe(w.id)
    r.e.advance(200)
    assert w.status == status and w.drone is drone and r.c.stock["malp"] == 3


def test_a_uav_sees_more_and_recall_brings_a_drone_home():
    r = Rig(difficulty="recruit")
    w = r.world(env="normal", inhabitants="human")
    r.e.send_uav(w.id)
    r.e.advance(120)
    assert w.drone == "uav" and w.seen["inhabitants"] == "settlement" and "count" in w.seen
    assert r.c.stock["uav"] == 1
    assert r.e.recall_drone(w.id).startswith("RECALL QUEUED")
    r.e.advance(1)
    assert w.drone is None and r.c.stock["uav"] == 2 and r.c.gate_until == r.c.now - 1 + 30
    assert r.e.recall_drone(w.id).startswith("NO DRONE")


def test_an_alarm_waits_three_game_hours_then_the_default_order_runs():
    r = Rig(UNKNOWN)
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(1)
    p = r.e.prompt
    assert r.alarms == ["INCOMING"] and p.title == "INCOMING" and "Jaffa" in p.text
    assert [ok for _, ok in p.options] == [True, True, False] and p.total == 180
    assert r.e.alarm_title == "INCOMING"
    r.e.advance(178)
    assert r.e.prompt is not None and r.c.meters["security"] == 70
    r.e.advance(2)
    assert r.e.prompt is None and r.c.meters["security"] == 65
    assert any("STANDING ORDER: KEEP THE IRIS CLOSED" in line for line in r.logs)


def test_a_standing_order_changes_what_runs_on_timeout():
    r = Rig(UNKNOWN)
    r.e.set_order("unknown_idc", "open_guarded")
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(200)
    assert r.c.meters["personnel"] == 70 and r.c.meters["security"] == 70


def test_the_window_is_at_least_a_real_minute():
    r = Rig(UNKNOWN, pace=10)
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(1)
    assert r.e.prompt.total == 60 and r.c.alarms[0]["deadline"] == 480 + 360


def test_keys_pick_enabled_choices_only_and_answers_save():
    r = Rig(UNKNOWN)
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(1)
    assert r.e.key("3") and r.e.prompt is not None
    assert not r.e.key("x")
    r.e.key("2")
    assert r.e.prompt is None and r.c.meters["personnel"] == 70 and r.saves[-1]["meters"]["personnel"] == 70


def test_alarms_queue_and_each_gets_its_own_window():
    r = Rig(UNKNOWN)
    r.c.events.push(r.c.now, "incoming")
    r.c.events.push(r.c.now + 60, "incoming")
    r.e.advance(61)
    assert len(r.c.alarms) == 2 and r.c.alarms[1]["deadline"] is None
    r.e.key("1")
    assert r.c.alarms[0]["deadline"] == r.c.now + 180


def test_game_over_ends_the_campaign():
    r = Rig(DOOM)
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(1)
    r.e.key("1")
    assert r.c.over == "The Jaffa took the SGC." and r.ended == [r.c] and r.e.ended
    assert r.e.prompt.title == "BASE OVERRUN" and r.c.alarms == []
    n = len(r.saves)
    r.e.save_now()
    r.e.advance(600)
    assert len(r.saves) == n and r.e.probe(r.world().id) == "THE CAMPAIGN IS OVER"
    r.e.key("1")
    assert r.e.finished


def test_random_incoming_reschedules_itself():
    r = Rig(UNKNOWN)
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(1)
    nxt = r.c.events.find(lambda e: e.kind == "incoming")
    assert len(nxt) == 1 and 36 * 60 <= nxt[0].due - (r.c.now - 1) <= 96 * 60


def test_saving_and_loading_mid_flight_gives_an_identical_future():
    a = Rig(UNKNOWN, PROBE)
    for i in (3, 4, 5):
        a.e.probe(a.world(i).id)
    a.c.events.push(a.c.now + 30, "incoming")
    a.e.advance(45)                                      # an alarm is open, drones are in flight
    a.e.save_now()
    b = Rig(UNKNOWN, PROBE, campaign=from_dict(json.loads(json.dumps(a.saves[-1]))))
    assert b.e.prompt is not None and b.e.prompt.text == a.e.prompt.text
    for r in (a, b):
        r.e.advance(3 * 24 * 60)
        r.e.save_now()
    assert a.saves[-1] == b.saves[-1]


def test_campaign_randomness_does_not_depend_on_the_frame_rate():
    a, b = Rig(UNKNOWN, PROBE), Rig(UNKNOWN, PROBE)
    for r in (a, b):
        r.c.events.push(r.c.now + 100, "incoming")
        r.e.probe(r.world(4).id)
    for _ in range(600):
        a.e.update(1.0)
    for _ in range(2400):
        b.e.update(0.25)
    a.e.save_now()
    b.e.save_now()
    da, db = a.saves[-1], b.saves[-1]
    da["minutes"] = db["minutes"] = 0
    assert da == db


def play(r, seconds, dt):
    """The app's frame: the director, then the engine. Returns (game minute, line) for both logs, in order."""
    out = []
    for _ in range(round(seconds / dt)):
        out += [(r.c.minutes, line) for line in r.d.advance(dt)[0]]
        seen = len(r.logs)
        r.e.update(dt)
        out += [(r.c.minutes, line) for line in r.logs[seen:]]
    return out


def test_at_a_busy_pace_a_result_waits_for_the_gate_to_finish_showing_it():
    r = Rig(PROBE, director=True, pace=10)                # 10 real seconds a game hour: a dial outlasts the trip
    w = r.world(env="normal", inhabitants="none")
    name = w.name.upper()                                 # the ruins may name it once the telemetry is in
    r.e.probe(w.id)
    lines = [line for _, line in play(r, 120, 0.1)]
    transit = lines.index("MALP IN TRANSIT")
    telemetry = lines.index(f"MALP TELEMETRY FROM {name}")
    assert transit < lines.index("TELEMETRY RECEIVED") < telemetry        # logged once the feed has played
    assert w.status == "probed"


def test_the_clock_is_not_held_by_the_ambient_scene_or_without_a_director():
    r = ambient_rig()
    before = r.c.minutes
    r.c.events.push(r.c.now + 1, "recovery_tick")
    r.e.update(10)
    assert r.c.minutes == before + 10


def test_holding_for_the_gate_does_not_change_the_campaign_at_any_frame_rate():
    a, b = Rig(UNKNOWN, PROBE, director=True, pace=10), Rig(UNKNOWN, PROBE, director=True, pace=10)
    for r in (a, b):
        r.c.events.push(r.c.now + 100, "incoming")
        for i in (3, 4, 5):
            r.e.probe(r.world(i).id)
    play(a, 300, 1 / 30)
    play(b, 300, 1 / 7)
    for r in (a, b):
        r.e.advance(clock.START + 2 * clock.DAY - r.c.minutes)      # the rest headless, to the same minute
        r.e.save_now()
    assert a.saves[-1] == b.saves[-1]


def test_the_director_plays_the_gate_and_follows_the_teams():
    r = Rig(UNKNOWN, director=True)
    assert r.d.auto is False
    r.c.events.push(r.c.now, "incoming")
    r.e.update(1)
    assert not r.d.idle and r.d.scene.prompt is r.e.prompt
    r.c.teams["SG-2"].status = "captured"
    r.e.update(0.1)
    assert r.d.scene.teams["SG-2"] == "CAPTURED"


def test_the_gate_panel_uses_the_database_words_without_the_time_left():
    from sgc.game.database import team_status
    r = Rig(UNKNOWN, director=True)
    c, w = r.c, next(iter(r.c.worlds.values()))
    c.teams["SG-1"].status, c.teams["SG-1"].where = "offworld", w.id
    c.teams["SG-2"].until = c.now + 11 * 60
    c.teams["SG-3"].status, c.teams["SG-3"].until = "injured", c.now + 44 * 60
    c.teams["SG-4"].status, c.teams["SG-4"].until = "lost", c.now + 53 * 60
    r.e.update(0.1)
    assert r.d.scene.teams == {"SG-1": f"AWAY: {w.name.upper()}", "SG-2": "STOOD DOWN", "SG-3": "INJURED",
                               "SG-4": "RE-FORMING"}
    for name in ("SG-2", "SG-3", "SG-4"):
        assert team_status(c, name).startswith(r.d.scene.teams[name] + " ")
    c.teams["SG-2"].until, c.teams["SG-4"].until = 0, 0
    r.e.update(0.1)
    assert r.d.scene.teams["SG-2"] == "BASE" == team_status(c, "SG-2")
    assert r.d.scene.teams["SG-4"] == "LOST" == team_status(c, "SG-4")


def test_a_pending_alarm_whose_scenario_is_gone_is_withdrawn_on_load():
    a = Rig(UNKNOWN)
    a.c.events.push(a.c.now, "incoming")
    a.e.advance(1)
    b = Rig(campaign=from_dict(json.loads(json.dumps(to_dict(a.c)))))
    assert b.e.prompt is None and b.c.alarms == [] and any("WITHDRAWN" in line for line in b.logs)


CHAIN = """
id = "t_chain"
kind = "incoming"
[node.start]
text.full = "Someone is dialing in."
situation = "unknown_idc"
default = "closed"
[[node.start.choice]]
key = "closed"
label = "Keep the iris closed"
outcome = { goto = "second" }
[[node.start.choice]]
key = "open_guarded"
label = "Open under guard"
outcome = { end = true }
[node.second]
text.full = "They're transmitting a message."
default = "listen"
[[node.second.choice]]
key = "listen"
label = "Listen"
outcome = { end = true }
[[node.second.choice]]
key = "ignore"
label = "Ignore it"
outcome = { end = true }
"""

QUIET_DOOM = """
id = "t_quiet_doom"
kind = "incoming"
[node.start]
text.full = "The self-destruct goes off."
default = "boom"
[[node.start.choice]]
key = "boom"
label = "Boom"
outcome = { effects = ["game_over The SGC is gone."], end = true }
"""


def normal_worlds(r, n):
    return [r.world(i, env="normal", inhabitants="none") for i in range(3, 3 + n)]


def test_a_follow_up_node_goes_to_the_front_of_the_queue_without_ringing_again():
    r = Rig(CHAIN, UNKNOWN)
    r.e.scenarios = {k: v for k, v in r.e.scenarios.items() if k == "t_chain"}
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(1)
    r.e.scenarios.update(scen(UNKNOWN))
    r.e.scenarios.pop("t_chain")
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(31)                                      # the gate frees after the first wormhole
    r.e.scenarios.update(scen(CHAIN))
    assert [a["scenario"] for a in r.c.alarms] == ["t_chain", "t_unknown"] and len(r.alarms) == 2
    r.e.key("1")
    assert [(a["scenario"], a["node"]) for a in r.c.alarms] == [("t_chain", "second"), ("t_unknown", "start")]
    assert len(r.alarms) == 2 and r.e.prompt.text == "They're transmitting a message."
    assert r.c.alarms[0]["deadline"] == r.c.now + 180


def test_a_game_over_is_never_saved_and_a_fallen_base_loads_straight_to_game_over():
    r = Rig(QUIET_DOOM)
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(1)
    assert r.e.ended and r.c.over and all(s["over"] is None for s in r.saves)
    d = to_dict(r.c)
    b = Rig(QUIET_DOOM, campaign=from_dict(json.loads(json.dumps(d))))
    assert b.e.ended and b.ended == [b.c] and b.e.prompt.title == "BASE OVERRUN" and b.saves == []


def test_changing_pace_with_an_alarm_open_keeps_the_countdown_within_its_bar():
    r = Rig(UNKNOWN)
    r.c.pace = "busy"
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(1)
    assert r.c.alarms[0]["deadline"] == r.c.now - 1 + 360
    r.e.set_pace("standard")
    assert r.c.alarms[0]["deadline"] == r.c.now + 180
    r.e.update(0)
    assert r.e.prompt.total == 180 and r.e.prompt.remaining == 180
    r.e.update(5.0)
    assert r.e.prompt.remaining == pytest.approx(175)


def test_a_timed_out_alarm_that_can_no_longer_be_answered_is_withdrawn():
    r = Rig(UNKNOWN)
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(1)
    r.e.scenarios.clear()
    r.e.advance(200)
    assert r.c.alarms == [] and r.e.prompt is None and any("WITHDRAWN" in line for line in r.logs)


@pytest.mark.parametrize("alarm", [
    {"type": "mystery", "title": "T", "text": "X", "deadline": None},
    {"type": "node", "title": "T", "text": "X", "deadline": None, "bind": {}, "scenario": "t_unknown", "node": "gone"},
])
def test_an_alarm_that_cant_be_shown_is_withdrawn_on_load(alarm):
    a = Rig(UNKNOWN)
    a.c.alarms.append(alarm)
    b = Rig(UNKNOWN, campaign=from_dict(json.loads(json.dumps(to_dict(a.c)))))
    assert b.c.alarms == [] and b.e.prompt is None and any("WITHDRAWN" in line for line in b.logs)


def test_inbound_traffic_goes_before_queued_dial_outs():
    r = Rig(UNKNOWN)
    r.c.stock["malp"] = 6
    for w in normal_worlds(r, 6):
        r.e.probe(w.id)
    r.c.events.push(r.c.now + 1, "incoming")
    r.e.advance(16)                                      # the first dial-out holds the gate for 15 minutes
    assert r.alarms == ["INCOMING"]
    assert len(r.c.events.find(lambda e: e.kind == "dial_out")) == 5


def test_redialing_a_world_with_no_lock_counts_one_probe():
    r = Rig()
    w = r.world(env="no_lock")
    r.e.probe(w.id)
    r.e.advance(1)
    r.e.probe(w.id)
    r.e.advance(1)
    assert w.status == "lost" and r.c.stock["malp"] == 4 and r.c.record["probes"] == 1


def test_a_quiet_gate_plays_an_ambient_scene_to_a_known_world_without_touching_the_campaign():
    r = Rig(director=True)
    before, state = to_dict(r.c), r.e.rng.getstate()
    assert r.d.idle
    r.e._idle_scene(eng.IDLE_SCENE - 1)
    assert r.d.idle
    r.e._idle_scene(1)
    assert not r.d.idle
    for _ in range(40):
        r.d.advance(1.0)
    known = {w.name.upper() for w in r.c.worlds.values() if w.status in ("probed", "surveyed", "contact")}
    assert r.d.scene.panel_title.startswith("SCIENCE · ") and r.d.scene.panel_title[10:] in known
    assert to_dict(r.c) == before and r.e.rng.getstate() == state


def ambient_rig(*texts):
    r = Rig(*texts, director=True)
    r.e._idle_scene(eng.IDLE_SCENE)
    assert r.e.ambient and not r.d.idle
    for _ in range(10):
        r.d.advance(1.0)                                  # the uplink is dialling
    return r


def director_logs(r, seconds):
    logs = []
    for _ in range(int(seconds * 10)):
        logs += r.d.advance(0.1)[0]
    return logs


def test_an_alarm_cuts_the_ambient_scene_at_once():
    r = ambient_rig(UNKNOWN)
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(1)
    assert not r.e.ambient
    assert "UNSCHEDULED OFFWORLD ACTIVATION" in director_logs(r, 5)


def test_routine_traffic_cuts_the_ambient_scene_instead_of_being_dropped():
    r = ambient_rig()
    r.e._show(r.e._v_checkin("SG-2"))
    assert not r.e.ambient
    assert "IDC RECEIVED — SG-2" in director_logs(r, 10)


def test_the_ambient_flag_clears_when_the_scene_ends():
    r = ambient_rig()
    for _ in range(80):
        r.d.advance(1.0)
    assert r.d.idle and r.e.ambient
    r.e.update(0)
    assert not r.e.ambient


@pytest.mark.parametrize("why", ["alarm", "gate", "no_worlds"])
def test_no_ambient_scene_while_an_alarm_is_open_the_gate_is_busy_or_nothing_is_known(why):
    r = Rig(UNKNOWN, director=True)
    if why == "alarm":
        r.c.events.push(r.c.now, "incoming")
        r.e.advance(1)
        r.d.skip(log=None)
        for _ in range(20):
            r.d.advance(1.0)
        assert r.c.alarms and r.d.idle
    elif why == "gate":
        r.c.gate_until = r.c.now + 30
    else:
        for w in r.c.worlds.values():
            w.status = "unexplored"
    r.e._idle_scene(eng.IDLE_SCENE + 1)
    assert r.d.idle and not r.e.ambient


def test_an_order_for_an_unknown_situation_is_rejected():
    r = Rig()
    with pytest.raises(ValueError):
        r.e.set_order("tea_time", "earl_grey")


def test_a_walk_is_not_gate_traffic_and_traffic_due_during_it_waits_behind_it():
    r = Rig(PROBE, director=True, pace=10)
    w = r.world(env="normal", inhabitants="none")
    r.d.run_steps(sq.to_briefing(10.0))                   # the app's walk between the rooms
    assert not r.e.showing
    r.e.probe(w.id)
    before = r.c.minutes
    r.e.update(1.0)
    assert r.c.minutes == pytest.approx(before + clock.to_minutes(1.0, r.e.sph))      # the clock ran on
    assert any("MALP SENT TO" in line for line in r.logs)
    assert r.e.showing                                    # the drone's scene is queued behind the walk
    assert "MALP IN TRANSIT" in director_logs(r, 40)


def test_real_traffic_still_holds_the_clock_until_it_has_played():
    r = Rig(PROBE, director=True, pace=10)
    w = r.world(env="normal", inhabitants="none")
    r.e.probe(w.id)
    r.e.update(0.1)
    assert r.e.showing
    due = r.c.now + 1
    r.c.events.push(due, "incoming")
    r.e.update(5.0)
    assert r.c.minutes == due and r.c.events.peek().kind == "incoming"       # held at the next thing due
    director_logs(r, 60)
    r.e.update(0.1)
    assert not r.e.showing and r.c.minutes > due


def test_the_ambient_scene_and_its_cut_are_not_traffic():
    r = ambient_rig()
    assert not r.e.showing
    r.e.cut_ambient()
    assert not r.d.idle and not r.e.showing


def test_an_alarm_with_nothing_to_show_still_cuts_the_ambient_scene():
    r = ambient_rig()
    r.e._raise({"type": "missed_checkin", "mission": 99, "deadline": None, "title": "MISSED CHECK-IN",
                "text": "Nobody."})
    assert not r.e.ambient


def test_a_failed_save_is_logged_once_per_streak_and_play_goes_on():
    r = Rig()
    broken = [True]

    def save(c):
        if broken[0]:
            raise PermissionError(13, "Permission denied")
        r.saves.append(to_dict(c))
    r.e._save = save
    r.e.save_now()
    r.e.update(60 * 5)                                     # hours of game time, trying every five minutes
    assert [line for line in r.logs if "SAVE" in line] == ["SAVE FAILED — PERMISSION DENIED"]
    broken[0] = False
    r.e.save_now()
    r.e.save_now()
    assert [line for line in r.logs if "SAVE" in line][1:] == ["SAVE OK"] and r.saves
    broken[0] = True
    r.e.save_now()
    assert [line for line in r.logs if "SAVE" in line][2:] == ["SAVE FAILED — PERMISSION DENIED"]


def test_one_tuple_of_text_levels_and_no_dead_engine_attributes():
    from sgc.game import content, world
    r = Rig(UNKNOWN)
    assert not hasattr(r.e, "transition") and not hasattr(world, "DETAILS") and not hasattr(eng, "INFO_LEVELS")
    assert set(eng.DETAIL.values()) == set(content.TEXT_LEVELS)
