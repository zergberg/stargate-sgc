import json

from sgc.game import clock, planner, rules
from sgc.game.database import Database, team_status
from sgc.game.engine import team_label
from sgc.game.room import Room
from sgc.game.state import available_teams, from_dict, to_dict
from tests.test_game_engine import Rig
from tests.test_game_engine_events import AMBUSH
from tests.test_game_missions import probed


def staged(r, team="SG-2", **traits):
    """A team assigned while the gate is busy: it stages until the gate is free."""
    w = probed(r, **traits)
    r.c.gate_until = r.c.now + 30
    r.e.assign(w.id, team, "survey")
    return w, r.c.teams[team]


def test_an_assigned_team_stages_until_the_gate_takes_it():
    r = Rig()
    w, tm = staged(r)
    assert (tm.status, tm.where, tm.mission) == ("staging", w.id, 1)
    assert "SG-2" not in available_teams(r.c) and r.e.assign(w.id, "SG-2", "survey") == "SG-2 IS NOT AVAILABLE"
    r.e.advance(29)
    assert tm.status == "staging"
    r.e.advance(1)
    assert tm.status == "offworld" and f"SG-2 DEPARTING FOR {w.name.upper()}" in r.logs


def test_cancelling_the_departure_returns_a_staging_team_to_base():
    r = Rig()
    w, tm = staged(r)
    r.e.cancel(r.e.schedule_view()[0].id, confirm=True)
    assert (tm.status, tm.where, tm.mission) == ("base", "", None) and "SG-2" in available_teams(r.c)


def test_the_labels_say_staging_and_where_it_is_going():
    r = Rig()
    w, tm = staged(r)
    assert team_label(r.c, "SG-2") == f"STAGING: {w.name}"
    assert team_status(r.c, "SG-2") == "STAGING"
    db = Database(r.c)
    db.tab = "teams"
    row = next(row for row in db.rows() if row.key == "SG-2")
    assert row.cells[3] == "STAGING" and row.cells[4] == "SGC"


def test_the_assign_screen_says_who_is_staging_and_for_where():
    r = Rig()
    w, tm = staged(r)
    room = Room(r.e)
    room.world_id, room.screen = w.id, "team_pick"
    i = list(r.c.teams).index("SG-2")
    assert room.items()[i][1] is False
    room.key(str(i + 1))
    assert room.notice == f"SG-2 IS STAGING FOR {w.name.upper()}"


def test_a_team_hurt_while_staging_never_departs():
    r = Rig()
    w, tm = staged(r)
    tm.status, tm.until = "injured", r.c.now + clock.DAY
    r.e.advance(30)
    assert tm.status == "injured" and r.c.mission(1).state == "aborted" and tm.mission is None
    assert not any("DEPARTING" in line for line in r.logs)


def test_an_old_save_with_a_queued_departure_loads_as_staging():
    r = Rig()
    w, tm = staged(r)
    d = to_dict(r.c)
    d["teams"]["SG-2"]["status"] = "offworld"            # how a save from before STAGING recorded it
    back = from_dict(json.loads(json.dumps(d)))
    assert back.teams["SG-2"].status == "staging"
    r.e.advance(30)
    assert from_dict(to_dict(r.c)).teams["SG-2"].status == "offworld"      # gone through: stays offworld


def test_a_staging_team_is_never_ambushed_or_flagged_on_the_world():
    r = Rig(AMBUSH)
    w, tm = staged(r, owner="Sokar", inhabitants="jaffa")
    assert rules.teams_matching(r.c, "territory", {"faction_id": "sokar"}) == []
    assert not any(row.cells[4] == "T" for row in Database(r.c).rows() if row.key == w.id)
    r.c.factions["sokar"].attention = 60
    r.c.events.push(r.c.now, "faction_action", {"faction": "sokar"})
    r.e.advance(30)                                        # the ambush goes first, while SG-2 still stages
    assert not any("ambushed" in line for line in r.logs) and tm.status == "offworld"


def test_a_staging_team_recalled_stands_by_at_base():
    r = Rig()
    w, tm = staged(r)
    assert rules.recall(r.c, r.c.mission(1)) == []
    assert (tm.status, tm.mission) == ("base", None) and r.c.mission(1).state == "aborted"
    r.e.advance(clock.HOUR)
    assert tm.status == "base" and not any("DEPARTING" in line for line in r.logs)


def test_the_planner_never_sends_a_staging_team_again_and_its_mission_is_not_in_the_field():
    r = Rig()
    w, tm = staged(r)
    out = planner.step(r.e)
    assert not any(line.startswith("SG-2 ASSIGNED") for line in out) and tm.mission == 1
    assert not any(i.kind == "mission" for i in r.e.schedule_view())
