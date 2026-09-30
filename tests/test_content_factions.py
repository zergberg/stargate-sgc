from sgc.game import clock, content, planner, rules, world
from sgc.game.engine import Engine
from sgc.game.state import new_campaign

SCENARIOS, _ = content.load(user=None)
RECKLESS = {"unknown_idc": "open_guarded", "hostiles_following": "keep_closed", "bad_idc": "closed_silent",
            "object": "push_back", "missed_checkin": "wait", "under_fire": "hold", "contact_offer": "accept"}


def pool(stage):
    return [sc for sc in SCENARIOS.values() if sc.kind == "faction" and sc.stage == stage]


def texts(sc):
    for node in sc.nodes.values():
        yield from node.text.values()
        yield from (ch.label for ch in node.choices)


def _effects(o):
    if o.roll:
        yield from _effects(o.roll.win)
        yield from _effects(o.roll.lose)
    else:
        yield from o.effects


def test_every_stage_has_actions_and_the_random_pool_is_friendly():
    assert len(pool("curious")) >= 4 and len(pool("hostile")) >= 4 and len(pool("seeking")) >= 2
    assert {sc.id for sc in SCENARIOS.values() if sc.kind == "incoming"} == \
        {"false_alarm", "stolen_idc", "hostiles_following"}
    assert {"unknown_probe", "jaffa_incursion", "naquadah_bomb", "unknown_code"} <= {
        sc.id for sc in SCENARIOS.values() if sc.kind == "faction"}


def test_no_action_names_a_goauld_before_the_sgc_knows_it():
    for sc in SCENARIOS.values():
        if sc.kind == "faction":
            for text in texts(sc):
                assert not any(name in text for name in world.GOAULD), (sc.id, text)


def test_an_action_can_reveal_its_goauld_and_spies_can_steal_codes():
    effects = {e.text for sc in pool("curious") + pool("hostile") for node in sc.nodes.values()
               for ch in node.choices for e in _effects(ch.outcome)}
    assert "reveal faction {faction} from jaffa" in effects
    assert any(e.startswith("idc {team} compromise") for e in effects)
    assert any(e.startswith("attention {faction} +") for e in effects)


def run(seed, stage_attention, days, orders=None, difficulty="officer"):
    c = new_campaign("campaign", difficulty, seed)
    if orders:
        c.orders.update(orders)
    e = Engine(c, SCENARIOS)
    for fid in ("cronus", "baal"):
        rules.attention(c, fid, stage_attention)
    for _ in range(days * 24):
        planner.step(e)
        e.advance(clock.HOUR)
        for fid in ("cronus", "baal"):
            c.factions[fid].attention = max(c.factions[fid].attention, stage_attention)   # hold the stage
        if e.ended:
            break
    return c, e


def test_curious_goauld_are_a_nuisance_not_a_threat():
    for seed in (1, 2, 3):
        c, e = run(seed, 30, 20)
        assert c.over is None, (seed, c.over)
        assert c.meters["security"] < 100 or c.record["personnel_lost"] > 0     # they did make themselves felt


def test_a_seeking_goauld_can_overrun_a_reckless_commander():
    assert any(run(seed, 85, 20, RECKLESS, "commander")[0].over for seed in range(1, 7))
