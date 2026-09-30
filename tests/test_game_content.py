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
mods = ["intel >= 30 +10"]
[node.start.choice.outcome.roll.win]
effects = ["intel +15"]
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
    (('effects = ["intel +15"]', 'effects = ["intel +lots"]'), "whole number"),
    (('mods = ["intel >= 30 +10"]', 'mods = ["charm > 3 +10"]'), "unknown condition"),
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
    bad = GOOD.replace('label = "Close the iris"', 'label = "Close the iris"\nrequires = ["intel >= 5"]')
    with pytest.raises(ContentError, match="default"):
        parse(bad)


def test_missions_need_brief_and_risk():
    mission = GOOD.replace('kind = "incoming"', 'kind = "mission"\nmission_type = "science"')
    with pytest.raises(ContentError, match="brief"):
        parse(mission)
    ok = mission.replace('weight = 2', 'weight = 2\nbrief = "Survey {destination}"\nrisk = "low"')
    assert parse(ok).brief == "Survey {destination}"


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


CAPTIVE_UNBOUND = """
id = "t_captive_unbound"
kind = "incoming"
visual = "incoming"

[node.start]
text.full = "We must rescue {captive}."
default = "wait"

[[node.start.choice]]
key = "wait"
label = "Wait"
outcome = { effects = ["breach 10"], end = true }
"""


def test_unbound_captive_placeholder_is_rejected():
    with pytest.raises(ContentError, match="doesn't bind it"):
        parse(CAPTIVE_UNBOUND, "captive.toml")


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
requires = ["intel >= 30"]
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
    assert kinds.count("incoming") >= 12 and kinds.count("mission") >= 10


def test_user_files_override_and_bad_ones_are_skipped(tmp_path):
    (tmp_path / "mine.toml").write_text(GOOD.replace('"t_probe"', '"false_alarm"'))
    (tmp_path / "broken.toml").write_text("id = ")
    scenarios, warnings = content.load(user=tmp_path)
    assert scenarios["false_alarm"].source == "mine.toml"
    assert len(warnings) == 1 and "broken.toml" in warnings[0]


def test_bundled_incoming_scenarios():
    scenarios, warnings = content.load(user=None)
    incoming = {s.id for s in scenarios.values() if s.kind == "incoming"}
    assert {"stolen_idc", "jaffa_incursion", "naquadah_bomb", "infiltration_check", "tokra_envoy"} <= incoming
    assert len(incoming) >= 12 and warnings == []
