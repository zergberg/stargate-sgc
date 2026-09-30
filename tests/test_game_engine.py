import random
import tomllib

from sgc.addresses import AddressPicker, load_canon
from sgc.config import Config
from sgc.director import Director
from sgc.events import REGISTRY
from sgc.game.content import parse_scenario
from sgc.game.engine import Engine
from sgc.game.state import new_campaign, to_dict

CHOICE = """
id = "t_choice"
kind = "incoming"
visual = "incoming"
[node.start]
text.full = "Decide."
countdown = 5
default = "safe"
[[node.start.choice]]
key = "bold"
label = "Bold"
outcome = { effects = ["intel +50"], end = true }
[[node.start.choice]]
key = "safe"
label = "Safe"
outcome = { effects = ["security -10"], end = true }
[[node.start.choice]]
key = "locked"
label = "Locked"
requires = ["intel >= 99"]
outcome = { effects = ["intel +1"], end = true }
"""

DOOM = """
id = "t_doom"
kind = "incoming"
[node.start]
text.full = "It's a trap."
default = "open"
[[node.start.choice]]
key = "open"
label = "Open the iris"
outcome = { visual = "firefight", effects = ["game_over The Jaffa took the SGC."] }
"""

STOLEN = """
id = "t_stolen"
kind = "incoming"
team = "compromised"
when = ["any_compromised_idc"]
[node.start]
text.full = "IDC from {team}, captured on {captured_at}."
text.minimal = "IDC received."
default = "hold"
[[node.start.choice]]
key = "hold"
label = "Hold"
outcome = { end = true }
"""

RECON = """
id = "t_recon"
kind = "mission"
mission_type = "recon"
goauld = "any"
risk = "low"
brief = "Recon {goauld} at {destination}"
[node.start]
text.full = "{team} is on {destination}."
default = "home"
[[node.start.choice]]
key = "home"
label = "Come home"
outcome = { visual = "team_return", effects = ["intel +20"], end = true }
"""

STRIKE = """
id = "t_strike"
kind = "mission"
mission_type = "strike"
goauld = "weakest"
risk = "high"
when = ["goauld {goauld} strength <= 1"]
brief = "Strike {goauld}"
[node.start]
text.full = "{goauld} is in reach."
default = "hit"
[[node.start.choice]]
key = "hit"
label = "Take the shot"
outcome = { visual = "team_return", effects = ["goauld {goauld} strength -1"], end = true }
"""


def scen(*texts):
    out = {}
    for i, t in enumerate(texts):
        sc = parse_scenario(tomllib.loads(t), f"t{i}.toml")
        out[sc.id] = sc
    return out


class Rig:
    def __init__(self, scenarios, campaign=None, difficulty="officer", mode="campaign"):
        self.d = Director(Config(), random.Random(1), AddressPicker(load_canon(), 0.6, random.Random(1)), REGISTRY)
        self.c = campaign or new_campaign(mode, difficulty, 7)
        self.saves, self.ended, self.logs = [], [], []
        self.e = Engine(self.d, self.c, scenarios, transition=1.0, countdown=4.0,
                        save=lambda c: self.saves.append(to_dict(c)), on_end=self.ended.append,
                        log=self.logs.append)
        self.e.begin()

    def run(self, until, limit=4000, dt=0.25, answer=None):
        for _ in range(limit):
            logs, _ = self.d.advance(dt)
            self.logs += logs
            self.e.update(dt)
            if answer and self.d.scene.prompt is not None and self.e.mode in ("decision", "briefing"):
                key = answer(self.e)
                if key:
                    self.e.key(key)
            if until():
                return
        raise AssertionError(f"condition not reached; mode={self.e.mode} cycles={self.c.cycles}")


def test_countdown_timeout_takes_the_default():
    r = Rig(scen(CHOICE))
    r.e.queue_scenario("t_choice")
    r.run(lambda: r.e.mode == "decision")
    p = r.d.scene.prompt
    assert p.total == 5 and [ok for _, ok in p.options] == [True, True, False]
    r.run(lambda: r.e.mode != "decision")
    assert r.c.meters["security"] == 60 and any("STANDING PROCEDURE" in line for line in r.logs)


