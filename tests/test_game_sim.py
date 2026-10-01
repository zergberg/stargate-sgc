"""The idle simulation: game days with nobody at the keyboard, only standing orders and the planner."""
import pytest

from sgc.game import clock, content, factions, planner
from sgc.game.engine import Engine
from sgc.game.state import Mission, new_campaign

SCENARIOS, _ = content.load(user=None)
RECKLESS = {"unknown_idc": "open_guarded", "hostiles_following": "keep_closed", "bad_idc": "closed_silent",
            "object": "push_back", "missed_checkin": "wait", "under_fire": "hold", "contact_offer": "accept"}
SEEDS = range(1, 11)
# difficulty: (runs lost with default orders: lowest, highest), fewest runs lost with reckless orders
TARGETS = {"recruit": ((0, 0), 5), "officer": ((0, 2), 7), "commander": ((1, 5), 8)}


def simulate(seed, orders=None, days=30, difficulty="officer", mode="campaign", watch=None):
    c = new_campaign(mode, difficulty, seed)
    if orders:
        c.orders.update(orders)
    e = Engine(c, SCENARIOS)
    for _ in range(days * 24):
        planner.step(e)
        e.advance(clock.HOUR)
        if watch:
            watch(c)
        if e.ended:
            break
    return c, e


def test_default_orders_survey_worlds_and_funding_never_goes_negative():
    for seed in (1, 2, 3):
        lows = []
        c, e = simulate(seed, watch=lambda c: lows.append(c.funding))
        assert min(lows) >= 0, seed
        assert sum(w.status in ("surveyed", "contact") for w in c.worlds.values()) >= 5, seed
        assert c.reviews and min(g for _, g, _ in c.reviews) >= 50, seed


def test_the_planner_spends_on_upgrades_and_teams():
    grown = 0
    for seed in (1, 2, 3):
        c, _ = simulate(seed)
        assert "uav_program" in c.upgrades, seed
        grown += len(c.teams) >= 5
    assert grown >= 2


def test_goauld_attention_rises_in_most_runs():
    hostile = 0
    for seed in SEEDS:
        seen = set()
        simulate(seed, days=60, watch=lambda c: seen.update(
            fid for fid in factions.GOAULD if factions.stage_of(c, fid) in ("hostile", "seeking")))
        hostile += bool(seen)
    assert hostile >= 5, hostile


def test_the_arcs_come_into_play():
    woke = resolved = 0
    for seed in SEEDS:
        c, _ = simulate(seed, days=60)
        woke += c.arcs["apophis"].state != "dormant"
        resolved += any(st.state == "resolved" for st in c.arcs.values())
    assert woke >= 3 and resolved >= 1, (woke, resolved)


@pytest.mark.parametrize("difficulty", ["recruit", "officer", "commander"])
def test_balance_targets(difficulty):
    (lo, hi), reckless_min = TARGETS[difficulty]
    default = sum(simulate(s, days=60, difficulty=difficulty)[0].over is not None for s in SEEDS)
    reckless = sum(simulate(s, RECKLESS, days=60, difficulty=difficulty)[0].over is not None for s in SEEDS)
    assert lo <= default <= hi, (difficulty, default)
    assert reckless >= reckless_min and reckless > default, (difficulty, reckless, default)


def test_sandbox_has_no_arcs_and_still_plays():
    c, e = simulate(4, mode="sandbox")
    assert c.arcs == {} and c.won is None and c.record["missions"] >= 10


def test_the_planner_probes_first_and_prefers_breathable_worlds():
    c = new_campaign("sandbox", "officer", 4)
    e = Engine(c, SCENARIOS)
    out = planner.step(e)
    assert any(line.startswith("MALP QUEUED FOR") for line in out)
    ws = [w for w in c.worlds.values() if w.status == "unexplored"][:2]
    for w, env in zip(ws, ("toxic atmosphere", "breathable atmosphere")):
        w.status, w.seen = "probed", {"env": env}
    assert planner.score(ws[1]) > planner.score(ws[0])


def test_a_rescue_comes_first():
    c = new_campaign("sandbox", "officer", 4)
    e = Engine(c, SCENARIOS)
    w = list(c.worlds.values())[5]
    w.status = "hostile"
    c.teams["SG-2"].status, c.teams["SG-2"].where = "captured", w.id
    w.options.append("rescue")
    planner.step(e)
    assert any(m.type == "rescue" and m.world == w.id for m in c.missions)


def test_the_planner_never_raids_for_nothing():
    for seed in (1, 2):
        c, _ = simulate(seed, mode="sandbox")
        assert not any(m.type == "raid" for m in c.missions), seed


def test_the_planner_keeps_exploring_for_sixty_days():
    for seed in (1, 5):
        c, e = simulate(seed, days=60, difficulty="recruit")
        late = [m for m in c.missions if m.start >= clock.START + 45 * clock.DAY]
        assert c.over is None and len(late) >= 10, (seed, len(late))


def _planner_rig():
    c = new_campaign("sandbox", "officer", 4)
    for w in c.worlds.values():
        w.status = "surveyed" if w.status != "contact" else w.status
    c.stock["malp"] = 0
    c.funding = 0
    return c, Engine(c, SCENARIOS)


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


def test_the_planner_leaves_a_world_alone_while_its_probe_s_gate_is_open():
    c = new_campaign("sandbox", "officer", 4)
    e = Engine(c, SCENARIOS)
    first = next(w for w in c.worlds.values() if w.status == "unexplored")
    e.probe(first.id)
    e.advance(1)                                          # through the gate: its report lands at shutdown
    assert c.events.find(lambda ev: ev.kind == "drone_report") and first.drone is None
    out = planner.step(e)
    assert any(line.startswith("MALP QUEUED FOR") for line in out)             # the next address instead
    assert not any(line.startswith("A DRONE IS ALREADY BOUND") for line in out)


def test_the_planner_never_orders_an_extended_report():
    kinds = set()
    simulate(1, days=10, watch=lambda c: kinds.update(e.kind for e in c.events))
    assert "uplink" not in kinds and "uplink_report" not in kinds
