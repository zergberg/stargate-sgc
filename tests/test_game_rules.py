import pytest

from sgc.game import rules
from sgc.game.state import POOL, new_campaign


def camp(diff="officer", mode="campaign"):
    return new_campaign(mode, diff, 11)


def run(effect_texts, c, bind=None):
    return rules.apply_all([rules.parse_effect(t) for t in effect_texts], c, bind or {})


# -------------------------------------------------------------- conditions

@pytest.mark.parametrize("text,expected", [
    ("security >= 70", True), ("security > 70", False), ("intel < 11", True), ("cycles == 0", True),
    ("ally.tokra", False), ("not ally.tokra", True), ("any_compromised_idc", False), ("any_captured", False),
    ("team SG-1 base", True), ("team {team} captured", False),
])
def test_conditions(text, expected):
    c = camp()
    assert rules.parse_cond(text)(c, {"team": "SG-2"}) is expected


def test_goauld_conditions_use_bindings():
    c = camp()
    lord = c.lords[0]
    lord.strength = 1
    assert rules.parse_cond("goauld {goauld} strength <= 1")(c, {"goauld": lord.name})
    assert not rules.parse_cond("goauld {goauld} strength >= 2")(c, {"goauld": lord.name})
    absent = next(n for n in POOL if c.lord(n) is None)
    assert not rules.parse_cond(f"goauld {absent} strength >= 0")(c, {})


def test_single_use_flags_count_as_gone_once_used():
    c = camp()
    c.inventory.add("ally.asgard")
    assert rules.parse_cond("ally.asgard")(c, {})
    c.used.add("ally.asgard")
    assert not rules.parse_cond("ally.asgard")(c, {})


@pytest.mark.parametrize("bad", ["", "security >= lots", "moral > 3", "team SG-9 base", "team SG-1 napping",
                                 "not ally.goauld", "goauld Anubis strength < 2", "goauld {team} strength < 2"])
def test_bad_conditions_raise(bad):
    with pytest.raises(rules.RuleError):
        rules.parse_cond(bad)


# -------------------------------------------------------------- effects

def test_meter_effects_scale_with_difficulty():
    for diff, loss, gain in (("recruit", 60, 85), ("officer", 50, 80), ("commander", 40, 75)):
        c = camp(diff)
        c.meters["security"], c.meters["intel"] = 70, 60
        run(["security -20", "intel +20"], c)
        assert (c.meters["security"], c.meters["intel"]) == (loss, gain), diff


def test_small_losses_never_round_away_to_zero():
    c = camp("recruit")
    run(["security -1"], c)
    assert c.meters["security"] == 69


def test_intel_losses_are_spent_unscaled():
    c = camp("commander")
    c.meters["intel"] = 60
    run(["intel -10"], c)
    assert c.meters["intel"] == 50


def test_meters_clamp_and_personnel_losses_are_recorded():
    c = camp()
    run(["personnel -200", "intel +500"], c)
    assert c.meters["personnel"] == 0 and c.meters["intel"] == 100 and c.record["personnel_lost"] == 80


def test_breach_ends_the_game_only_at_zero_security():
    c = camp()
    c.meters["security"] = 10
    run(["breach 30"], c)
    assert c.meters["security"] == 0 and c.over is None
    run(["breach 5"], c)
    assert c.over == "The SGC was overrun."


def test_capture_compromises_the_idc_silently_and_rescue_reissues():
    c = camp()
    msgs = run(["team {team} captured"], c, {"team": "SG-3", "destination": "Chulak"})
    t = c.teams["SG-3"]
    assert t.status == "captured" and t.idc == "compromised" and t.captured_at == "Chulak"
    assert t.out_cycles == 6
    assert msgs == ["SG-3 CAPTURED ON CHULAK"]
    run(["team {captive} base"], c, {"captive": "SG-3"})
    assert t.status == "base" and t.idc == "valid" and t.captured_at == "" and t.out_cycles == 2


def test_lost_team_counts_and_nox_save_once():
    c = camp()
    run(["team SG-2 lost"], c)
    assert c.teams["SG-2"].status == "lost" and c.record["teams_lost"] == 1 and c.teams["SG-2"].out_cycles == 4
    c.inventory.add("ally.nox")
    msgs = run(["team SG-4 lost"], c)
    assert c.teams["SG-4"].status == "injured" and "ally.nox" in c.used and "NOX" in msgs[0]
    assert c.teams["SG-4"].out_cycles == 3


def test_revoke_reissues_and_stands_the_team_down():
    c = camp()
    c.teams["SG-1"].idc = "compromised"
    run(["idc SG-1 revoke"], c)
    assert c.teams["SG-1"].idc == "valid" and c.teams["SG-1"].out_cycles == 2


def test_captured_team_is_presumed_lost_after_six_cycles():
    c = camp()
    run(["team SG-1 captured"], c, {"destination": "Chulak"})
    assert c.teams["SG-1"].out_cycles == 6
    msgs = []
    for _ in range(6):
        msgs = rules.start_cycle(c)
    t = c.teams["SG-1"]
    assert t.status == "lost" and t.idc == "revoked" and t.out_cycles == 4
    assert c.record["teams_lost"] == 1
    assert "SG-1 PRESUMED LOST" in msgs


