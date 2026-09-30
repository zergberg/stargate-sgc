import tomllib

import pytest

from sgc.game import content
from sgc.game.content import ContentError, parse_scenario

GOOD = """
id = "t_probe"
kind = "incoming"
weight = 2
visual = "incoming"
team = "any"

[node.start]
text.full = "A probe on the ramp. {team} is nearby."
text.minimal = "Object on the ramp."
default = "close"

[[node.start.choice]]
key = "study"
label = "Study it"
[node.start.choice.outcome.roll]
odds = 60
mods = ["security >= 30 +10"]
[node.start.choice.outcome.roll.win]
effects = ["security +15"]
end = true
[node.start.choice.outcome.roll.lose]
goto = "pulse"

[[node.start.choice]]
key = "close"
label = "Close the iris"
outcome = { visual = "iris_hold", end = true }

[node.pulse]
text.full = "It pulses!"
default = "run"

[[node.pulse.choice]]
key = "run"
label = "Evacuate"
outcome = { effects = ["breach 10"], end = true }
"""


def parse(text, source="t.toml"):
    return parse_scenario(tomllib.loads(text), source)


def test_parses_a_good_scenario():
    sc = parse(GOOD)
    assert sc.id == "t_probe" and sc.kind == "incoming" and sc.weight == 2 and sc.visual == ("incoming",)
    start = sc.nodes["start"]
    assert start.default == "close" and [c.key for c in start.choices] == ["study", "close"]
    roll = start.choices[0].outcome.roll
    assert roll.odds == 60 and roll.win.end and roll.lose.goto == "pulse"
    assert start.text["full"].startswith("A probe")


@pytest.mark.parametrize("change,message", [
    (('kind = "incoming"', 'kind = "outgoing"'), "kind"),
    (('visual = "incoming"', 'visual = "fireworks"'), "unknown visual"),
    (('goto = "pulse"', 'goto = "nowhere"'), "unknown node"),
    (('default = "close"', 'default = "hide"'), "default"),
    (('effects = ["security +15"]', 'effects = ["security +lots"]'), "whole number"),
    (('mods = ["security >= 30 +10"]', 'mods = ["charm > 3 +10"]'), "unknown condition"),
    (("{team} is nearby", "{teem} is nearby"), "placeholder"),
    (("weight = 2", "weight = 2\ncolour = 3"), "unknown key"),
    (('outcome = { effects = ["breach 10"], end = true }', 'outcome = { effects = ["breach 10"] }'),
     "goto, end, roll"),
])
def test_bad_content_is_reported_with_its_path(change, message):
    bad = GOOD.replace(*change)
    with pytest.raises(ContentError) as e:
        parse(bad, "bad.toml")
    assert str(e.value).startswith("bad.toml:") and message in str(e.value)


def test_node_that_can_never_end_is_rejected():
    loop = GOOD.replace('outcome = { effects = ["breach 10"], end = true }', 'outcome = { goto = "pulse" }')
    with pytest.raises(ContentError, match="never reach an end"):
        parse(loop)


def test_default_choice_may_not_have_requires():
    bad = GOOD.replace('label = "Close the iris"', 'label = "Close the iris"\nrequires = ["security >= 5"]')
    with pytest.raises(ContentError, match="default"):
        parse(bad)


CHECKIN = """
id = "t_checkin"
kind = "checkin"
mission_type = "survey"
when = ["world {world} feature ruins"]

[node.start]
text.full = "{team} ({specialty}) checks in from {world}, designation {designation}."
default = "log"

[[node.start.choice]]
key = "log"
label = "Log it"
outcome = { effects = ["xp {team} +1", "reveal name {world} from locals"], end = true }
"""


def test_checkins_bind_the_team_and_the_world():
    sc = parse(CHECKIN)
    assert sc.kind == "checkin" and sc.mission_type == "survey" and sc.nodes["start"].routine
    probe = CHECKIN.replace('kind = "checkin"\nmission_type = "survey"', 'kind = "probe"')
    with pytest.raises(ContentError, match="doesn't bind it"):
        parse(probe)
    with pytest.raises(ContentError, match="mission_type"):
        parse(CHECKIN.replace('"survey"', '"picnic"'))
    with pytest.raises(ContentError, match="mission_type"):
        parse(GOOD.replace('kind = "incoming"', 'kind = "incoming"\nmission_type = "survey"'))


