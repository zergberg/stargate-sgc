import pytest

from sgc.game import arcs, clock, factions, rules, trade, world
from sgc.game.rules import RuleError
from sgc.game.state import STOCK, CapturedDrone, Mission, Team, new_campaign


def camp(diff="officer", mode="campaign"):
    return new_campaign(mode, diff, 11)


def run(texts, c, bind=None):
    return rules.apply_all([rules.parse_effect(t) for t in texts], c, bind or {})


def cond(text, c, bind=None):
    return rules.parse_cond(text)(c, bind or {})


def owned(c, owner="Sokar"):
    w = list(c.worlds.values())[6]
    w.owner, w.inhabitants, w.env = owner, "jaffa", "normal"
    return w, {"world": w.name, "world_id": w.id, "designation": w.id}


# -------------------------------------------------------------- conditions

def test_faction_conditions():
    c = camp()
    c.factions["apophis"].attention, c.factions["tokra"].trust = 55, 30
    assert cond("attention apophis >= 50", c) and not cond("attention apophis >= 60", c)
    assert cond("stage apophis hostile", c) and cond("trust tokra >= 25", c) and not cond("aware tokra", c)
    c.factions["tokra"].known = True
    assert cond("aware tokra", c) and cond("attention {faction} > 50", c, {"faction_id": "apophis"})
    w, b = owned(c, "Apophis")
    assert cond("stage {owner} hostile", c, b) and not cond("stage {owner} hostile", c, {})


def test_resource_upgrade_and_arc_conditions():
    c = camp()
    assert cond("funding >= 500", c) and not cond("naquadah > 0", c)
    assert not cond("upgrade uav_program", c)
    c.upgrades.add("uav_program")
    assert cond("upgrade uav_program", c)
    assert cond("arc apophis dormant", c) and not cond("arc apophis stage >= 1", c)
    arcs.start(c, "apophis")
    assert cond("arc apophis active", c) and cond("arc apophis stage == 1", c)


def test_named_places():
    c = camp()
    chulak = c.worlds[world.place_id("Chulak")]
    b = {"world_id": chulak.id}
    assert cond("is {world} @Chulak", c, b) and not cond("is {world} @Cimmeria", c, b)
    assert cond("known @Chulak", c) and not cond("known @Vorash", c)          # unlisted until revealed
    assert cond("status @Chulak unexplored", c)


def test_held_means_a_captive_team_or_a_located_drone():
    c = camp()
    w, b = owned(c)
    assert not cond("held {world}", c, b)
    c.captured_drones.append(CapturedDrone("malp", w.id, c.now))
    assert not cond("held {world}", c, b)
    c.captured_drones[0].located = True
    assert cond("held {world}", c, b)
    c.captured_drones.clear()
    c.teams["SG-3"].status, c.teams["SG-3"].where = "captured", w.id
    assert cond("held {world}", c, b)


def test_hidden_conditions_are_flagged():
    for text in ("world {world} env toxic", "attention apophis >= 20", "stage {owner} curious", "is {world} @Chulak"):
        assert rules.hidden(text), text
    for text in ("trust tokra >= 25", "aware apophis", "arc apophis active", "funding >= 10",
                 "status {world} probed", "held {world}"):
        assert not rules.hidden(text), text


@pytest.mark.parametrize("bad", ["attention zeus >= 5", "trust tokra >> 5", "stage apophis angry", "arc ghost active",
                                 "arc apophis stage >= x", "is {world} Chulak", "is {world} @Atlantis",
                                 "upgrade warp_drive", "aware", "held", "naquadah >= lots"])
def test_bad_stage_2_conditions_raise(bad):
    with pytest.raises(RuleError):
        rules.parse_cond(bad)


# -------------------------------------------------------------- effects

@pytest.mark.parametrize("bad", ["attention apophis 5", "trust zeus +5", "funding 5", "naquadah +x", "incident -1",
                                 "arc apophis explode", "deal {world} spice 2", "deal {world} naquadah 0",
                                 "reveal faction zeus from jaffa", "reveal faction apophis from gossip",
                                 "reveal address @Atlantis", "locate", "recover {world} now", "unlock warp {world}"])