def test_keys_pick_enabled_choices_only():
    r = Rig(scen(CHOICE))
    r.e.queue_scenario("t_choice")
    r.run(lambda: r.e.mode == "decision")
    assert r.e.key("3") and r.e.mode == "decision"
    r.e.key("1")
    assert r.e.mode == "busy" and r.c.meters["intel"] == 60 and r.d.scene.prompt is None
    assert r.saves and r.saves[-1]["meters"]["intel"] == 60
    r.run(lambda: r.c.cycles >= 2)


def test_game_over_ends_the_campaign():
    r = Rig(scen(DOOM))
    r.e.queue_scenario("t_doom")
    r.run(lambda: r.e.mode == "decision")
    r.e.key("1")
    assert r.c.over == "The Jaffa took the SGC." and r.ended == [r.c]
    r.run(lambda: r.e.mode == "over")
    assert r.d.scene.prompt.title == "BASE OVERRUN"
    n = len(r.saves)
    r.e.save_now()
    assert len(r.saves) == n                 # a fallen campaign is never saved again
    r.e.key("1")
    assert r.e.finished


def test_compromised_idc_binds_the_team_and_difficulty_hides_detail():
    for diff, expected in (("officer", "captured on Chulak"), ("commander", "IDC received.")):
        c = new_campaign("campaign", diff, 7)
        c.teams["SG-3"].status, c.teams["SG-3"].idc, c.teams["SG-3"].captured_at = "captured", "compromised", "Chulak"
        r = Rig(scen(STOLEN), campaign=c)
        r.e.queue_scenario("t_stolen")
        r.run(lambda: r.e.mode == "decision")
        assert expected in r.d.scene.prompt.text, diff


def test_briefing_sends_a_team_and_brings_it_home():
    r = Rig(scen(RECON))
    r.c.since_briefing = 2
    r.run(lambda: r.e.mode == "briefing")
    p = r.d.scene.prompt
    assert p.title == "MISSION BRIEFING" and p.options[-1][0] == "Stand down" and "LOW RISK" in p.options[0][0]
    assert r.d.scene.view_p == 0.0
    r.e.key("1")
    assert r.c.teams["SG-1"].status == "offworld" and r.c.record["missions"] == 1
    r.run(lambda: r.e.mode == "decision")
    assert r.d.scene.view_p == 1.0 and "SG-1" in r.d.scene.prompt.text
    r.e.key("1")
    r.run(lambda: r.c.cycles >= 2)
    assert r.c.meters["intel"] == 30 and r.c.teams["SG-1"].status == "base"


def test_standing_down_walks_back_down():
    r = Rig(scen(RECON))
    r.c.since_briefing = 2
    r.run(lambda: r.e.mode == "briefing")
    r.e.key("2")
    r.run(lambda: r.c.cycles >= 2)
    assert r.d.scene.view_p == 1.0 and r.c.record["missions"] == 0


def test_strike_on_the_last_lord_wins_and_debriefs():
    r = Rig(scen(STRIKE))
    r.c.record["goauld_defeated"] = 2
    weakest = r.c.lords[0]
    weakest.strength = 1
    r.e.queue_scenario("t_strike")
    r.run(lambda: r.e.mode == "decision")
    assert weakest.name in r.d.scene.prompt.text
    r.e.key("1")
    r.run(lambda: r.e.mode == "debrief")
    assert r.c.won and weakest.defeated and r.ended == [r.c]
    assert "RATING" in r.d.scene.prompt.text


def test_an_eligible_strike_opens_an_early_briefing():
    r = Rig(scen(RECON, STRIKE))
    r.c.lords[0].strength = 1
    r.run(lambda: r.e.mode == "briefing")
    assert r.c.cycles == 1 and any("Strike" in label for label, _ in r.d.scene.prompt.options)


def test_no_early_briefing_without_an_eligible_strike():
    r = Rig(scen(RECON, STRIKE))
    r.run(lambda: r.e.mode == "briefing")
    assert r.c.cycles == 3