def test_lord_defeat_wins_campaign_at_goal():
    c = camp()
    c.record["goauld_defeated"] = rules.GOAL - 1
    lord = c.lords[0]
    lord.strength = 1
    msgs = run(["goauld {goauld} strength -1", "goauld {goauld} aggression +15"], c, {"goauld": lord.name})
    assert lord.defeated and c.won and c.record["goauld_defeated"] == rules.GOAL
    assert any("HAS FALLEN" in m for m in msgs)


def test_endless_replaces_a_fallen_lord():
    c = camp(mode="endless")
    lord = c.lords[0]
    lord.strength = 1
    msgs = run(["goauld {goauld} strength -1"], c, {"goauld": lord.name})
    assert not c.won and len(c.active_lords()) == 5 and any("RISES" in m for m in msgs)


def test_gain_use_and_unless():
    c = camp()
    msgs = run(["gain ally.tokra", "gain tech.naquadah_generator"], c)
    assert c.inventory >= {"ally.tokra", "tech.naquadah_generator"} and len(msgs) == 2
    run(["security -20 unless tech.naquadah_generator"], c)
    assert c.meters["security"] == 70
    c.inventory.add("ally.asgard")
    run(["use ally.asgard"], c)
    assert "ally.asgard" in c.used


def test_game_over_fills_placeholders_and_stops_later_effects():
    c = camp()
    msgs = run(["game_over {goauld}'s Jaffa took the SGC.", "intel +50"], c, {"goauld": "Apophis"})
    assert c.over == "Apophis's Jaffa took the SGC." and c.meters["intel"] == 10 and msgs


def test_game_over_message_may_contain_the_word_unless():
    c = camp()
    msgs = run(["game_over Nothing could stop them unless you were ready"], c)
    assert c.over == "Nothing could stop them unless you were ready" and msgs


@pytest.mark.parametrize("bad", ["security 5", "breach 0", "team SG-1 dancing", "idc SG-1 lose", "gain ally.goauld",
                                 "goauld {goauld} charisma -1", "launch nukes", "intel +5 unless ally.goauld",
                                 "use tech.zat"])
def test_bad_effects_raise(bad):
    with pytest.raises(rules.RuleError):
        rules.parse_effect(bad)


# -------------------------------------------------------------- odds, cycles, rating

def test_odds_apply_mods_and_clamp():
    c = camp()
    c.inventory.add("tech.zat")
    mods = [rules.parse_mod(t) for t in ("tech.zat +20", "personnel < 40 -15", "ally.jaffa +30")]
    assert rules.odds(50, mods, c, {}) == 70
    assert rules.odds(90, mods, c, {}) == 95 and rules.odds(-50, [], c, {}) == 5
    with pytest.raises(rules.RuleError):
        rules.parse_mod("tech.zat 20")


def test_start_cycle_heals_and_reforms():
    c = camp()
    run(["team SG-1 injured", "team SG-2 lost"], c)
    for _ in range(2):
        rules.start_cycle(c)
    assert c.teams["SG-1"].status == "injured" and c.teams["SG-2"].status == "lost"
    msgs = rules.start_cycle(c)
    assert c.teams["SG-1"].status == "base" and "SG-1 BACK ON DUTY" in msgs
    assert c.teams["SG-2"].status == "lost"
    msgs2 = rules.start_cycle(c)
    assert c.teams["SG-2"].status == "base" and c.teams["SG-2"].idc == "valid" and "SG-2 RE-FORMED" in msgs2
    assert c.cycles == 4 and c.since_briefing == 4


def test_revoked_team_stands_down_for_two_cycles():
    c = camp()
    run(["idc SG-1 revoke"], c)
    rules.start_cycle(c)
    assert "SG-1" not in rules.available_teams(c)
    rules.start_cycle(c)
    assert "SG-1" in rules.available_teams(c)


def test_teams_matching_and_available():
    c = camp()
    c.teams["SG-2"].idc, c.teams["SG-2"].status = "compromised", "captured"
    c.teams["SG-4"].out_cycles = 1
    assert rules.teams_matching(c, "compromised") == ["SG-2"]
    assert rules.teams_matching(c, "captured") == ["SG-2"]
    assert rules.available_teams(c) == ["SG-1", "SG-3"]


def test_quiet_recovers_and_calms():
    c = camp()
    c.meters["personnel"] = 50
    before = [l.aggression for l in c.lords]
    c.meters["security"] = 50
    rules.quiet(c)
    assert c.meters["personnel"] == 52 and [l.aggression for l in c.lords] == [max(0, a - 3) for a in before]
    assert c.meters["security"] == 53
    c.meters["security"] = 99
    rules.quiet(c)
    assert c.meters["security"] == 100


def test_rating_bands():
    c = camp()
    c.cycles, c.record["personnel_lost"] = 80, 10
    c.inventory |= {"ally.tokra", "ally.asgard"}
    assert rules.rating(c)[0] == "OUTSTANDING"
    c = camp()
    c.record["teams_lost"], c.record["personnel_lost"], c.cycles = 4, 60, 150
    assert rules.rating(c)[0] == "UNDER REVIEW"


def test_a_100_cycle_win_with_a_few_losses_is_at_least_satisfactory():
    c = camp()
    c.cycles, c.record["personnel_lost"], c.record["teams_lost"] = 100, 30, 2
    assert rules.rating(c)[0] in ("OUTSTANDING", "COMMENDED", "SATISFACTORY")
