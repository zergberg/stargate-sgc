from sgc.game import clock, roster
from sgc.game.state import Team, available_teams, new_campaign


def camp():
    return new_campaign("campaign", "officer", 8)


def test_strength_is_full_for_the_primary_and_half_for_a_secondary_or_elite():
    assert roster.strength(Team("recon"), "recon") == 1.0
    assert roster.strength(Team("combat", secondary="recon"), "recon") == 0.5
    assert roster.strength(Team("elite"), "medical") == 0.5
    assert roster.strength(Team("science"), "combat") == 0.0


def test_commissioning_forms_the_next_number_for_two_days():
    c = camp()
    assert roster.commission(c, "diplomatic") == "SG-5 COMMISSIONED: DIPLOMATIC, FORMING FOR 2 DAYS"
    t = c.teams["SG-5"]
    assert (t.specialty, t.status, t.until, t.xp) == ("diplomatic", "forming", c.now + 2 * clock.DAY, 0)
    assert c.funding == 300 and "SG-5" not in available_teams(c)
    del c.teams["SG-2"]
    assert roster.next_number(c) == "SG-2"                  # a gap is filled first


def test_commissioning_needs_funding_and_room_on_the_roster():
    c = camp()
    c.funding = 199
    assert roster.commission(c, "medical") == "NOT ENOUGH FUNDING (200)" and "SG-5" not in c.teams
    c.funding = 10_000
    for _ in range(8):
        roster.commission(c, "combat")
    assert len(c.teams) == 12 and roster.commission_reason(c) == "THE ROSTER IS FULL (SG-12)"


def test_training_adds_a_secondary_for_a_day():
    c = camp()
    assert roster.train(c, "SG-3", "medical") == "SG-3 TRAINING: MEDICAL, BACK ON DUTY IN 24 HOURS"
    t = c.teams["SG-3"]
    assert (t.secondary, t.status, t.until, c.funding) == ("medical", "training", c.now + clock.DAY, 400)


def test_training_refusals_say_why():
    c = camp()
    assert roster.train_reason(c, "SG-1", "medical") == "SG-1 ALREADY TRAINS IN EVERY SPECIALTY"
    assert roster.train_reason(c, "SG-2", "recon") == "SG-2 IS ALREADY RECON"
    c.teams["SG-4"].status = "offworld"
    assert roster.train_reason(c, "SG-4", "combat") == "SG-4 MUST BE AT BASE AND ON DUTY"
    c.teams["SG-3"].secondary = "recon"
    assert roster.train_reason(c, "SG-3", "medical") == "SG-3 ALREADY HAS A SECOND SPECIALTY"
    c.funding = 99
    assert roster.train(c, "SG-2", "medical") == "NOT ENOUGH FUNDING (100)" and c.teams["SG-2"].secondary is None