def test_incoming_scenarios_can_play_when_a_team_comes_home():
    text = GOOD.replace('team = "any"', 'on = "team_return"').replace("{team} is nearby.", "{team} is back from {world}.")
    sc = parse(text)
    assert sc.on == "team_return" and sc.team is None
    with pytest.raises(ContentError, match="leave team out"):
        parse(GOOD.replace('team = "any"', 'team = "any"\non = "team_return"'))
    with pytest.raises(ContentError, match="on must be"):
        parse(CHECKIN.replace('kind = "checkin"', 'kind = "checkin"\non = "team_return"'))
    with pytest.raises(ContentError, match="doesn't bind it"):
        parse(GOOD.replace("{team} is nearby.", "{team} is on {world}."))


def test_hidden_world_traits_are_allowed_in_when():
    sc = parse(CHECKIN)
    assert sc.when


def test_hidden_world_traits_are_rejected_in_requires():
    bad = CHECKIN.replace(
        '[[node.start.choice]]\nkey = "log"',
        '[[node.start.choice]]\n'
        'key = "study"\n'
        'label = "Study more"\n'
        'requires = ["world {world} env normal"]\n'
        'outcome = { effects = ["xp {team} +1"], end = true }\n\n'
        '[[node.start.choice]]\n'
        'key = "log"',
    )
    with pytest.raises(ContentError, match="when"):
        parse(bad)


def test_hidden_world_traits_are_rejected_in_mods():
    bad = CHECKIN.replace(
        'outcome = { effects = ["xp {team} +1", "reveal name {world} from locals"], end = true }',
        '[node.start.choice.outcome.roll]\n'
        'odds = 60\n'
        'mods = ["world {world} env normal +10"]\n'
        '[node.start.choice.outcome.roll.win]\n'
        'effects = ["xp {team} +1"]\n'
        'end = true\n'
        '[node.start.choice.outcome.roll.lose]\n'
        'effects = ["xp {team} +1"]\n'
        'end = true',
    )
    with pytest.raises(ContentError, match="when"):
        parse(bad)


SITUATION = """
id = "t_situation"
kind = "incoming"
goauld = "any"

[node.start]
text.full = "No IDC. Jaffa, probably."
situation = "unknown_idc"
default = "closed"

[[node.start.choice]]
key = "closed"
label = "Keep the iris closed"
outcome = { visual = "iris_hold", end = true }

[[node.start.choice]]
key = "open_guarded"
label = "Open it under guard"
outcome = { effects = ["breach 10"], end = true }
"""


def test_the_unused_hostile_flag_is_gone():
    with pytest.raises(ContentError, match="hostile"):
        parse(SITUATION.replace('goauld = "any"', 'goauld = "any"\nhostile = true'))
    assert not hasattr(parse(SITUATION), "hostile")


def test_situation_nodes_are_checked_against_the_standing_orders():
    sc = parse(SITUATION)
    node = sc.nodes["start"]
    assert node.situation == "unknown_idc" and not node.routine and sc.goauld
    with pytest.raises(ContentError, match="unknown situation"):
        parse(SITUATION.replace('"unknown_idc"', '"alien_tea_party"'))
    with pytest.raises(ContentError, match='default must be "closed"'):
        parse(SITUATION.replace('default = "closed"', 'default = "open_guarded"'))
    with pytest.raises(ContentError, match="needs choices keyed open_guarded"):
        parse(SITUATION.replace('key = "open_guarded"', 'key = "open"'))


@pytest.mark.parametrize("change", [
    ("weight = 2", 'weight = 2\nbrief = "x"'),
    ("weight = 2", "weight = 2\ncaptive = true"),
    ('default = "close"', 'default = "close"\ncountdown = 5'),
])
def test_build_1_fields_are_gone(change):
    with pytest.raises(ContentError, match="unknown key"):
        parse(GOOD.replace(*change))


def test_build_1_kinds_and_picks_are_gone():
    with pytest.raises(ContentError, match="kind"):
        parse(GOOD.replace('kind = "incoming"', 'kind = "mission"'))
    with pytest.raises(ContentError, match="goauld"):
        parse(GOOD.replace('team = "any"', 'team = "any"\ngoauld = "aggressor"'))


@pytest.mark.parametrize("bad_text", ["{team:%%%}", "{team!x}", "{captive:>9}"])
def test_format_specs_and_conversions_are_rejected(bad_text):
    bad = GOOD.replace("A probe on the ramp. {team} is nearby.", bad_text)
    with pytest.raises(ContentError, match="format specs and conversions"):
        parse(bad)