def test_bad_stage_2_effects_raise(bad):
    with pytest.raises(RuleError):
        rules.parse_effect(bad)


def test_attention_and_trust_effects():
    c = camp()
    w, b = owned(c)
    assert run(["attention {owner} +25"], c, b) == []                          # Sokar is unknown: silent
    assert c.factions["sokar"].attention == 25 and any(e.kind == "faction_action" for e in c.events)
    run(["trust tokra +30", "trust {faction} -5"], c, {"faction_id": "tokra"})
    assert c.factions["tokra"].trust == 25
    assert run(["attention {owner} +5"], c, {}) == [] and c.factions["sokar"].attention == 25


def test_attention_reaching_80_starts_an_arc_endgame():
    c = camp()
    arcs.start(c, "apophis")
    lines = run(["attention apophis +80"], c)
    assert c.arcs["apophis"].stage == 4 and any(line.startswith("APOPHIS AND CHULAK: TWO HA'TAKS") for line in lines)


def test_reveal_faction_files_the_world_owner_too():
    c = camp()
    w, b = owned(c)
    assert run(["reveal faction {owner} from jaffa"], c, b) == ["NEW FACTION ON FILE: SOKAR — FROM THE JAFFA"]
    assert w.seen["owner"] == "Sokar" and c.factions["sokar"].known and c.ledger["intel"] == 1
    assert run(["reveal faction tokra from allies"], c) == ["NEW FACTION ON FILE: THE TOK'RA — FROM ALLIED INTELLIGENCE"]
    assert run(["reveal faction tokra from allies"], c) == [] and c.ledger["intel"] == 2


def test_reveal_address_of_a_named_place():
    c = camp()
    vid = world.place_id("Vorash")
    assert run(["reveal address @Vorash"], c, {"team": "SG-1"}) == [f"NEW ADDRESS: {vid}"]
    assert vid in c.worlds and vid not in c.unlisted and c.worlds[vid].found == "intel from SG-1"
    assert run(["reveal address @Vorash"], c) == [] and c.ledger["intel"] == 1
    s = camp(mode="sandbox")
    run(["reveal address @Vorash"], s)
    assert s.worlds[vid].name == vid and s.worlds[vid].hidden_names == {"allies": "Vorash"}


def test_funding_naquadah_and_incidents():
    c = camp()
    assert run(["funding +50", "naquadah +4"], c) == ["FUNDING +50", "NAQUADAH +4"]
    assert c.funding == 550 and c.naquadah == 4
    run(["funding -9999", "naquadah -9"], c)
    assert c.funding == 0 and c.naquadah == 0
    assert run(["incident +1"], c) == ["THE NID HAS TAKEN AN INTEREST"] and c.ledger["incidents"] == 1


def test_arc_effects_and_a_catastrophe_stops_what_follows():
    c = camp()
    assert run(["arc thor start", "arc thor advance"], c)[0] == "NEW LEAD: CIMMERIA AND THOR'S HAMMER"
    assert c.arcs["thor"].stage == 2
    run(["arc thor resolve"], c)
    assert c.arcs["thor"].state == "resolved"
    run(["arc apophis start", "arc apophis fail", "security +5"], c)
    assert c.over.startswith("Apophis's ha'taks") and c.meters["security"] == 70


def test_a_deal_effect():
    c = camp()
    w, b = owned(c)
    assert run(["deal {world} naquadah 2"], c, b)[0].startswith("TRADE DEAL: 2 NAQUADAH")
    assert c.deals[0].world == w.id
    assert trade.HOSTILE == dict(factions.STAGES)["hostile"]


def test_capture_and_loss_count_and_draw_attention():
    c = camp()
    w, b = owned(c)
    run(["team {team} captured"], c, {**b, "team": "SG-2"})
    assert c.factions["sokar"].attention == 15 and "rescue" in w.options and c.ledger["captured"] == 1
    run(["team {team} lost"], c, {"team": "SG-3"})
    assert c.ledger["lost"] == 1


