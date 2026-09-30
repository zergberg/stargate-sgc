import pytest

from sgc.game import clock, rules
from sgc.game.state import Mission, new_campaign


def camp(diff="officer", mode="campaign"):
    return new_campaign(mode, diff, 11)


def run(effect_texts, c, bind=None):
    return rules.apply_all([rules.parse_effect(t) for t in effect_texts], c, bind or {})


# -------------------------------------------------------------- conditions

@pytest.mark.parametrize("text,expected", [
    ("security >= 70", True), ("security > 70", False), ("personnel < 81", True), ("day == 1", True),
    ("day >= 2", False), ("ally.tokra", False), ("not ally.tokra", True), ("any_compromised_idc", False),
    ("any_captured", False), ("team SG-1 base", True), ("team {team} captured", False), ("team_available", True),
])
def test_conditions(text, expected):
    c = camp()
    assert rules.parse_cond(text)(c, {"team": "SG-2"}) is expected


def test_day_and_team_available_follow_the_clock():
    c = camp()
    c.minutes = 2 * clock.DAY + 1
    assert rules.parse_cond("day == 3")(c, {})
    for t in c.teams.values():
        t.status = "offworld"
    assert not rules.parse_cond("team_available")(c, {})


def test_single_use_flags_count_as_gone_once_used():
    c = camp()
    c.inventory.add("ally.asgard")
    assert rules.parse_cond("ally.asgard")(c, {})
    c.used.add("ally.asgard")
    assert not rules.parse_cond("ally.asgard")(c, {})


@pytest.mark.parametrize("bad", ["", "security >= lots", "moral > 3", "team SG-13 base", "team SG-1 napping",
                                 "not ally.goauld", "intel >= 10", "cycles >= 2", "goauld Apophis strength < 2"])
def test_bad_conditions_raise(bad):
    with pytest.raises(rules.RuleError):
        rules.parse_cond(bad)


# -------------------------------------------------------------- effects

def test_meter_effects_scale_with_difficulty():
    for diff, loss, gain in (("recruit", 60, 85), ("officer", 50, 80), ("commander", 40, 75)):
        c = camp(diff)
        c.meters["security"], c.meters["personnel"] = 70, 60
        run(["security -20", "personnel +20"], c)
        assert (c.meters["security"], c.meters["personnel"]) == (loss, gain), diff


def test_small_losses_never_round_away_to_zero():
    c = camp("recruit")
    run(["security -1"], c)
    assert c.meters["security"] == 69


def test_meters_clamp_and_personnel_losses_are_recorded():
    c = camp()
    run(["personnel -200", "security +500"], c)
    assert c.meters["personnel"] == 0 and c.meters["security"] == 100 and c.record["personnel_lost"] == 80


def test_breach_ends_the_game_only_at_zero_security():
    c = camp()
    c.meters["security"] = 10
    run(["breach 30"], c)
    assert c.meters["security"] == 0 and c.over is None
    run(["breach 5"], c)
    assert c.over == "The SGC was overrun."


def test_capture_compromises_the_idc_and_rescue_reissues():
    c = camp()
    msgs = run(["team {team} captured"], c, {"team": "SG-3", "world": "Chulak", "world_id": "P3X-100"})
    t = c.teams["SG-3"]
    assert t.status == "captured" and t.idc == "compromised" and t.where == "P3X-100"
    assert t.until == c.now + rules.CAPTIVE and msgs == ["SG-3 CAPTURED ON CHULAK"]
    run(["team SG-3 base"], c)
    assert t.status == "base" and t.idc == "valid" and t.where == "" and t.until == c.now + rules.STAND_DOWN


def test_lost_team_counts_and_nox_save_once():
    c = camp()
    run(["team SG-2 lost"], c)
    assert c.teams["SG-2"].status == "lost" and c.record["teams_lost"] == 1
    assert c.teams["SG-2"].until == c.now + rules.REFORM
    c.inventory.add("ally.nox")
    msgs = run(["team SG-4 lost"], c)
    assert c.teams["SG-4"].status == "injured" and "ally.nox" in c.used and "NOX" in msgs[0]
    assert c.teams["SG-4"].until == c.now + rules.INJURED


def test_revoke_reissues_and_stands_the_team_down():
    c = camp()
    c.teams["SG-1"].idc = "compromised"
    run(["idc SG-1 revoke"], c)
    assert c.teams["SG-1"].idc == "valid" and c.teams["SG-1"].until == c.now + 12 * 60
    run(["idc {team} compromise"], c, {"team": "SG-4"})
    assert c.teams["SG-4"].idc == "compromised"