def test_r_does_nothing_once_the_campaign_is_over():
    r = Rig(scen(DOOM))
    r.e.queue_scenario("t_doom")
    r.run(lambda: r.e.mode == "decision")
    r.e.key("1")
    assert r.e.mode == "busy" and not r.e.key("r") and not r.e._revoke_pending
    r.run(lambda: r.e.mode == "over")
    assert not r.e.key("r") and not r.e._revoke_pending


def test_revoke_request_opens_between_cycles():
    r = Rig(scen(CHOICE))
    r.c.teams["SG-2"].idc = "compromised"
    assert r.e.key("r")
    r.run(lambda: r.e.mode == "revoke")
    labels = [label for label, _ in r.d.scene.prompt.options]
    assert labels[-1] == "Cancel" and not any("COMPROMISED" in label for label in labels)
    r.e.key("2")
    assert r.c.teams["SG-2"].idc == "valid" and r.c.teams["SG-2"].out_cycles == 2 and r.e.mode == "idle"


def test_scene_team_labels_follow_the_campaign():
    r = Rig(scen(CHOICE))
    r.c.teams["SG-4"].status = "captured"
    r.e.update(0.0)
    assert r.d.scene.teams["SG-4"] == "MISSING" and r.d.scene.teams["SG-1"] == "AT BASE"


RECON2 = """
id = "t_recon2"
kind = "mission"
mission_type = "recon"
risk = "low"
brief = "Recon {destination}"
[node.start]
text.full = "{team} is on {destination}."
default = "on"
[[node.start.choice]]
key = "on"
label = "Press on"
outcome = { goto = "deeper" }
[node.deeper]
text.full = "{team} is deep inside."
default = "home"
[[node.deeper.choice]]
key = "home"
label = "Come home"
outcome = { visual = "team_return", end = true }
"""

CAPTURE = """
id = "t_capture"
kind = "mission"
mission_type = "recon"
risk = "high"
brief = "Walk into a trap at {destination}"
[node.start]
text.full = "{team} is surrounded."
default = "give"
[[node.start.choice]]
key = "give"
label = "Surrender"
outcome = { effects = ["team {team} captured"], end = true }
"""

TWO_VISUALS = """
id = "t_two"
kind = "incoming"
visual = ["incoming", "malp"]
[node.start]
text.full = "Two things at once."
default = "ok"
[[node.start.choice]]
key = "ok"
label = "Carry on"
outcome = { end = true }
"""

SILENT = """
id = "t_silent"
kind = "incoming"
[node.start]
text.full = ""
default = "ok"
[[node.start.choice]]
key = "ok"
label = "Carry on"
outcome = { end = true }
"""


def test_a_mid_mission_save_brings_the_team_home_on_load():
    from sgc.game.rules import available_teams
    from sgc.game.state import from_dict
    r = Rig(scen(RECON2))
    r.e.queue_scenario("t_recon2")
    r.run(lambda: r.e.mode == "decision")
    r.e.key("1")
    r.run(lambda: r.e.mode == "decision")
    saved = r.saves[-1]
    assert saved["teams"]["SG-1"]["status"] == "offworld"
    b = Rig(scen(RECON2), campaign=from_dict(saved))
    assert b.c.teams["SG-1"].status == "base" and "SG-1" in available_teams(b.c)
    assert "SG-1 RECALLED TO BASE" in b.logs


def _play(dt):
    from sgc.game import content
    scenarios, _ = content.load(user=None)
    r = Rig(scenarios)
    r.run(lambda: r.c.cycles >= 6 or r.e.mode in ("over", "debrief"), limit=100000, dt=dt,
          answer=lambda e: "1" if e.mode == "briefing" else None)
    d = to_dict(r.c)
    d.pop("rng_state", None)
    return d, r.e.rng.getstate()


def test_campaign_randomness_does_not_depend_on_the_frame_rate():
    slow, fast = _play(0.25), _play(0.6)
    assert slow[0]["cycles"] >= 6
    assert slow[0] == fast[0]
    assert slow[1] == fast[1]