def test_a_captured_drone_is_held_located_and_recovered():
    c = camp()
    w, b = owned(c)
    w.drone = "uav"
    stock = c.stock["uav"]
    run(["drone {world} captured"], c, b)
    assert c.captured_drones == [CapturedDrone("uav", w.id, c.now)] and c.factions["sokar"].attention == 10
    assert w.status == "hostile" and "recover" not in w.options
    assert run(["locate {world}"], c, b) == [f"THE UAV TAKEN ON {w.name.upper()} IS HELD THERE"]
    assert "recover" in w.options and c.captured_drones[0].located
    assert run(["recover {world}"], c, b) == [f"UAV RECOVERED FROM {w.name.upper()}"]
    assert c.captured_drones == [] and c.stock["uav"] == stock + 1
    assert run(["recover {world}"], c, b) == []


def test_new_mission_types_can_be_unlocked():
    c = camp()
    w, b = owned(c)
    run(["unlock raid {world}", "unlock mine {world}", "unlock trade @Chulak"], c, b)
    assert {"raid", "mine"} <= set(w.options) and "trade" in c.worlds[world.place_id("Chulak")].options


def test_defense_softens_security_losses_and_breaches():
    c = camp()
    c.upgrades.add("iris_reinforcement")
    run(["security -8"], c)
    assert c.meters["security"] == 64
    run(["breach 20"], c)
    assert c.meters["security"] == 49 and c.ledger["breaches"] == 1


def test_gains_and_intel_count_for_the_review():
    c = camp()
    run(["gain tech.zat", "gain ally.tokra", "reveal address"], c)
    assert (c.ledger["tech"], c.ledger["allies"], c.ledger["intel"]) == (1, 1, 1)


def test_the_territory_pick():
    c = camp()
    w, b = owned(c)
    c.missions.append(Mission(1, "SG-2", w.id, "survey", c.now, c.now + 600))
    c.teams["SG-2"].status, c.teams["SG-2"].where, c.teams["SG-2"].mission = "offworld", w.id, 1
    assert rules.teams_matching(c, "territory", {"faction_id": "sokar"}) == ["SG-2"]
    assert rules.teams_matching(c, "territory", {"faction_id": "yu"}) == []


def test_new_addresses_never_take_an_unlisted_designation():
    c = camp()
    for _ in range(30):
        rules.new_address(c, "intel")
    assert not set(c.unlisted) & set(c.worlds)


# -------------------------------------------------------------- upkeep

def test_new_campaigns_start_without_uavs_and_stores_are_bigger():
    assert STOCK == {"malp": (4, 8), "uav": (0, 4)}
    assert camp().stock == {"malp": 4, "uav": 0}


def test_nothing_is_free_at_midnight_but_the_reserve_is_bought():
    c = camp()
    c.stock = {"malp": 1, "uav": 0}
    c.minutes = clock.DAY
    msgs = rules.hourly(c)
    assert "REQUISITION: 1 MALP (20)" in msgs and c.stock == {"malp": 2, "uav": 0} and c.funding == 480
    assert not any("DELIVERED" in m for m in msgs)


def test_a_drone_coming_home_always_fits():
    c = camp()
    c.stock["malp"] = 8
    assert rules.stow(c, "malp") == [] and c.stock["malp"] == 9


def test_the_security_detail_doubles_recovery():
    c = camp()
    c.meters["security"] = 50
    c.upgrades.add("security_detail")
    c.minutes = 18 * 60
    rules.hourly(c)
    assert c.meters["security"] == 52


def test_forming_and_training_teams_come_on_duty():
    c = camp()
    c.teams["SG-5"] = Team("medical", status="forming", until=c.now)
    c.teams["SG-3"].status, c.teams["SG-3"].until, c.teams["SG-3"].secondary = "training", c.now, "medical"
    msgs = rules.hourly(c)
    assert "SG-5 READY FOR DUTY" in msgs and "SG-3 TRAINING COMPLETE: MEDICAL" in msgs
    assert c.teams["SG-5"].status == c.teams["SG-3"].status == "base"


