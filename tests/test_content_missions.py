from sgc.game import content, rules, world
from sgc.game.engine import Engine
from sgc.game.state import CapturedDrone, new_campaign

SCENARIOS, _ = content.load(user=None)
NEW = ("trade", "raid", "study", "rescue", "recover", "mine", "aid")


def camp():
    c = new_campaign("sandbox", "officer", 21)
    w = list(c.worlds.values())[5]
    w.env, w.inhabitants, w.features, w.owner, w.status = "normal", "human", (), None, "probed"
    return c, w


def play(c, sid, w, team="SG-3", roll=0.0, **extra):
    """Play one bundled scenario on a world with a fixed roll (0.0 wins every roll, 0.99 loses)."""
    e = Engine(c, {sid: SCENARIOS[sid]})
    e.rng.random = lambda: roll
    logs = []
    e._log = logs.append
    e._start(SCENARIOS[sid], {"world": w.name, "world_id": w.id, "designation": w.id, "team": team,
                              "specialty": c.teams[team].specialty, **extra})
    return logs


def test_every_new_type_has_its_own_debrief_and_a_check_in():
    for mtype in NEW:
        assert any(sc.kind == "debrief" and sc.mission_type == mtype for sc in SCENARIOS.values()), mtype
        assert any(sc.kind == "checkin" and sc.mission_type in (None, mtype) for sc in SCENARIOS.values()), mtype


def _effects(o):
    if o.roll:
        yield from _effects(o.roll.win)
        yield from _effects(o.roll.lose)
    else:
        yield from o.effects


def test_something_in_the_content_unlocks_every_type():
    unlocks = {e.text.split()[1] for sc in SCENARIOS.values() for node in sc.nodes.values()
               for ch in node.choices for e in _effects(ch.outcome) if e.text.startswith("unlock ")}
    assert {"contact", "trade", "raid", "study", "mine", "aid"} <= unlocks      # rescue and recover open themselves


def test_no_mission_text_names_a_goauld():
    for sc in SCENARIOS.values():
        if sc.kind in ("checkin", "debrief", "probe") and sc.id != "debrief_tollana" and not sc.id.startswith("arc_"):
            for node in sc.nodes.values():
                for text in [*node.text.values(), *(ch.label for ch in node.choices)]:
                    assert not any(name in text for name in world.GOAULD), (sc.id, text)


def test_a_garrison_debrief_names_its_goauld_and_opens_a_raid():
    c, w = camp()
    w.inhabitants, w.owner = "jaffa", "Sokar"
    logs = play(c, "debrief_garrison", w)
    assert c.factions["sokar"].known and w.seen["owner"] == "Sokar" and "raid" in w.options
    assert "NEW FACTION ON FILE: SOKAR — FROM THE JAFFA" in logs and w.status == "hostile"


def test_a_stronghold_debrief_names_its_goauld_and_opens_a_raid():
    c, w = camp()
    w.inhabitants, w.owner = "goauld", "Cronus"
    play(c, "debrief_stronghold", w)
    assert c.factions["cronus"].known and "raid" in w.options and w.status == "hostile"


def test_a_raid_draws_attention_and_can_bring_back_a_zat():
    c, w = camp()
    w.inhabitants, w.owner = "jaffa", "Sokar"
    play(c, "debrief_raid", w, roll=0.0)
    assert "tech.zat" in c.inventory and c.factions["sokar"].attention == 15
    c2, w2 = camp()
    w2.inhabitants, w2.owner = "jaffa", "Sokar"
    play(c2, "debrief_raid", w2, roll=0.99)
    assert c2.teams["SG-3"].status == "injured" and c2.factions["sokar"].attention == 15


def test_contact_builds_trust_and_opens_trade_and_trade_makes_a_deal():
    c, w = camp()
    play(c, "debrief_contact", w, team="SG-1")
    assert c.factions["locals"].trust == 5 and "trade" in w.options
    c.factions["locals"].trust = 20
    play(c, "debrief_trade_deal", w, team="SG-1")
    assert c.deals and c.deals[0].world == w.id


def test_trade_waits_on_trust():
    c, w = camp()
    bind = {"world": w.name, "world_id": w.id, "team": "SG-1"}
    assert rules.check_all(SCENARIOS["debrief_trade_wary"].when, c, bind)
    assert not rules.check_all(SCENARIOS["debrief_trade_deal"].when, c, bind)
    play(c, "debrief_trade_wary", w, team="SG-1")
    assert c.factions["locals"].trust == 5 and not c.deals


def test_locals_open_contact_and_aid():
    c, w = camp()
    play(c, "debrief_locals", w)
    assert {"contact", "aid"} <= set(w.options)


def test_ruins_and_technology_open_a_study():
    c, w = camp()
    play(c, "debrief_ruins", w)
    assert "study" in w.options
    c2, w2 = camp()
    play(c2, "debrief_technology", w2)
    assert "study" in w2.options


def test_mining_and_aid():
    c, w = camp()
    w.features = ("naquadah",)
    play(c, "debrief_naquadah", w)
    assert "mine" in w.options
    play(c, "debrief_mine", w)
    assert c.naquadah == 4
    play(c, "debrief_aid", w, team="SG-1")
    assert c.factions["locals"].trust == 10 and "trade" in w.options


def test_rescue_and_recover():
    c, w = camp()
    c.teams["SG-2"].status, c.teams["SG-2"].where = "captured", w.id
    play(c, "debrief_rescue", w, roll=0.0, captive="SG-2")
    assert c.teams["SG-2"].status == "base"
    c.captured_drones.append(CapturedDrone("malp", w.id, 0, located=True))
    stock = c.stock["malp"]
    play(c, "debrief_recover", w, roll=0.0)
    assert c.captured_drones == [] and c.stock["malp"] == stock + 1


def test_a_rescue_debrief_only_plays_while_the_captive_is_held():
    c, w = camp()
    sc = SCENARIOS["debrief_rescue"]
    bind = {"world": w.name, "world_id": w.id, "team": "SG-3", "captive": "SG-2"}
    c.teams["SG-2"].status, c.teams["SG-2"].where = "captured", w.id
    assert rules.check_all(sc.when, c, bind)
    c.teams["SG-2"].status = "lost"                  # "team X base" would revive a lost team
    assert not rules.check_all(sc.when, c, bind)


def test_the_tollan_are_met_on_tollana():
    c = new_campaign("campaign", "officer", 21)
    w = c.worlds[world.place_id("Tollana")]
    assert SCENARIOS["debrief_tollana"].when
    assert SCENARIOS["debrief_tollana"].mission_type == "survey"     # never displaces a rescue's debrief
    play(c, "debrief_tollana", w, team="SG-1")
    assert c.factions["tollan"].known and "contact" in w.options
