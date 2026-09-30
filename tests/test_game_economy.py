from sgc.game import clock, economy
from sgc.game.state import STOCK, UPGRADE_IDS, new_campaign


def camp(diff="officer"):
    c = new_campaign("campaign", diff, 6)
    c.stock = {"malp": 4, "uav": 0}
    return c


def test_drones_cost_funding_and_uavs_need_the_program():
    c = camp()
    assert economy.buy(c, "malp") == "MALP PURCHASED — 5 IN STORES, FUNDING 480"
    assert economy.reason(c, "uav") == "NEEDS THE UAV PROGRAM" and economy.buy(c, "uav") == "NEEDS THE UAV PROGRAM"
    assert economy.buy(c, "uav_program") == "UAV PROGRAM APPROVED" and c.funding == 330 and c.stock["uav"] == 1
    assert economy.buy(c, "uav").startswith("UAV PURCHASED — 2 IN STORES") and c.funding == 270
    assert economy.reason(c, "uav_program") == "ALREADY APPROVED"


def test_purchases_never_overdraw_and_stores_have_a_cap():
    c = camp()
    c.funding = 19
    assert economy.buy(c, "malp") == "NOT ENOUGH FUNDING (20)" and c.funding == 19 and c.stock["malp"] == 4
    c.funding, c.stock["malp"] = 1000, STOCK["malp"][1]
    assert economy.reason(c, "malp") == f"STORES FULL ({STOCK['malp'][1]})"


def test_upgrades_that_cost_naquadah():
    c = camp()
    assert economy.reason(c, "iris_reinforcement") == "NOT ENOUGH NAQUADAH (5)"
    c.naquadah = 12
    economy.buy(c, "iris_reinforcement")
    assert c.funding == 400 and c.naquadah == 7 and economy.defense(c) == 0.75
    assert economy.buy(c, "naquadah_generator") == "NOT ENOUGH NAQUADAH (10)"
    c.naquadah = 10
    assert economy.buy(c, "naquadah_generator") == "NAQUADAH GENERATOR APPROVED"
    assert c.naquadah == 0 and c.funding == 400 and "tech.naquadah_generator" in c.inventory
    assert economy.defense(c) == 0.75 * 0.75
    assert economy.cost_text("iris_reinforcement") == "100 + 5 NQ"
    assert economy.cost_text("naquadah_generator") == "10 NQ" and economy.cost_text("uav_program") == "150"


def test_every_upgrade_is_described():
    assert set(economy.UPGRADES) == set(UPGRADE_IDS)
    assert all(u.text and u.title == u.title.upper() for u in economy.UPGRADES.values())


def test_requisition_tops_up_the_reserve_but_keeps_a_floor():
    c = camp()
    c.stock, c.reserve = {"malp": 0, "uav": 0}, {"malp": 3, "uav": 1}
    assert economy.requisition(c) == ["REQUISITION: 3 MALPS (60)"] and c.stock["malp"] == 3 and c.funding == 440
    c.upgrades.add("uav_program")
    assert economy.requisition(c) == ["REQUISITION: 1 UAV (60)"] and c.funding == 380
    c.stock["malp"], c.funding = 0, 139
    assert economy.requisition(c) == ["REQUISITION: 1 MALP (20)"] and c.funding == 119
    assert economy.requisition(c) == []                    # another would leave less than 100


def test_the_reserve_is_clamped():
    c = camp()
    assert economy.set_reserve(c, "malp", 9) == "MALP RESERVE: 4" and c.reserve["malp"] == 4
    economy.set_reserve(c, "uav", -1)
    assert c.reserve["uav"] == 0


def test_a_review_itemizes_performance_scales_by_difficulty_and_schedules_the_next():
    c = camp("commander")
    c.ledger.update(intel=12, missions=3, lost=1)
    lines = economy.review(c)
    # intel 12 x 10 capped at 100, missions +15, one team lost -40: (300 + 75) x 0.8 = 300
    assert lines == ["FUNDING REVIEW: +300 — FUNDING 800",
                     "BASE 300 · INTEL +100 · MISSIONS +15 · TEAMS LOST -40 · ×0.8"]
    assert c.funding == 800 and set(c.ledger.values()) == {0}
    assert c.reviews == [(c.now, 300, "BASE 300 · INTEL +100 · MISSIONS +15 · TEAMS LOST -40 · ×0.8")]
    assert any(e.kind == "funding_review" and e.due == c.now + 7 * clock.DAY for e in c.events)


def test_a_grant_is_at_least_fifty_and_three_reviews_are_kept():
    c = camp()
    c.ledger["lost"] = 20
    economy.review(c)
    assert c.reviews[-1][1] == 50
    for _ in range(4):
        economy.review(c)
    assert len(c.reviews) == 3


def test_a_recruit_review_is_scaled_up():
    c = camp("recruit")
    assert economy.review(c) == ["FUNDING REVIEW: +375 — FUNDING 875", "BASE 300 · ×1.25"]


def test_a_half_rounds_up():
    c = camp("recruit")
    c.ledger["intel"] = 3                                   # (300 + 30) x 1.25 = 412.5
    economy.review(c)
    assert c.reviews[-1][1] == 413


def test_a_long_review_is_wrapped_for_the_log():
    c = camp("recruit")
    c.ledger.update(intel=12, tech=2, allies=1, missions=3, arcs=1, lost=1, captured=1, breaches=1, incidents=1)
    lines = economy.review(c)
    assert lines[0].startswith("FUNDING REVIEW: +") and len(lines) >= 3
    assert all(len(line) <= 66 for line in lines)
    assert " · ".join(lines[1:]) == c.reviews[-1][2]


def test_a_late_review_keeps_to_the_seven_day_grid():
    c = camp()
    c.minutes = clock.START + 7 * clock.DAY + 5 * clock.HOUR           # a review that fired five hours late
    c.events.cancel(lambda e: e.kind == "funding_review")
    economy.review(c)
    [ev] = [e for e in c.events if e.kind == "funding_review"]
    assert ev.due == clock.START + 14 * clock.DAY


def test_note_counts_for_the_next_review():
    c = camp()
    economy.note(c, "intel")
    economy.note(c, "breaches", 2)
    assert c.ledger["intel"] == 1 and c.ledger["breaches"] == 2
