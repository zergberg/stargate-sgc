from sgc.game import clock, trade
from sgc.game.state import new_campaign


def camp():
    c = new_campaign("sandbox", "officer", 12)
    w = list(c.worlds.values())[4]
    w.env, w.inhabitants, w.owner, w.status = "normal", "human", None, "contact"
    return c, w


def deliveries(c):
    return [e for e in c.events if e.kind == "trade_delivery"]


def test_a_deal_delivers_every_three_days_six_times_then_runs_out():
    c, w = camp()
    assert trade.new_deal(c, w.id, "naquadah", 2) == [f"TRADE DEAL: 2 NAQUADAH FROM {w.name.upper()} EVERY 72 HOURS"]
    d = c.deals[0]
    assert (d.id, d.left, d.next, d.state) == (1, 6, c.now + 72 * 60, "active")
    assert [e.due for e in deliveries(c)] == [d.next]
    for i in range(6):
        c.minutes = d.next
        c.events.cancel(lambda e: e.kind == "trade_delivery")
        lines, arrived = trade.deliver(c, 1, roll=99.0)
        assert arrived and lines[0] == f"DELIVERY FROM {w.name.upper()}: 2 NAQUADAH"
    assert c.naquadah == 12 and d.state == "ended" and deliveries(c) == []
    assert lines[-1] == f"THE DEAL WITH {w.name.upper()} HAS RUN ITS COURSE"


def test_two_disruptions_in_a_row_cut_the_route():
    c, w = camp()
    trade.new_deal(c, w.id, "naquadah", 3)
    lines, arrived = trade.deliver(c, 1, roll=0.0)
    assert not arrived and lines == [f"DELIVERY FROM {w.name.upper()} DID NOT ARRIVE"] and c.naquadah == 0
    assert trade.risk_words(c, c.deals[0]) == "RAISED"
    lines, _ = trade.deliver(c, 1, roll=0.0)
    assert lines[-1] == f"THE TRADE ROUTE TO {w.name.upper()} IS CUT" and c.deals[0].state == "cut"
    assert trade.deliver(c, 1, roll=99.0) == ([], False)


def test_a_new_deal_with_the_same_world_renews_it():
    c, w = camp()
    trade.new_deal(c, w.id, "naquadah", 2)
    c.deals[0].left = 1
    assert trade.new_deal(c, w.id, "naquadah", 3) == [f"TRADE WITH {w.name.upper()} RENEWED"]
    assert len(c.deals) == 1 and c.deals[0].left == 6 and c.deals[0].amount == 3


def test_renewing_a_deal_clears_its_misses():
    c, w = camp()
    trade.new_deal(c, w.id, "naquadah", 2)
    trade.deliver(c, 1, roll=0.0)
    assert c.deals[0].misses == 1
    trade.new_deal(c, w.id, "naquadah", 2)
    assert c.deals[0].misses == 0


def test_a_lost_world_cuts_the_route_and_reads_high():
    c, w = camp()
    trade.new_deal(c, w.id, "naquadah", 2)
    w.status = "lost"
    assert trade.risk_words(c, c.deals[0]) == "HIGH"
    c.events.cancel(lambda e: e.kind == "trade_delivery")          # the engine takes the due event
    lines, arrived = trade.deliver(c, 1, roll=99.0)
    assert not arrived and c.naquadah == 0 and c.deals[0].state == "cut"
    assert lines == [f"{w.name.upper()} IS LOST — THE TRADE ROUTE IS CUT"]
    assert deliveries(c) == []


def test_risk_words_take_the_owner_the_sgc_has_learned():
    c, w = camp()
    trade.new_deal(c, w.id, "naquadah", 2)
    d = c.deals[0]
    w.inhabitants, w.owner = "jaffa", "Apophis"                     # the true, hidden owner is calm
    w.seen["owner"] = "Cronus"                                      # the SGC believes it is Cronus
    c.factions["cronus"].known, c.factions["cronus"].attention = True, 60
    assert trade.risk_words(c, d) == "HIGH"


def test_disruption_grows_with_danger_hostility_and_a_hostile_owner():
    c, w = camp()
    trade.new_deal(c, w.id, "naquadah", 2)
    d = c.deals[0]
    assert trade.disruption(c, d) == 5
    w.inhabitants, w.owner = "jaffa", "Cronus"
    assert trade.disruption(c, d) == 15
    c.factions["cronus"].attention = 60
    assert trade.disruption(c, d) == 30
    w.status = "hostile"
    assert trade.disruption(c, d) == 40


def test_risk_words_use_only_what_the_sgc_knows():
    c, w = camp()
    trade.new_deal(c, w.id, "naquadah", 2)
    d = c.deals[0]
    w.inhabitants, w.owner = "jaffa", "Cronus"
    c.factions["cronus"].attention = 60
    assert trade.risk_words(c, d) == "LOW"                 # the owner is a hidden trait
    w.seen["owner"] = "Cronus"
    assert trade.risk_words(c, d) == "LOW"                 # ...and Cronus himself isn't known yet
    c.factions["cronus"].known = True
    assert trade.risk_words(c, d) == "HIGH"
    d.state = "ended"
    assert trade.risk_words(c, d) == "—"
