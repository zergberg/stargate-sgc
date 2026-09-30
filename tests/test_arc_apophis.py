from sgc.game import arcs, clock, content, rules, world
from sgc.game.engine import Engine
from sgc.game.state import new_campaign

SCENARIOS, _ = content.load(user=None)


def camp():
    c = new_campaign("campaign", "officer", 31)
    c.events.cancel(lambda e: e.kind == "incoming")
    return c, c.worlds[world.place_id("Chulak")]


def play(c, sid, w, team="SG-3", roll=0.0):
    e = Engine(c, {sid: SCENARIOS[sid]})
    e.rng.random = lambda: roll
    logs = []
    e._log = logs.append
    e._start(SCENARIOS[sid], {**e._wbind(w), "team": team, "specialty": c.teams[team].specialty})
    return e, logs


def bind(c, w, team="SG-3"):
    return {"world": w.name, "world_id": w.id, "designation": w.id, "team": team,
            "specialty": c.teams[team].specialty}


def to_stage(c, n):
    arcs.start(c, "apophis")
    for _ in range(n - 1):
        arcs.advance(c, "apophis")


def test_the_wake_plays_only_on_chulak_and_only_once():
    c, w = camp()
    wake = SCENARIOS["arc_apophis_wake"]
    assert rules.check_all(wake.when, c, bind(c, w))
    other = next(x for x in c.worlds.values() if x.id != w.id)
    assert not rules.check_all(wake.when, c, bind(c, other))
    arcs.start(c, "apophis")
    assert not rules.check_all(wake.when, c, bind(c, w))


def test_the_wake_names_apophis_and_opens_raids():
    c, w = camp()
    _, logs = play(c, "arc_apophis_wake", w)
    assert c.arcs["apophis"].state == "active" and c.factions["apophis"].known
    assert "raid" in w.options and w.name == "Chulak" and "NEW LEAD: APOPHIS AND CHULAK" in logs


def test_the_raid_turns_tealc_and_apophis_strikes_back():
    c, w = camp()
    play(c, "arc_apophis_wake", w)
    play(c, "arc_apophis_raid", w, roll=0.0)
    assert c.arcs["apophis"].stage == 2 and c.factions["jaffa"].known and c.factions["jaffa"].trust == 25
    e = Engine(c, SCENARIOS)
    step = next(ev for ev in c.events if ev.kind == "arc_step")
    e.advance(step.due - c.now)
    assert e.alarm_title == "PRIORITY ONE"
    e.advance(4 * 60)                                  # no answer: the iris stays closed
    assert c.arcs["apophis"].stage == 3


def test_a_failed_raid_leaves_the_arc_where_it_was():
    c, w = camp()
    play(c, "arc_apophis_wake", w)
    play(c, "arc_apophis_raid", w, roll=0.99)
    assert c.arcs["apophis"].stage == 1 and c.teams["SG-3"].status == "injured"


def test_bratac_warns_of_the_fleet_and_the_countdown_starts():
    c, w = camp()
    to_stage(c, 3)
    c.factions["jaffa"].trust = 25
    assert rules.check_all(SCENARIOS["arc_apophis_bratac"].when, c, bind(c, w, "SG-1"))
    play(c, "arc_apophis_bratac", w, team="SG-1")
    st = c.arcs["apophis"]
    assert "ally.jaffa" in c.inventory and st.stage == 4 and st.deadline == c.now + 120 * clock.HOUR


def test_harassing_the_fleet_can_bring_the_endgame_on():
    c, w = camp()
    to_stage(c, 3)
    c.factions["apophis"].attention = 70
    play(c, "arc_apophis_harass", w, roll=0.0)
    assert c.arcs["apophis"].stage == 4 and c.arcs["apophis"].deadline is not None


def test_striking_at_chulak_in_time_wins_the_campaign():
    c, w = camp()
    to_stage(c, 4)
    e, _ = play(c, "arc_apophis_strike", w, roll=0.0)
    assert c.arcs["apophis"].state == "resolved" and c.won is not None and e.alarm_title == "VICTORY"


def test_without_help_the_fleet_ends_it():
    c, w = camp()
    to_stage(c, 4)
    e = Engine(c, SCENARIOS)
    e.rng.random = lambda: 0.99
    e.advance(c.arcs["apophis"].deadline - c.now)
    assert e.ended and c.ending == "fallen" and e.prompt.title == "EARTH HAS FALLEN"


def test_the_asgard_can_turn_the_fleet_away():
    c, w = camp()
    to_stage(c, 4)
    c.inventory.add("ally.asgard")
    e = Engine(c, SCENARIOS)
    e.advance(c.arcs["apophis"].deadline - c.now)
    assert [ok for _, ok in e.prompt.options] == [True, True]
    e.key("2")
    assert c.arcs["apophis"].state == "resolved" and "ally.asgard" in c.used


def test_apophis_and_his_people_appear_only_in_his_arc():
    for sc in SCENARIOS.values():
        body = " ".join(t for n in sc.nodes.values() for t in [*n.text.values(), *(ch.label for ch in n.choices)])
        if any(name in body for name in ("Apophis", "Teal'c", "Bra'tac")):
            assert sc.id.startswith("arc_apophis"), sc.id
            assert sc.kind == "arc" or any(cond.text.startswith("arc apophis") for cond in sc.when), sc.id