def test_revoking_a_captured_team_leaves_its_capture_clock_alone():
    c = camp()
    tm = c.teams["SG-2"]
    tm.status, tm.idc, tm.until = "captured", "compromised", c.now + 3 * 60
    run(["idc SG-2 revoke"], c)
    assert tm.idc == "valid" and tm.status == "captured" and tm.until == c.now + 3 * 60


def test_revoking_keeps_the_longer_of_a_timer_already_running_and_the_stand_down():
    c = camp()
    injured, reforming, standing = c.teams["SG-2"], c.teams["SG-3"], c.teams["SG-4"]
    injured.status, injured.until = "injured", c.now + 2 * 60
    reforming.status, reforming.until = "lost", c.now + rules.REFORM
    standing.until = c.now + 20 * 60
    for name in ("SG-2", "SG-3", "SG-4"):
        run([f"idc {name} revoke"], c)
    assert injured.until == c.now + rules.STAND_DOWN and injured.status == "injured"
    assert reforming.until == c.now + rules.REFORM and standing.until == c.now + 20 * 60


def test_gain_use_and_unless():
    c = camp()
    assert run(["gain ally.asgard"], c) == ["ALLIANCE: THE ASGARD"]
    assert run(["gain ally.asgard"], c) == []
    assert run(["use ally.asgard"], c) == ["THE ASGARD CALLED IN"]
    assert run(["security -10 unless ally.asgard"], c) == ["SECURITY -10"]
    c.inventory.add("tech.naquadah_generator")
    assert run(["security -10 unless tech.naquadah_generator"], c) == []


def test_game_over_fills_placeholders_and_stops_later_effects():
    c = camp()
    msgs = run(["game_over {team} could not hold the gate room.", "security +10"], c, {"team": "SG-3"})
    assert c.over == "SG-3 could not hold the gate room." and msgs == ["SG-3 COULD NOT HOLD THE GATE ROOM."]
    assert c.meters["security"] == 70


def test_game_over_message_may_contain_the_word_unless():
    c = camp()
    run(["game_over Nothing survives unless we act."], c)
    assert c.over == "Nothing survives unless we act."


@pytest.mark.parametrize("bad", ["intel +5", "goauld Apophis aggression +5", "breach 0", "breach x",
                                 "team SG-1 napping", "gain ally.ori", "use tech.zat", "security +5 unless magic",
                                 "security 5", ""])
def test_bad_effects_raise(bad):
    with pytest.raises(rules.RuleError):
        rules.parse_effect(bad)


def test_odds_apply_mods_bonus_and_clamp():
    c = camp()
    c.inventory.add("tech.zat")
    mods = [rules.parse_mod("tech.zat +15"), rules.parse_mod("personnel < 40 -20")]
    assert rules.odds(50, mods, c, {}) == 65
    assert rules.odds(50, mods, c, {}, bonus=10) == 75
    assert rules.odds(90, mods, c, {}) == 95 and rules.odds(1, [], c, {}) == 5


# -------------------------------------------------------------- bookkeeping

def test_hourly_recovers_security_every_six_hours_and_personnel_every_four():
    c = camp()
    c.meters["security"], c.meters["personnel"] = 50, 50
    for h in range(9, 21):                       # 09:00 .. 20:00 on day 1
        c.minutes = h * 60
        rules.hourly(c)
    assert c.meters["security"] == 52 and c.meters["personnel"] == 53     # 12, 18 / 12, 16, 20


def test_hourly_runs_team_timers():
    c = camp()
    run(["team SG-1 captured", "team SG-2 injured", "team SG-3 lost"], c, {"world": "Chulak"})
    c.teams["SG-3"].xp = 9
    c.minutes += rules.INJURED
    assert rules.hourly(c) == ["SG-2 BACK ON DUTY"]
    c.minutes += rules.REFORM - rules.INJURED
    assert rules.hourly(c) == ["SG-3 RE-FORMED — GREEN"] and c.teams["SG-3"].xp == 0
    c.minutes += rules.CAPTIVE - rules.REFORM
    assert rules.hourly(c) == ["SG-1 PRESUMED LOST"] and c.teams["SG-1"].status == "lost"
    assert c.record["teams_lost"] == 2


def test_teams_matching():
    c = camp()
    c.teams["SG-2"].idc = "compromised"
    c.teams["SG-3"].status = "captured"
    c.teams["SG-4"].status = "lost"
    assert rules.teams_matching(c, "compromised") == ["SG-2"]
    assert rules.teams_matching(c, "captured") == ["SG-3"]
    assert rules.teams_matching(c, "base") == ["SG-1", "SG-2"]
    assert rules.teams_matching(c, "any") == ["SG-1", "SG-2", "SG-3"]


# -------------------------------------------------------------- worlds, teams and missions