TEAM_UNBOUND = """
id = "t_team_unbound"
kind = "incoming"
visual = "incoming"

[node.start]
text.full = "Standby."
default = "wait"

[[node.start.choice]]
key = "wait"
label = "Wait"
outcome = { effects = ["team {team} injured"], end = true }
"""


def test_unbound_team_placeholder_is_rejected():
    with pytest.raises(ContentError, match="doesn't bind it"):
        parse(TEAM_UNBOUND, "team.toml")


GOAULD_UNBOUND = """
id = "t_goauld_unbound"
kind = "incoming"
visual = "incoming"

[node.start]
text.full = "{goauld} forces approach."
default = "wait"

[[node.start.choice]]
key = "wait"
label = "Wait"
outcome = { effects = ["breach 10"], end = true }
"""


def test_unbound_goauld_placeholder_is_rejected():
    with pytest.raises(ContentError, match="doesn't bind it"):
        parse(GOAULD_UNBOUND, "goauld.toml")


LOOP_DEFAULT = """
id = "t_loop_default"
kind = "incoming"
visual = "incoming"

[node.start]
text.full = "Stuck."
default = "loop"

[[node.start.choice]]
key = "loop"
label = "Wait"
outcome = { goto = "start" }

[[node.start.choice]]
key = "exit"
label = "Force exit"
requires = ["security >= 30"]
outcome = { effects = ["breach 10"], end = true }
"""


def test_node_only_terminates_through_its_default_choice():
    with pytest.raises(ContentError, match="never reach an end"):
        parse(LOOP_DEFAULT, "loop.toml")


@pytest.mark.parametrize("bad_weight", ["nan", "inf"])
def test_weight_must_be_finite(bad_weight):
    bad = GOOD.replace("weight = 2", f"weight = {bad_weight}")
    with pytest.raises(ContentError, match="weight"):
        parse(bad)


def test_weight_type_error_is_readable():
    bad = GOOD.replace("weight = 2", "weight = [1, 2]")
    with pytest.raises(ContentError, match="weight must be a number"):
        parse(bad)


def test_every_bundled_scenario_loads():
    scenarios, warnings = content.load(user=None)
    assert warnings == []
    kinds = [s.kind for s in scenarios.values()]
    assert kinds.count("probe") >= 10 and kinds.count("checkin") >= 8 and kinds.count("debrief") >= 6
    assert kinds.count("incoming") >= 6


def test_bundled_situation_choices_say_what_the_standing_orders_say():
    from sgc.game.orders import SITUATIONS
    scenarios, _ = content.load(user=None)
    seen = set()
    for sc in scenarios.values():
        for name, n in sc.nodes.items():
            if not n.situation:
                continue
            orders = dict(SITUATIONS[n.situation].choices)
            for ch in n.choices:
                if ch.key in orders:
                    assert ch.label == orders[ch.key], f"{sc.id}:{name}:{ch.key}"
                    seen.add((n.situation, ch.key))
    assert len(seen) == sum(len(s.choices) for s in SITUATIONS.values() if s.id != "missed_checkin")
    assert max(len(label) for s in SITUATIONS.values() for _, label in s.choices) <= 32   # the 38-column panel


def test_bundled_content_covers_the_situations_and_mission_types():
    from sgc.game.orders import SITUATIONS
    scenarios, _ = content.load(user=None)
    used = {n.situation for sc in scenarios.values() for n in sc.nodes.values() if n.situation}
    assert used == set(SITUATIONS) - {"missed_checkin"}          # the engine raises missed check-ins itself
    for mtype in ("survey", "contact"):
        for kind in ("checkin", "debrief"):
            assert any(sc.kind == kind and sc.mission_type in (None, mtype) for sc in scenarios.values()), (kind, mtype)
    assert any(sc.on == "team_return" for sc in scenarios.values())
    assert {"stolen_idc", "jaffa_incursion", "naquadah_bomb", "unknown_code"} <= set(scenarios)


def test_user_files_override_and_bad_ones_are_skipped(tmp_path):
    (tmp_path / "mine.toml").write_text(GOOD.replace('"t_probe"', '"false_alarm"'))
    (tmp_path / "broken.toml").write_text("id = ")
    scenarios, warnings = content.load(user=tmp_path)
    assert scenarios["false_alarm"].source == "mine.toml"
    assert len(warnings) == 1 and "broken.toml" in warnings[0]


