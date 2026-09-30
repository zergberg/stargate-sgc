from sgc.game import arcs, clock, scoring
from sgc.game.state import Mission, Team, new_campaign


def test_the_score_counts_program_growth():
    c = new_campaign("sandbox", "officer", 4)
    assert scoring.score(c) == 0
    c.record["surveyed"] = 7
    c.inventory |= {"ally.tokra", "tech.zat"}
    c.teams["SG-5"] = Team("medical")
    w = next(iter(c.worlds))
    c.missions += [Mission(1, "SG-2", w, "survey", 0, 1, state="complete"),
                   Mission(2, "SG-3", w, "survey", 0, 1, state="aborted")]
    assert scoring.score(c) == 70 + 50 + 20 + 2


def test_resolved_arcs_add_to_a_campaign_score():
    c = new_campaign("campaign", "officer", 4)
    arcs.start(c, "apophis")
    arcs.resolve(c, "apophis")
    arcs.start(c, "thor")
    arcs.resolve(c, "thor")
    arcs.start(c, "tokra")
    arcs.fail(c, "tokra")
    assert scoring.score(c) == 300


def test_victory_needs_a_major_arc_resolved_and_nothing_still_active():
    c = new_campaign("campaign", "officer", 4)
    assert not scoring.victory(c)
    arcs.start(c, "thor")
    arcs.resolve(c, "thor")
    assert not scoring.victory(c)                             # a minor arc alone
    arcs.start(c, "apophis")
    arcs.start(c, "tokra")
    arcs.resolve(c, "apophis")
    assert not scoring.victory(c)                             # the Tok'ra are still in play
    arcs.fail(c, "tokra")
    assert scoring.victory(c)                                 # a failed minor arc doesn't block it
    s = new_campaign("sandbox", "officer", 4)
    assert not scoring.victory(s)


def test_the_record_entry():
    c = new_campaign("campaign", "commander", 4)
    c.minutes = clock.START + 9 * clock.DAY
    c.record["surveyed"] = 3
    assert scoring.record(c) == {"mode": "campaign", "difficulty": "commander", "result": "overrun", "days": 10,
                                 "surveyed": 3, "score": 30}
    c.ending = "retired"
    assert scoring.record(c)["result"] == "retired"
    c.won = 100
    assert scoring.record(c)["result"] == "victory"
