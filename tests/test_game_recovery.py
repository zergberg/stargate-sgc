import pytest

from sgc.game import clock
from sgc.game import engine as eng
from sgc.game.database import Database
from sgc.game.room import Room
from tests.test_game_engine import Rig
from tests.test_game_missions import probed


@pytest.fixture(autouse=True)
def no_missed_checkins(monkeypatch):
    monkeypatch.setattr(eng, "MISS", (0,) * 4)


def mission_home(r, w, team="SG-2"):
    """Send a team on a survey of w and bring it home."""
    r.e.assign(w.id, team, "survey")
    m = r.c.mission(1)
    r.e.advance(m.end - r.c.now + clock.HOUR)
    assert r.c.teams[team].status == "base"
    return m


def test_a_team_brings_a_parked_drone_home():
    r = Rig()
    w = probed(r, drone="uav")
    stock = r.c.stock["uav"]
    m = mission_home(r, w)
    assert w.drone is None and r.c.stock["uav"] == stock + 1 and "SG-2 BROUGHT THE UAV HOME" in m.findings


@pytest.mark.parametrize("wreck,odds", [("crashed", 60), ("shot_down", 30)])
def test_the_salvage_odds_depend_on_how_the_uav_came_down(wreck, odds):
    assert eng.SALVAGE[wreck] == odds and eng.REPAIR == 30


def test_a_salvaged_wreck_is_repaired_for_half_a_uav(monkeypatch):
    monkeypatch.setitem(eng.SALVAGE, "shot_down", 100)
    r = Rig()
    w = probed(r, wreck="shot_down")
    r.c.funding, stock = 500, r.c.stock["uav"]
    m = mission_home(r, w)
    assert w.wreck is None and r.c.stock["uav"] == stock + 1 and r.c.funding == 470
    assert "UAV WRECK SALVAGED — REPAIRED FOR 30" in m.findings and "UAV WRECK SALVAGED — REPAIRED FOR 30" in r.logs


def test_a_wreck_that_cant_be_salvaged_is_written_off(monkeypatch):
    monkeypatch.setitem(eng.SALVAGE, "crashed", 0)
    r = Rig()
    w = probed(r, wreck="crashed")
    r.c.funding, stock = 500, r.c.stock["uav"]
    m = mission_home(r, w)
    assert w.wreck is None and r.c.stock["uav"] == stock and r.c.funding == 500
    assert "UAV WRECK WRITTEN OFF" in m.findings


def test_a_wreck_is_written_off_when_the_repair_cant_be_paid(monkeypatch):
    monkeypatch.setitem(eng.SALVAGE, "crashed", 100)
    r = Rig()
    w = probed(r, wreck="crashed")
    r.c.funding, stock = 10, r.c.stock["uav"]
    m = mission_home(r, w)
    assert w.wreck is None and r.c.stock["uav"] == stock and r.c.funding == 10
    assert "UAV WRECK WRITTEN OFF — NO FUNDS FOR REPAIR" in m.findings


def test_a_drone_and_a_wreck_on_one_world_both_come_home(monkeypatch):
    monkeypatch.setitem(eng.SALVAGE, "crashed", 100)
    r = Rig()
    w = probed(r, drone="malp", wreck="crashed")
    malps, uavs = r.c.stock["malp"], r.c.stock["uav"]
    mission_home(r, w)
    assert (w.drone, w.wreck) == (None, None) and r.c.stock["malp"] == malps + 1 and r.c.stock["uav"] == uavs + 1


def test_a_reinforcing_team_brings_nothing_home():
    r = Rig()
    w = probed(r, drone="malp", wreck="crashed")
    r.c.teams["SG-3"].status, r.c.teams["SG-3"].where = "offworld", w.id
    r.c.events.push(r.c.now, "team_return", {"team": "SG-3"})
    r.e.advance(clock.HOUR)
    assert r.c.teams["SG-3"].status == "base" and (w.drone, w.wreck) == ("malp", "crashed")


def test_the_assign_screen_shows_the_tandem_task():
    r = Rig()
    w = probed(r, drone="malp")
    w.options.append("contact")
    room = Room(r.e)
    room.world_id, room.team, room.screen = w.id, "SG-1", "type_pick"
    assert [label for label, _ in room.items()] == ["SURVEY · RECOVER MALP", "CONTACT · RECOVER MALP", "BACK"]
    w.drone, w.wreck = None, "shot_down"
    assert room.items()[1][0] == "CONTACT · SALVAGE UAV WRECK"
    w.wreck = None
    assert room.items()[0][0] == "SURVEY"


def test_the_database_shows_a_wreck():
    r = Rig()
    w = probed(r, wreck="crashed")
    db = Database(r.c)
    assert next(row for row in db.rows() if row.key == w.id).cells[5] == "WRECK"
    db.world_id = w.id
    assert "  UAV WRECK ON SITE" in db.detail()
    w.drone = "malp"
    assert next(row for row in db.rows() if row.key == w.id).cells[5] == "MALP"
    assert db.detail().index("  MALP ON SITE") + 1 == db.detail().index("  UAV WRECK ON SITE")