def test_quitting_at_the_briefing_board_keeps_the_briefing():
    from sgc.game.state import from_dict
    r = Rig(scen(RECON))
    r.c.since_briefing = 2
    r.run(lambda: r.e.mode == "briefing")
    r.e.save_now()
    n = r.c.cycles
    b = Rig(scen(RECON), campaign=from_dict(r.saves[-1]))
    b.run(lambda: b.e.mode == "briefing" or b.c.cycles >= n + 2)
    assert b.e.mode == "briefing" and b.c.cycles == n + 1


def test_each_visual_is_built_against_the_scene_it_plays_on():
    r = Rig(scen(TWO_VISUALS))
    r.e.queue_scenario("t_two")
    seen = []
    for _ in range(4000):
        logs, _ = r.d.advance(0.25)
        if any(line.startswith("DIALING") for line in logs):
            seen.append(r.d.scene.horizon)
        r.e.update(0.25)
        if r.e.mode == "decision":
            break
    assert seen == ["off"]


def test_empty_text_does_not_crash_the_engine():
    r = Rig(scen(SILENT))
    r.e.queue_scenario("t_silent")
    r.run(lambda: r.e.mode == "decision")
    assert r.d.scene.prompt.text == ""


def test_redaction_follows_the_scenarios_own_info_level():
    for tokra, expected in ((False, "SG-1 MISSED CHECK-IN"), (True, "SG-1 CAPTURED ON")):
        c = new_campaign("campaign", "commander", 7)
        if tokra:
            c.inventory.add("ally.tokra")
        r = Rig(scen(CAPTURE), campaign=c)
        r.e.queue_scenario("t_capture")
        r.run(lambda: r.e.mode == "decision")
        r.e.key("1")
        assert any(line.startswith(expected) for line in r.logs), tokra


def test_a_saved_win_goes_straight_to_the_debrief():
    c = new_campaign("campaign", "officer", 7)
    c.won = True
    r = Rig(scen(CHOICE), campaign=c)
    assert r.e.mode == "debrief" and r.ended == [c] and "RATING" in r.d.scene.prompt.text


def test_quiet_cycles_skip_the_traffic_event():
    from sgc.game.engine import QUIET_EVENTS
    assert "traffic" not in QUIET_EVENTS


def test_game_over_plays_the_outcome_visual_before_the_verdict():
    r = Rig(scen(DOOM))
    r.e.queue_scenario("t_doom")
    r.run(lambda: r.e.mode == "decision")
    r.e.key("1")
    r.run(lambda: r.e.mode == "over")
    assert r.logs.index("FIREFIGHT IN THE GATE ROOM") < r.logs.index("THE SGC HAS FALLEN")


from sgc.game import content
from sgc.game.state import from_dict


def _briefings_only(e):
    return "1" if e.mode == "briefing" else None     # decisions time out to the cautious default


def test_save_and_load_resume_identically():
    scenarios, _ = content.load(user=None)
    a = Rig(scenarios)
    a.run(lambda: a.c.cycles >= 9 or a.e.mode in ("over", "debrief"), limit=40000, answer=_briefings_only)
    assert a.c.cycles >= 9, "a cautious run should survive nine cycles"
    at = max(i for i, s in enumerate(a.saves) if s["cycles"] == 4)
    b = Rig(scenarios, campaign=from_dict(a.saves[at]))
    b.run(lambda: b.c.cycles >= 9 or b.e.mode in ("over", "debrief"), limit=40000, answer=_briefings_only)
    later = [s for s in a.saves[at + 1:] if s["cycles"] <= 8]
    assert later and b.saves[:len(later)] == later


def test_bundled_content_survives_a_long_reckless_campaign():
    scenarios, _ = content.load(user=None)
    r = Rig(scenarios, difficulty="recruit")
    r.run(lambda: r.c.cycles >= 25 or r.e.mode in ("over", "debrief"), limit=80000, answer=lambda e: "1")
    assert r.c.cycles >= 1 and r.c.record["missions"] >= 1
