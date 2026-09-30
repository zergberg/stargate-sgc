import json

from sgc.game import arcs, clock, rules, trade
from sgc.game import engine as eng
from sgc.game.state import from_dict, to_dict
from tests.test_game_engine import Rig

SCOUT = """
id = "t_scout"
kind = "faction"
stage = "curious"
visual = "incoming"
[node.start]
text.full = "Unscheduled activation. Jaffa loyal to {faction} are testing the iris."
situation = "unknown_idc"
default = "closed"
[[node.start.choice]]
key = "closed"
label = "Keep the iris closed"
outcome = { visual = "iris_hold", effects = ["security -3", "attention {faction} +5"], end = true }
[[node.start.choice]]
key = "open_guarded"
label = "Open for 30 seconds under guard"
outcome = { effects = ["reveal faction {faction} from jaffa"], end = true }
"""

AMBUSH = """
id = "t_ambush"
kind = "faction"
stage = "hostile"
team = "territory"
[node.start]
text.full = "{team} is ambushed on {world} by Jaffa loyal to {faction}."
default = "log"
[[node.start.choice]]
key = "log"
label = "Log it"
outcome = { effects = ["team {team} injured"], end = true }
"""

REPRISAL = """
id = "t_reprisal"
kind = "arc"
arc = "apophis"
arc_stage = 2
[node.start]
text.full = "Jaffa loyal to {faction} strike through the gate."
default = "hold"
[[node.start.choice]]
key = "hold"
label = "Hold the gate room"
outcome = { effects = ["security -5", "arc apophis advance"], end = true }
"""

FINALE = """
id = "t_finale"
kind = "arc"
arc = "apophis"
arc_stage = 3
[node.start]
text.full = "Apophis's fleet is broken."
default = "win"
[[node.start.choice]]
key = "win"
label = "Win"
outcome = { effects = ["arc apophis resolve"], end = true }
"""

DOOM = """
id = "t_doom"
kind = "arc"
arc = "apophis"
arc_stage = 1
[node.start]
text.full = "The ha'taks are overhead."
default = "fall"
[[node.start.choice]]
key = "fall"
label = "Brace"
outcome = { effects = ["arc apophis fail"], end = true }
"""


def actions(c, fid=None):
    return [e for e in c.events if e.kind == "faction_action" and fid in (None, e.data["faction"])]


def test_a_funding_review_pays_out_and_schedules_the_next():
    r = Rig()
    r.c.ledger["intel"] = 3
    r.e.advance(7 * clock.DAY)
    assert "FUNDING REVIEW: +330 — FUNDING 830" in r.logs and "BASE 300 · INTEL +30" in r.logs
    assert r.c.funding == 830
    assert [e.due for e in r.c.events if e.kind == "funding_review"] == [clock.START + 14 * clock.DAY]


def test_friendly_allies_share_addresses_at_reviews(monkeypatch):
    monkeypatch.setattr(eng, "SHARE_ODDS", 1.0)
    r = Rig()
    f = r.c.factions["tokra"]
    f.known, f.trust = True, 60
    n = len(r.c.worlds)
    r.e.advance(7 * clock.DAY)
    assert len(r.c.worlds) == n + 1 and any(line.startswith("THE TOK'RA SHARED AN ADDRESS") for line in r.logs)
    assert list(r.c.worlds.values())[-1].found == "intel from the Tok'ra"


def test_a_curious_goauld_acts_through_the_gate_without_being_named():
    r = Rig(SCOUT)
    rules.attention(r.c, "cronus", 25)
    [ev] = actions(r.c)
    r.e.advance(ev.due - r.c.now)
    assert r.alarms == ["INCOMING"] and "loyal to a Goa'uld" in r.e.prompt.text
    before = r.c.factions["cronus"].attention                                  # it has faded a little since
    r.e.key("1")
    assert r.c.factions["cronus"].attention == before + 5
    [nxt] = actions(r.c)
    assert 72 * 60 <= nxt.due - ev.due <= 120 * 60