FACTION = """
id = "t_faction"
kind = "faction"
stage = "curious"
visual = "incoming"

[node.start]
text.full = "A probe rolls through. Jaffa loyal to {faction}, Teal'c thinks."
situation = "object"
default = "seal"

[[node.start.choice]]
key = "seal"
label = "Seal the level"
outcome = { effects = ["attention {faction} +5"], end = true }

[[node.start.choice]]
key = "push_back"
label = "Dial out, push it back through"
outcome = { effects = ["reveal faction {faction} from jaffa"], end = true }
"""


def test_faction_scenarios_bind_the_faction_and_need_a_stage():
    sc = parse(FACTION)
    assert sc.kind == "faction" and sc.stage == "curious" and sc.arc is None
    with pytest.raises(ContentError, match="stage"):
        parse(FACTION.replace('stage = "curious"\n', ""))
    with pytest.raises(ContentError, match="stage"):
        parse(FACTION.replace('"curious"', '"furious"'))
    with pytest.raises(ContentError, match="stage"):
        parse(GOOD.replace("weight = 2", 'weight = 2\nstage = "curious"'))
    with pytest.raises(ContentError, match="doesn't bind it"):
        parse(FACTION.replace('kind = "faction"\nstage = "curious"', 'kind = "incoming"'))


def test_a_territory_team_is_on_one_of_the_factions_worlds():
    sc = parse(FACTION.replace('visual = "incoming"', 'team = "territory"')
               .replace("Teal'c thinks.", "{team} is pinned down on {world}."))
    assert sc.team == "territory"
    with pytest.raises(ContentError, match="team must be"):
        parse(GOOD.replace('team = "any"', 'team = "territory"'))
    assert parse(FACTION.replace('visual = "incoming"', 'team = "compromised"')).team == "compromised"


ARC = """
id = "t_arc"
kind = "arc"
arc = "apophis"
arc_stage = 2

[node.start]
text.full = "Jaffa loyal to {faction} come through from {world} ({designation})."
default = "brace"

[[node.start.choice]]
key = "brace"
label = "Brace"
outcome = { effects = ["arc apophis advance", "attention {owner} +5"], end = true }
"""


@pytest.mark.parametrize("change", [('arc = "apophis"\n', ""), ('"apophis"', '"ghost"'),
                                    ("arc_stage = 2", "arc_stage = 9"), ("arc_stage = 2", 'arc_stage = "2"'),
                                    ("arc_stage = 2\n", "")])
def test_arc_scenarios_name_an_arc_and_one_of_its_stages(change):
    assert (parse(ARC).arc, parse(ARC).arc_stage) == ("apophis", 2)
    with pytest.raises(ContentError, match="arc"):
        parse(ARC.replace(*change))


def test_only_arc_scenarios_take_arc_keys():
    with pytest.raises(ContentError, match="arc"):
        parse(GOOD.replace("weight = 2", 'weight = 2\narc = "apophis"\narc_stage = 1'))


def test_owner_may_only_appear_in_effects_and_conditions():
    with pytest.raises(ContentError, match="owner"):
        parse(ARC.replace("({designation})", "({owner})"))
    with pytest.raises(ContentError, match="owner"):
        parse(ARC.replace('label = "Brace"', 'label = "Brace for {owner}"'))
    with pytest.raises(ContentError, match="doesn't bind it"):
        parse(FACTION.replace("attention {faction} +5", "attention {owner} +5"))     # no world bound


def test_a_rescue_binds_the_captive():
    rescue = CHECKIN.replace('mission_type = "survey"', 'mission_type = "rescue"').replace(
        "designation {designation}.", "looking for {captive}.")
    assert parse(rescue).mission_type == "rescue"
    with pytest.raises(ContentError, match="doesn't bind it"):
        parse(CHECKIN.replace("designation {designation}.", "looking for {captive}."))


@pytest.mark.parametrize("hidden", ["attention {faction} >= 20", "stage {faction} hostile", "is {world} @Chulak"])
def test_hidden_conditions_are_rejected_outside_when(hidden):
    bad = ARC.replace('label = "Brace"', f'label = "Brace"\nrequires = ["{hidden}"]').replace(
        'default = "brace"', 'default = "wait"\n\n[[node.start.choice]]\nkey = "wait"\nlabel = "Wait"\n'
                             'outcome = { end = true }')
    with pytest.raises(ContentError, match="when"):
        parse(bad)
    assert parse(ARC.replace('arc_stage = 2', f'arc_stage = 2\nwhen = ["{hidden}"]')).when


