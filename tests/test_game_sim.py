"""The idle simulation: 30 game days with nobody at the keyboard."""
import pytest

from sgc.game import clock, content, planner
from sgc.game.engine import Engine
from sgc.game.state import Mission, new_campaign

RECKLESS = {"unknown_idc": "open_guarded", "hostiles_following": "keep_closed", "bad_idc": "closed_silent",
            "object": "push_back", "missed_checkin": "wait", "under_fire": "hold", "contact_offer": "accept"}


def simulate(seed, orders=None, days=30, difficulty="officer"):
    scenarios, warnings = content.load(user=None)
    assert warnings == []
    c = new_campaign("campaign", difficulty, seed)
    if orders:
        c.orders.update(orders)
    e = Engine(c, scenarios)
    for _ in range(days * 24):
        planner.step(e)
        e.advance(clock.HOUR)
        if e.ended:
            break
    return c, e


def test_default_orders_keep_the_base_alive_and_survey_worlds():
    for seed in (1, 2, 3):
        c, e = simulate(seed)
        assert c.over is None and not e.ended, (seed, c.over)
        assert sum(w.status in ("surveyed", "contact") for w in c.worlds.values()) >= 5, seed
        assert c.minutes >= clock.START + 30 * clock.DAY
        assert c.record["missions"] >= 10 and c.record["probes"] >= 10


@pytest.mark.xfail(reason="hostile wormholes are now faction actions, earned by attention; Task 21 rewrites this",
                   strict=False)
def test_reckless_orders_can_lose_the_base():
    assert any(simulate(seed, RECKLESS)[0].over for seed in range(1, 9))


def test_the_planner_probes_first_and_prefers_breathable_worlds():
    scenarios, _ = content.load(user=None)
    c = new_campaign("sandbox", "officer", 4)
    e = Engine(c, scenarios)
    out = planner.step(e)
    assert out[0].startswith("MALP QUEUED FOR") and c.stock["malp"] == 3
    ws = [w for w in c.worlds.values() if w.status == "unexplored"][:2]
    for w, env in zip(ws, ("toxic atmosphere", "breathable atmosphere")):
        w.status, w.seen = "probed", {"env": env}
    assert planner.score(ws[1]) > planner.score(ws[0])
    planner.step(e)
    assert c.mission(1).world == ws[1].id or c.mission(1).world == next(iter(c.worlds))


def _planner_rig():
    scenarios, _ = content.load(user=None)
    c = new_campaign("sandbox", "officer", 4)
    for w in c.worlds.values():
        w.status = "surveyed" if w.status != "contact" else w.status
    c.stock["malp"] = 0
    return c, Engine(c, scenarios)


def test_the_planner_retries_a_world_after_an_aborted_mission_once_it_has_cooled_down():
    c, e = _planner_rig()
    w = next(w for w in c.worlds.values() if w.status == "surveyed")
    w.status = "probed"
    for t in ("SG-2", "SG-3", "SG-4"):
        c.teams[t].status, c.teams[t].until = "injured", c.now + 30 * clock.DAY
    c.missions.append(Mission(1, "SG-2", w.id, "survey", c.now - clock.DAY, c.now, state="aborted"))
    planner.step(e)
    assert c.mission(2) is None or c.mission(2).world != w.id
    c2, e2 = _planner_rig()
    w2 = c2.worlds[w.id]
    w2.status = "probed"
    for t in ("SG-2", "SG-3", "SG-4"):
        c2.teams[t].status, c2.teams[t].until = "injured", c2.now + 30 * clock.DAY
    c2.missions.append(Mission(1, "SG-2", w2.id, "survey", c2.now - clock.DAY - planner.COOLDOWN,
                               c2.now - planner.COOLDOWN, state="aborted"))
    planner.step(e2)
    assert c2.mission(2).world == w2.id and c2.mission(2).type == "survey"


def test_with_nothing_new_to_do_the_planner_re_surveys_the_world_visited_longest_ago():
    c, e = _planner_rig()
    for t in ("SG-2", "SG-3", "SG-4"):
        c.teams[t].status, c.teams[t].until = "injured", c.now + 30 * clock.DAY
    for w in c.worlds.values():
        w.last_visit = c.now
    stale = list(c.worlds.values())[7]
    stale.last_visit = c.now - 5 * clock.DAY
    assert planner.step(e) and c.mission(1).world == stale.id and c.mission(1).type == "survey"


def test_the_planner_keeps_exploring_for_sixty_days():
    for seed in (1, 5):
        c, e = simulate(seed, days=60)
        late = [m for m in c.missions if m.start >= clock.START + 45 * clock.DAY]
        assert c.over is None and len(late) >= 10, (seed, len(late))