def test_a_goauld_that_lost_interest_stops_acting():
    r = Rig(SCOUT)
    rules.attention(r.c, "cronus", 25)
    r.c.factions["cronus"].attention = 5
    [ev] = actions(r.c)
    r.e.advance(ev.due - r.c.now)
    assert r.alarms == [] and actions(r.c) == []


def test_a_hostile_goauld_ambushes_a_team_in_its_territory():
    r = Rig(AMBUSH)
    w = r.world(5, status="probed", owner="Sokar", inhabitants="jaffa", env="normal")
    r.e.assign(w.id, "SG-3", "survey")
    r.e.advance(1)
    r.c.factions["sokar"].attention = 60
    r.c.events.push(r.c.now, "faction_action", {"faction": "sokar"})
    r.e.advance(30)
    assert r.c.teams["SG-3"].status == "injured" and r.c.mission(1).state == "aborted"
    assert any(line.startswith(f"SG-3 is ambushed on {w.name}") for line in r.logs)
    assert len(actions(r.c, "sokar")) == 1


CAPTURE = AMBUSH.replace('id = "t_ambush"', 'id = "t_capture"').replace("injured", "captured")


def test_a_team_captured_in_the_territory_has_its_mission_closed():
    r = Rig(CAPTURE)
    w = r.world(5, status="probed", owner="Sokar", inhabitants="jaffa", env="normal")
    r.e.assign(w.id, "SG-3", "survey")
    r.e.advance(1)
    r.c.factions["sokar"].attention = 60
    r.c.events.push(r.c.now, "faction_action", {"faction": "sokar"})
    r.e.advance(30)
    tm, m = r.c.teams["SG-3"], r.c.mission(1)
    assert tm.status == "captured" and m.state == "captured" and tm.mission is None
    assert not r.c.events.find(lambda e: e.data.get("mission") == 1)
    assert f"SG-3 MISSION ON {w.name.upper()} ENDED: CAPTURED" in r.logs


def test_a_curious_goauld_that_isnt_known_yet_is_never_named_in_the_log():
    r = Rig(SCOUT)
    rules.attention(r.c, "cronus", 25)
    [ev] = actions(r.c)
    r.e.advance(ev.due - r.c.now)
    assert not any("CRONUS" in line.upper() for line in r.logs)


def test_no_team_in_the_territory_means_no_ambush():
    r = Rig(AMBUSH)
    r.c.factions["sokar"].attention = 60
    r.c.events.push(r.c.now, "faction_action", {"faction": "sokar"})
    r.e.advance(1)
    assert r.alarms == [] and len(actions(r.c, "sokar")) == 1


def test_a_delivery_comes_through_the_gate(monkeypatch):
    monkeypatch.setattr(trade, "disruption", lambda c, d: 0)
    r = Rig()
    w = r.world(5, status="contact", env="normal", inhabitants="human", owner=None)
    rules.apply_all([rules.parse_effect("deal {world} naquadah 2")], r.c, {"world_id": w.id})
    r.e.advance(72 * 60)
    assert r.c.naquadah == 2 and f"DELIVERY FROM {w.name.upper()}: 2 NAQUADAH" in r.logs
    assert r.c.gate_until == r.c.now + clock.GATE_MINUTES["trade_delivery"]


def test_an_arc_step_plays_its_stage_and_a_stale_step_does_nothing():
    r = Rig(REPRISAL)
    arcs.start(r.c, "apophis")
    arcs.advance(r.c, "apophis")
    r.e.advance(48 * 60)
    assert r.c.arcs["apophis"].stage == 3
    assert "Jaffa loyal to Apophis strike through the gate." in r.logs        # the arc's start made him known
    n = len(r.logs)
    r.c.events.push(r.c.now, "arc_step", {"arc": "apophis", "stage": 2})
    r.e.advance(1)
    assert len(r.logs) == n


def win(r):
    arcs.start(r.c, "apophis")
    arcs.advance(r.c, "apophis")
    arcs.advance(r.c, "apophis")
    r.c.events.push(r.c.now, "arc_step", {"arc": "apophis", "stage": 3})
    r.e.advance(1)