@pytest.mark.parametrize("cond", ["aware {owner}", "trust {owner} >= 50"])
def test_a_condition_on_the_worlds_holder_is_hidden(cond):
    wait = ('default = "wait"\n\n[[node.start.choice]]\nkey = "wait"\nlabel = "Wait"\noutcome = { end = true }')
    with pytest.raises(ContentError, match="when"):
        parse(ARC.replace('label = "Brace"', f'label = "Brace"\nrequires = ["{cond}"]')
              .replace('default = "brace"', wait))
    with pytest.raises(ContentError, match="when"):
        parse(GOOD.replace('mods = ["security >= 30 +10"]', f'mods = ["{cond} +10"]')
              .replace('team = "any"', 'on = "team_return"').replace("{team} is nearby.", "{team} is home."))
    assert parse(ARC.replace("arc_stage = 2", f'arc_stage = 2\nwhen = ["{cond}"]')).when


def test_new_mission_types_are_accepted():
    for mtype in ("trade", "raid", "study", "rescue", "recover", "mine", "aid"):
        assert parse(CHECKIN.replace('"survey"', f'"{mtype}"')).mission_type == mtype


# ------------------------------------------------------------------ continuity: nobody is known before first contact

UNKNOWN = "before the SGC knows it"


def test_a_goauld_is_named_only_when_the_scenario_guarantees_the_sgc_knows_him():
    named = FACTION.replace("{faction}, Teal'c thinks", "Apophis, Teal'c thinks")
    with pytest.raises(ContentError, match=UNKNOWN) as e:
        parse(named, "named.toml")
    assert str(e.value).startswith("named.toml:node.start.text.full:") and "Apophis" in str(e.value)
    assert parse(named.replace('stage = "curious"', 'stage = "curious"\nwhen = ["aware apophis"]'))
    with pytest.raises(ContentError, match=UNKNOWN):           # his attention on Earth isn't our knowing him
        parse(named.replace('stage = "curious"', 'stage = "curious"\nwhen = ["stage apophis curious"]'))
    with pytest.raises(ContentError, match=UNKNOWN):           # shouting doesn't help
        parse(FACTION.replace("{faction}, Teal'c thinks", "APOPHIS, Teal'c thinks"))


def test_requires_greys_a_label_out_but_still_shows_it_so_it_guarantees_nothing():
    asgard = FACTION.replace('label = "Seal the level"', 'label = "Call the Asgard"\nrequires = ["ally.asgard"]')
    asgard = asgard.replace('default = "seal"', 'default = "push_back"').replace('situation = "object"\n', "")
    with pytest.raises(ContentError, match=UNKNOWN) as e:
        parse(asgard)
    assert "choice[0].label" in str(e.value)
    assert parse(asgard.replace('stage = "curious"', 'stage = "curious"\nwhen = ["ally.asgard"]'))


def test_an_arc_scenario_knows_its_own_arc_but_not_the_arcs_secrets():
    assert parse(ARC.replace("Jaffa loyal to {faction}", "Apophis's Jaffa").replace("{world} (", "Chulak ("))
    tokra = ARC.replace('"apophis"', '"tokra"').replace("arc apophis advance", "arc tokra advance")
    assert parse(tokra.replace("Jaffa loyal to {faction}", "Tok'ra agents"))
    with pytest.raises(ContentError, match=UNKNOWN):           # the arc's title never says Vorash
        parse(tokra.replace("{world} (", "Vorash ("))
    with pytest.raises(ContentError, match=UNKNOWN):           # nor does the Apophis arc know Heru'ur
        parse(ARC.replace("Jaffa loyal to {faction}", "Heru'ur's Jaffa"))


@pytest.mark.parametrize("when,ok", [("arc thor active", True), ("arc thor resolved", True),
                                     ("arc thor stage >= 2", True), ("arc thor dormant", False),
                                     ("known @Cimmeria", False), ("is {world} @Cimmeria", False)])
def test_an_arc_under_way_means_its_title_and_faction_are_known(when, ok):
    text = CHECKIN.replace('when = ["world {world} feature ruins"]', f'when = ["{when}"]').replace(
        "designation {designation}.", "near Thor's Hammer on Cimmeria. The Asgard built it.")
    if ok:
        assert parse(text)
    else:
        with pytest.raises(ContentError, match=UNKNOWN):
            parse(text)