def test_a_lost_commissioned_team_is_disbanded_but_a_core_team_re_forms():
    c = camp()
    c.teams["SG-7"] = Team("combat", status="lost", until=c.now, idc="revoked")
    c.teams["SG-2"].status, c.teams["SG-2"].until = "lost", c.now
    msgs = rules.hourly(c)
    assert "SG-7" not in c.teams and "SG-7 DISBANDED — THE NUMBER CAN BE RECOMMISSIONED" in msgs
    assert c.teams["SG-2"].status == "base" and "SG-2 RE-FORMED — GREEN" in msgs


def test_a_rescue_under_way_holds_the_captive_clock():
    c = camp()
    w, _ = owned(c)
    c.teams["SG-3"].status, c.teams["SG-3"].where, c.teams["SG-3"].until = "captured", w.id, c.now
    c.missions.append(Mission(1, "SG-2", w.id, "rescue", c.now, c.now + 1200, target="SG-3"))
    rules.hourly(c)
    assert c.teams["SG-3"].status == "captured" and c.teams["SG-3"].until == c.now + 1200 + clock.DAY
    c.missions[0].state = "complete"
    c.minutes = c.teams["SG-3"].until
    assert "SG-3 PRESUMED LOST" in rules.hourly(c)


def test_attention_decays_at_midnight():
    c = camp()
    factions.adjust_attention(c, "yu", 30)
    c.minutes = 2 * clock.DAY
    rules.hourly(c)
    assert c.factions["yu"].attention == 30 - factions.DECAY["officer"]


def test_running_out_of_addresses_is_said_once_per_time():
    c = camp()

    def explore_all():
        for w in c.worlds.values():
            if w.status == "unexplored":
                w.status = "surveyed"
    explore_all()
    c.minutes = clock.DAY
    assert rules.EXPLORED in rules.hourly(c)
    c.minutes = 2 * clock.DAY
    assert rules.EXPLORED not in rules.hourly(c)
    rules.new_address(c, "intel")
    c.minutes = 3 * clock.DAY
    rules.hourly(c)
    explore_all()
    c.minutes = 4 * clock.DAY
    assert rules.EXPLORED in rules.hourly(c)


def test_team_effects_on_a_number_not_on_the_roster_do_nothing():
    c = camp()
    assert "SG-7" not in c.teams
    assert run(["team SG-7 lost", "team SG-7 captured", "team SG-7 injured", "idc SG-7 revoke",
                "idc SG-7 compromise"], c) == []
    assert "SG-7" not in c.teams and c.ledger["lost"] == 0 and c.ledger["captured"] == 0


@pytest.mark.parametrize("status", ["forming", "training"])
def test_content_cannot_set_a_team_forming_or_training(status):
    with pytest.raises(RuleError):
        rules.parse_effect(f"team {{team}} {status}")
    assert rules.parse_cond(f"team SG-5 {status}")                          # but it may ask


def test_any_condition_on_the_owner_is_hidden():
    for text in ("aware {owner}", "trust {owner} >= 0"):
        assert rules.hidden(text), text
    assert not rules.hidden("aware {faction}") and not rules.hidden("trust {faction} >= 25")


def test_capturing_a_captured_or_lost_team_changes_nothing():
    c = camp()
    w, b = owned(c)
    run(["team {team} captured"], c, {**b, "team": "SG-2"})
    until, attention = c.teams["SG-2"].until, c.factions["sokar"].attention
    c.minutes += 600
    assert run(["team {team} captured"], c, {**b, "team": "SG-2"}) == []
    assert c.teams["SG-2"].until == until and c.factions["sokar"].attention == attention
    assert c.ledger["captured"] == 1
    run(["team SG-3 lost"], c)
    assert run(["team {team} captured"], c, {**b, "team": "SG-3"}) == []
    assert c.teams["SG-3"].status == "lost" and c.ledger["captured"] == 1


@pytest.mark.parametrize("bad", ["trust apophis >= 5", "attention tokra > 1", "stage asgard curious"])
def test_a_faction_of_the_wrong_kind_is_refused_in_conditions(bad):
    with pytest.raises(RuleError):
        rules.parse_cond(bad)


@pytest.mark.parametrize("bad", ["trust apophis +5", "attention tokra +5"])
def test_a_faction_of_the_wrong_kind_is_refused_in_effects(bad):
    with pytest.raises(RuleError):
        rules.parse_effect(bad)