def test_resolving_the_major_arc_wins_and_staying_carries_on():
    r = Rig(FINALE)
    win(r)
    assert r.c.won is not None and r.victories == [r.c] and r.alarms[-1] == "VICTORY"
    assert [label for label, _ in r.e.prompt.options] == ["Stay in command", "Retire in victory"]
    r.e.key("1")
    assert not r.e.ended and r.c.over is None and r.e.prompt is None
    r.e.advance(clock.DAY)
    assert r.victories == [r.c]                                                # won once


def test_no_answer_means_staying_in_command():
    r = Rig(FINALE)
    win(r)
    r.e.advance(4 * 60)
    assert not r.e.ended and any("STANDING ORDER: STAY IN COMMAND" in line for line in r.logs)


def test_retiring_in_victory_ends_the_campaign_quietly():
    r = Rig(FINALE)
    win(r)
    r.e.key("2")
    assert r.e.ended and r.c.ending == "retired" and r.ended == [r.c] and r.e.prompt.title == "VICTORY"


def test_retiring_from_the_briefing_room():
    r = Rig()
    assert r.e.retire() == "COMMAND HANDED OVER"
    assert r.e.ended and r.c.ending == "retired" and r.e.prompt.title == "COMMAND HANDED OVER"
    assert r.ended == [r.c] and r.saves == [] and "Score:" in r.e.prompt.text
    assert r.e.retire() == "THE CAMPAIGN IS OVER"


THOR_END = """
id = "t_thor_end"
kind = "arc"
arc = "thor"
arc_stage = 1
[node.start]
text.full = "The Asgard have what they need."
default = "done"
[[node.start.choice]]
key = "done"
label = "Done"
outcome = { effects = ["arc thor resolve"], end = true }
"""


def test_a_minor_arc_resolved_alone_is_no_victory():
    r = Rig(THOR_END)
    arcs.start(r.c, "thor")
    r.c.events.push(r.c.now, "arc_step", {"arc": "thor", "stage": 1})
    r.e.advance(1)
    assert r.c.arcs["thor"].state == "resolved" and r.c.won is None and r.victories == []


def test_an_arc_catastrophe_means_earth_has_fallen():
    r = Rig(DOOM)
    arcs.start(r.c, "apophis")
    r.c.events.push(r.c.now, "arc_step", {"arc": "apophis", "stage": 1})
    r.e.advance(1)
    assert r.e.ended and r.c.ending == "fallen" and r.e.prompt.title == "EARTH HAS FALLEN"


def test_starting_fills_in_what_an_older_save_lacks():
    r = Rig()
    c = r.c
    c.events.cancel(lambda e: e.kind == "funding_review")
    c.factions["baal"].attention = 30
    c.minutes = clock.START + 9 * clock.DAY
    again = Rig(campaign=c)
    assert [e.due for e in again.c.events if e.kind == "funding_review"] == [clock.START + 14 * clock.DAY]
    assert len(actions(again.c, "baal")) == 1


def test_the_gate_panel_lists_teams_that_are_away_first():
    r = Rig(director=True)
    r.c.teams["SG-3"].status = "injured"
    r.c.teams["SG-3"].until = r.c.now + 600
    r.e.update(0.01)
    assert list(r.d.scene.teams)[0] == "SG-3"


def test_saving_mid_way_gives_the_same_future():
    a = Rig(SCOUT, REPRISAL)
    rules.attention(a.c, "cronus", 30)
    arcs.start(a.c, "apophis")
    arcs.advance(a.c, "apophis")
    w = a.world(5, status="contact", owner=None, inhabitants="human", env="normal")
    rules.apply_all([rules.parse_effect("deal {world} naquadah 2")], a.c, {"world_id": w.id})
    a.e.advance(30 * 60)
    a.e.save_now()
    b = Rig(SCOUT, REPRISAL, campaign=from_dict(json.loads(json.dumps(a.saves[-1]))))
    for r in (a, b):
        r.e.advance(10 * clock.DAY)
    assert to_dict(a.c) == to_dict(b.c)