def world_bind(c, i=3):
    w = list(c.worlds.values())[i]
    return w, {"world": w.name, "world_id": w.id, "designation": w.id, "team": "SG-2"}


def test_world_conditions_use_the_bound_world_or_a_designation():
    c = camp()
    w, b = world_bind(c)
    w.env, w.inhabitants, w.features, w.status = "toxic", "unas", ("ruins",), "probed"
    for text, expected in [
        ("known {world}", True), (f"known {w.id}", True), ("known P9Z-999", False),
        ("status {world} probed", True), ("status {world} surveyed", False),
        ("unlocked survey {world}", True), ("unlocked contact {world}", False),
        ("world {world} env toxic", True), ("world {world} inhabitants unas", True),
        ("world {world} feature ruins", True), ("world {world} feature naquadah", False),
    ]:
        assert rules.parse_cond(text)(c, b) is expected, text
    assert rules.parse_cond("status {world} probed")(c, {}) is False          # nothing bound


def test_team_specialty_and_rank_conditions():
    c = camp()
    c.teams["SG-2"].xp = 8
    b = {"team": "SG-2"}
    assert rules.parse_cond("team {team} specialty recon")(c, b)
    assert not rules.parse_cond("team {team} specialty diplomatic")(c, b)
    assert rules.parse_cond("team SG-1 specialty diplomatic")(c, b)             # SG-1 does everything
    assert rules.parse_cond("rank {team} >= veteran")(c, b) and not rules.parse_cond("rank {team} >= elite")(c, b)
    assert rules.parse_cond("rank SG-3 == green")(c, b)


@pytest.mark.parametrize("bad", ["known Chulak", "status {world} sunny", "unlocked picnic {world}",
                                 "world {world} env lava", "world {world} feature gold",
                                 "team {team} specialty cooking", "rank {team} >= admiral", "rank {team} ~ green"])
def test_bad_world_and_team_conditions_raise(bad):
    with pytest.raises(rules.RuleError):
        rules.parse_cond(bad)


def test_reveal_address_adds_a_lead_deterministically():
    a, b = camp(), camp()
    msgs = run(["reveal address"], a, {"team": "SG-4"})
    run(["reveal address"], b, {"team": "SG-4"})
    new = list(a.worlds.values())[-1]
    assert len(a.worlds) == 21 and msgs == [f"NEW ADDRESS: {new.id}"] and new.found == "intel from SG-4"
    assert list(b.worlds.values())[-1] == new and new.status == "unexplored"


def test_reveal_name_from_a_source_or_a_literal():
    c = camp()
    w, b = world_bind(c)
    w.hidden_names = {"locals": "Tel'kar", "jaffa": "Ha'shek"}
    assert run(["reveal name {world} from jaffa"], c, b) == [f"{w.id} IS CALLED HA'SHEK BY THE JAFFA"]
    assert run(["reveal name {world} from jaffa"], c, b) == []
    run(['reveal name {world} "The Hollow" from records'], c, b)
    assert [n for n, _, _ in w.names] == ["Ha'shek", "The Hollow"] and w.name == "The Hollow"
    assert w.names[1][1] == "SGC records" and w.names[1][2] == c.now


def test_unlock_status_and_the_surveyed_record():
    c = camp()
    w, b = world_bind(c)
    assert run(["unlock contact {world}"], c, b) == [f"CONTACT MISSIONS POSSIBLE ON {w.name.upper()}"]
    assert run(["unlock contact {world}"], c, b) == [] and w.options == ["survey", "contact"]
    assert run(["status {world} surveyed"], c, b) == [f"{w.name.upper()}: SURVEYED"]
    assert run(["status {world} probed"], c, b) == [] and w.status == "surveyed"
    assert c.record["surveyed"] == 1
    run(["status {world} contact"], c, b)
    assert c.record["surveyed"] == 1 and w.status == "contact"


def test_surveying_a_hostile_world_still_counts_but_leaves_it_hostile():
    c = camp()
    w, b = world_bind(c)
    run(["status {world} hostile"], c, b)
    assert run(["status {world} surveyed"], c, b) == [] and w.status == "hostile"
    assert c.record["surveyed"] == 1
    assert run(["status {world} surveyed"], c, b) == [] and c.record["surveyed"] == 1     # still once


def test_surveying_the_same_world_twice_counts_once():
    c = camp()
    w, b = world_bind(c)
    run(["status {world} surveyed"], c, b)
    run(["status {world} lost"], c, b)
    run(["status {world} probed"], c, b)
    run(["status {world} surveyed"], c, b)
    assert c.record["surveyed"] == 1


