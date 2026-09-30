from sgc.game import arcs, content, factions, rules, world
from sgc.game.engine import Engine
from sgc.game.state import new_campaign

SCENARIOS, _ = content.load(user=None)


def camp():
    c = new_campaign("campaign", "officer", 32)
    c.events.cancel(lambda e: e.kind == "incoming")
    return c, c.worlds[world.place_id("Cimmeria")]


def play(c, sid, w, team="SG-4", roll=0.0):
    e = Engine(c, {sid: SCENARIOS[sid]})
    e.rng.random = lambda: roll
    e._start(SCENARIOS[sid], {**e._wbind(w), "team": team, "specialty": c.teams[team].specialty})
    return e


def stage3():
    c, w = camp()
    arcs.start(c, "thor")
    arcs.advance(c, "thor")
    arcs.advance(c, "thor")
    return c, w


def test_the_wake_opens_study_on_cimmeria():
    c, w = camp()
    b = {"world_id": w.id, "team": "SG-1"}
    assert rules.check_all(SCENARIOS["arc_thor_wake"].when, c, b)
    play(c, "arc_thor_wake", w, team="SG-1")
    assert c.arcs["thor"].state == "active" and "study" in w.options and w.name == "Cimmeria"
    # the arc's faction goes on file as a lead when it starts (arcs.start), but Thor hasn't spoken to us yet
    assert c.factions["asgard"].trust == 0 and "ally.asgard" not in c.inventory


def test_the_wake_plays_only_on_cimmeria_while_the_arc_sleeps():
    c, w = camp()
    other = next(x for x in c.worlds.values() if x.id != w.id)
    when = SCENARIOS["arc_thor_wake"].when
    assert not rules.check_all(when, c, {"world_id": other.id, "team": "SG-1"})
    arcs.start(c, "thor")
    assert not rules.check_all(when, c, {"world_id": w.id, "team": "SG-1"})


def test_the_labyrinth_reveals_the_asgard():
    c, w = camp()
    arcs.start(c, "thor")
    play(c, "arc_thor_labyrinth", w)
    assert c.factions["asgard"].known and c.factions["asgard"].trust == 20 and c.arcs["thor"].stage == 2
    c2, w2 = camp()
    arcs.start(c2, "thor")
    play(c2, "arc_thor_labyrinth", w2, roll=0.99)
    assert c2.arcs["thor"].stage == 1 and c2.teams["SG-4"].status == "injured"


def test_heruur_comes_to_cimmeria():
    c, w = camp()
    arcs.start(c, "thor")
    arcs.advance(c, "thor")
    assert not c.factions["heruur"].known
    e = Engine(c, SCENARIOS)
    e.advance(72 * 60)
    assert c.factions["heruur"].known and factions.stage_of(c, "heruur") == "curious"
    assert w.status == "hostile" and c.arcs["thor"].stage == 3


def test_finding_the_chariot_makes_the_asgard_allies():
    c, w = stage3()
    play(c, "arc_thor_chariot", w)
    assert "ally.asgard" in c.inventory and c.arcs["thor"].state == "resolved" and c.won is None
    c2, w2 = stage3()
    play(c2, "arc_thor_chariot", w2, roll=0.99)
    assert c2.arcs["thor"].state == "active" and c2.teams["SG-4"].status == "injured"


def test_a_week_without_the_chariot_loses_cimmeria():
    c, w = stage3()
    e = Engine(c, SCENARIOS)
    e.advance(168 * 60)
    assert c.arcs["thor"].state == "failed" and not e.ended and c.over is None


def test_thor_and_heruur_appear_only_in_the_arc():
    for sc in SCENARIOS.values():
        body = " ".join(t for n in sc.nodes.values() for t in [*n.text.values(), *(ch.label for ch in n.choices)])
        if any(name in body for name in ("Thor", "Asgard", "Heru'ur", "Gairwyn")):
            # only the arc itself, or a scenario that can only play once the Asgard are allies
            assert sc.id.startswith("arc_thor") or any(cond.text == "ally.asgard" for cond in sc.when), sc.id
