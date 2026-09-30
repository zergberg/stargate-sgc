from sgc.game import arcs, content, rules, world
from sgc.game.engine import Engine
from sgc.game.state import new_campaign

SCENARIOS, _ = content.load(user=None)


def camp():
    c = new_campaign("campaign", "officer", 33)
    c.events.cancel(lambda e: e.kind == "incoming")
    c.minutes += 8 * 24 * 60
    return c


def play(c, sid, w, team="SG-1", roll=0.0):
    e = Engine(c, {sid: SCENARIOS[sid]})
    e.rng.random = lambda: roll
    e._start(SCENARIOS[sid], {**e._wbind(w), "team": team, "specialty": c.teams[team].specialty})
    return e


def held_world(c, inhabitants="goauld"):
    w = list(c.worlds.values())[9]
    w.inhabitants, w.owner, w.env = inhabitants, "Cronus", "normal"
    return w


def test_the_wake_comes_from_a_goauld_world_after_the_first_week():
    c = camp()
    w = held_world(c)
    b = {"world_id": w.id, "team": "SG-3"}
    assert rules.check_all(SCENARIOS["arc_tokra_wake_stronghold"].when, c, b)
    c.minutes -= 8 * 24 * 60
    assert not rules.check_all(SCENARIOS["arc_tokra_wake_stronghold"].when, c, b)


def test_the_garrison_wake_comes_from_a_jaffa_world():
    c = camp()
    b = {"world_id": held_world(c, "jaffa").id, "team": "SG-3"}
    assert rules.check_all(SCENARIOS["arc_tokra_wake_garrison"].when, c, b)
    assert not rules.check_all(SCENARIOS["arc_tokra_wake_stronghold"].when, c, b)


def test_the_wake_puts_vorash_on_the_dialing_list():
    c = camp()
    play(c, "arc_tokra_wake_stronghold", held_world(c), team="SG-3")
    vorash = c.worlds[world.place_id("Vorash")]
    assert c.arcs["tokra"].state == "active" and "contact" in vorash.options and not c.unlisted
    # the whisper names the Tok'ra (arcs.start files them), but Vorash stays unnamed until a team goes
    assert c.factions["tokra"].known and vorash.name == vorash.id


def test_the_wake_plays_only_once():
    c = camp()
    w = held_world(c)
    play(c, "arc_tokra_wake_stronghold", w, team="SG-3")
    b = {"world_id": w.id, "team": "SG-3"}
    assert not rules.check_all(SCENARIOS["arc_tokra_wake_stronghold"].when, c, b)
    assert not rules.check_all(SCENARIOS["arc_tokra_wake_garrison"].when, c, b)


def test_meeting_the_tokra_then_their_request():
    c = camp()
    arcs.start(c, "tokra")
    rules.apply_all([rules.parse_effect("reveal address @Vorash")], c, {})
    vorash = c.worlds[world.place_id("Vorash")]
    play(c, "arc_tokra_vorash", vorash)
    assert c.factions["tokra"].known and vorash.name == "Vorash" and c.arcs["tokra"].stage == 2
    e = Engine(c, SCENARIOS)
    e.advance(48 * 60)
    assert e.alarm_title == "PRIORITY ONE"
    e.advance(4 * 60)                                   # nobody answers: we agree
    assert c.factions["tokra"].trust == 40 and c.arcs["tokra"].stage == 3


def test_trust_leads_to_an_alliance():
    c = camp()
    arcs.start(c, "tokra")
    arcs.advance(c, "tokra")
    arcs.advance(c, "tokra")
    rules.apply_all([rules.parse_effect("reveal address @Vorash")], c, {})
    vorash = c.worlds[world.place_id("Vorash")]
    c.factions["tokra"].trust = 30
    b = {"world_id": vorash.id}
    assert rules.check_all(SCENARIOS["arc_tokra_talks"].when, c, b)
    assert not rules.check_all(SCENARIOS["arc_tokra_alliance"].when, c, b)
    play(c, "arc_tokra_talks", vorash)
    assert c.factions["tokra"].trust == 40 and rules.check_all(SCENARIOS["arc_tokra_alliance"].when, c, b)
    play(c, "arc_tokra_alliance", vorash)
    assert "ally.tokra" in c.inventory and c.arcs["tokra"].state == "resolved"


def test_two_weeks_of_silence_ends_it():
    c = camp()
    arcs.start(c, "tokra")
    arcs.advance(c, "tokra")
    arcs.advance(c, "tokra")
    rules.apply_all([rules.parse_effect("reveal address @Vorash")], c, {})
    e = Engine(c, SCENARIOS)
    e.advance(336 * 60)
    assert c.arcs["tokra"].state == "failed" and c.worlds[world.place_id("Vorash")].status == "lost"


def test_the_arc_sleeps_in_sandbox():
    c = new_campaign("sandbox", "officer", 33)
    c.minutes += 8 * 24 * 60
    b = {"world_id": held_world(c).id, "team": "SG-3"}
    assert not rules.check_all(SCENARIOS["arc_tokra_wake_stronghold"].when, c, b)
    assert not rules.check_all(SCENARIOS["arc_tokra_wake_garrison"].when, c, b)


def test_the_tokra_appear_only_in_their_arc():
    for sc in SCENARIOS.values():
        body = " ".join(t for n in sc.nodes.values() for t in [*n.text.values(), *(ch.label for ch in n.choices)])
        if any(name in body for name in ("Tok'ra", "Martouf", "Vorash")):
            assert sc.id.startswith("arc_tokra"), sc.id