def test_a_lost_world_is_not_credited_as_surveyed_until_it_actually_is():
    c = camp()
    w, b = world_bind(c)
    run(["status {world} lost"], c, b)
    assert run(["status {world} surveyed"], c, b) == [] and w.status == "lost"    # blocked: not a real survey
    assert c.record["surveyed"] == 0
    assert run(["status {world} probed"], c, b) == [f"{w.name.upper()}: PROBED"]
    assert run(["status {world} surveyed"], c, b) == [f"{w.name.upper()}: SURVEYED"]
    assert c.record["surveyed"] == 1


def test_abydos_starts_at_contact_and_still_counts_as_surveyed():
    c = camp()
    abydos = next(iter(c.worlds.values()))
    b = {"world": abydos.name, "world_id": abydos.id, "team": "SG-2"}
    assert abydos.status == "contact"
    assert run(["status {world} surveyed"], c, b) == [] and abydos.status == "contact"
    assert c.record["surveyed"] == 1


def test_xp_promotes():
    c = camp()
    assert run(["xp {team} +2"], c, {"team": "SG-2"}) == []
    assert run(["xp {team} +1"], c, {"team": "SG-2"}) == ["SG-2 PROMOTED: SEASONED"]


def test_schedule_and_drone_effects():
    c = camp()
    w, b = world_bind(c)
    before = len(c.events)
    run(["schedule incoming in 6h"], c, b)
    assert len(c.events) == before + 1 and c.events.find(lambda e: e.due == c.now + 360)[0].kind == "incoming"
    w.drone = "malp"
    assert run(["drone {world} captured"], c, b) == [f"MALP ON {w.name.upper()} CAPTURED",
                                                       f"{w.name.upper()}: HOSTILE"]
    assert w.drone is None and w.status == "hostile"
    assert run(["drone {world} lost"], c, b) == []


def test_recall_brings_the_team_home_now_and_reinforce_sends_a_free_team():
    c = camp()
    w, b = world_bind(c)
    c.missions.append(Mission(1, "SG-2", w.id, "survey", c.now, c.now + 1440))
    c.teams["SG-2"].status, c.teams["SG-2"].where, c.teams["SG-2"].mission = "offworld", w.id, 1
    c.events.push(c.now + 60, "dial_out", {"mission": 1})
    c.events.push(c.now + 480, "checkin", {"mission": 1})
    c.events.push(c.now + 500, "overdue", {"mission": 1})
    c.events.push(c.now + 1440, "team_return", {"mission": 1})
    assert run(["reinforce {team}"], c, b) == ["SG-1 SENT TO REINFORCE SG-2"]
    assert c.teams["SG-1"].status == "offworld" and c.teams["SG-1"].where == w.id
    assert c.events.find(lambda e: e.data.get("team") == "SG-1")[0].due == c.now + rules.REINFORCE
    assert run(["recall {team}"], c, b) == ["SG-2 RECALLED"]
    home = c.events.find(lambda e: e.data.get("mission") == 1)
    assert [(e.kind, e.due) for e in home] == [("team_return", c.now)] and c.mission(1).end == c.now
    assert c.mission(1).state == "aborted"
    assert run(["recall SG-3"], c, b) == []


def test_reinforce_only_helps_an_offworld_team():
    c = camp()
    w, b = world_bind(c)
    assert run(["reinforce {team}"], c, b) == []                          # SG-2 is at home, not offworld
    c.teams["SG-2"].status, c.teams["SG-2"].where = "offworld", w.id
    assert run(["reinforce {team}"], c, b) == ["SG-1 SENT TO REINFORCE SG-2"]
    helper = c.teams["SG-1"]
    assert helper.status == "offworld" and helper.where == w.id and helper is not c.teams["SG-2"]


def test_missing_or_unknown_team_binds_are_a_no_op_not_a_crash():
    c = camp()
    assert run(["xp {team} +1"], c, {}) == []                                     # {team} unbound
    assert run(["recall {team}"], c, {"team": "SG-99"}) == []                     # not a real team
    assert rules.parse_cond("rank {team} >= veteran")(c, {}) is False
    assert rules.parse_cond("team {team} specialty recon")(c, {"team": "SG-99"}) is False
    assert rules.parse_cond("team {team} base")(c, {}) is False


@pytest.mark.parametrize("bad", ["reveal names", 'reveal name {world} "X" from gossip', "reveal name Chulak from locals",
                                 "unlock picnic {world}", "status {world} sunny", "xp {team} -1", "xp {team} +x",
                                 "schedule faction_action in 6h", "schedule incoming in 0h", "schedule incoming at 6h",
                                 "drone {world} stolen", "recall SG-13", "reinforce {world}"])
def test_bad_world_effects_raise(bad):
    with pytest.raises(rules.RuleError):
        rules.parse_effect(bad)