REVEAL = """
id = "t_reveal"
kind = "checkin"
when = ["arc apophis active"]              # a prisoner on Chulak (arcs are Campaign only)

[node.start]
text.full = "A prisoner on {world} whispers of the Tok'ra."
default = "listen"

[[node.start.choice]]
key = "listen"
label = "Hear him out"
outcome = { effects = ["reveal faction tokra from jaffa"], goto = "after" }

[[node.start.choice]]
key = "free"
label = "Free him"
[node.start.choice.outcome.roll]
odds = 50
[node.start.choice.outcome.roll.win]
effects = ["arc tokra start"]
goto = "after"
[node.start.choice.outcome.roll.lose]
effects = ["team {team} injured", "gain ally.tokra"]
end = true

[node.after]
text.full = "The Tok'ra will want to talk."
default = "ok"

[[node.after.choice]]
key = "ok"
label = "Noted"
outcome = { end = true }
"""


def test_the_scenario_that_reveals_a_name_may_use_it():
    assert parse(REVEAL)
    with pytest.raises(ContentError, match=UNKNOWN):           # a choice that files nothing
        parse(REVEAL.replace('effects = ["arc tokra start"]', 'effects = ["xp {team} +1"]'))
    with pytest.raises(ContentError, match=UNKNOWN):           # a reveal that may not happen
        parse(REVEAL.replace('"reveal faction tokra from jaffa"', '"reveal faction tokra from jaffa unless tech.zat"'))
    after = REVEAL.replace("whispers of the Tok'ra.", "whispers of friends.").replace(
        'effects = ["reveal faction tokra from jaffa"], goto', "goto")
    with pytest.raises(ContentError, match=UNKNOWN) as e:      # one path to the node never learned it
        parse(after)
    assert "node.after.text.full" in str(e.value)


def test_starting_an_arc_reveals_its_names_only_where_arcs_run():
    sandbox = REVEAL.replace('when = ["arc apophis active"]', "")
    with pytest.raises(ContentError, match=UNKNOWN):           # Sandbox has no arcs: the start does nothing
        parse(sandbox)
    assert parse(sandbox.replace('"arc tokra start"', '"arc tokra start", "reveal faction tokra from jaffa"'))


def test_a_world_name_is_revealed_only_once_the_world_is_on_the_dialing_list():
    vorash = REVEAL.replace("Tok'ra", "Vorash").replace('"reveal faction tokra from jaffa"',
                                                        '"reveal name @Vorash from allies"')
    vorash = vorash.replace('"arc tokra start"', '"reveal name @Vorash from allies"')
    vorash = vorash.replace('"gain ally.tokra"', '"reveal name @Vorash from allies"')
    with pytest.raises(ContentError, match=UNKNOWN):           # Vorash is unlisted: the name reveal does nothing
        parse(vorash)
    assert parse(vorash.replace('"reveal name @Vorash from allies"',
                                '"reveal address @Vorash", "reveal name @Vorash from allies"'))


def test_a_game_over_message_may_not_name_a_stranger():
    doom = GOOD.replace('effects = ["breach 10"], end = true', 'effects = ["game_over Sokar took the SGC."]')
    with pytest.raises(ContentError, match=UNKNOWN):
        parse(doom)
    assert parse(doom.replace("weight = 2", 'weight = 2\nwhen = ["aware sokar"]'))
    with pytest.raises(ContentError, match="goauld"):          # the message is shown, so it's text
        parse(doom.replace("Sokar", "{goauld}").replace("weight = 2", 'weight = 2\ngoauld = "any"'))


def test_goauld_any_names_a_random_goauld_so_texts_may_not_show_it():
    with pytest.raises(ContentError, match="goauld"):
        parse(SITUATION.replace("No IDC. Jaffa", "No IDC. {goauld}'s Jaffa"))


def test_names_are_matched_as_whole_words():
    tollan = CHECKIN.replace('when = ["world {world} feature ruins"]', 'when = ["aware tollan"]')
    assert parse(tollan.replace("designation {designation}.", "the Tollan are here."))
    with pytest.raises(ContentError, match=UNKNOWN):
        parse(tollan.replace("designation {designation}.", "the ruins of Tollana."))
