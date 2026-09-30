import json

from sgc.game import clock
from sgc.game import rules
from sgc.game import schedule
from sgc.game import trade
from sgc.game.schedule import QueueItem
from sgc.game.state import Mission, Team, available_teams, from_dict, to_dict
from tests.test_game_engine import UNKNOWN, Rig
from tests.test_game_missions import CHECKIN, missed, probed


def test_a_queue_item_is_a_row_of_when_what_and_status():
    item = QueueItem("dial:uav:P3X-774", "dial_out", "1", "UAV → P3X-774", "WAITING FOR THE GATE", (0, 0),
                     cancellable=True, movable=True)
    assert item.cells == ("1", "UAV → P3X-774", "WAITING FOR THE GATE") and item.reason == ""
    assert not QueueItem("team:SG-3", "team", "D4 18:00", "SG-3 INJURED", "BACK D4 18:00", (1, 5400)).cancellable



def busy(r, minutes=30):
    r.c.gate_until = r.c.now + minutes


def view(r):
    return r.e.schedule_view()


def sent_to(r):
    """Worlds in the order their drones went through the gate."""
    return [line.split(" SENT TO ")[1] for line in r.logs if " SENT TO " in line]


# ---------------------------------------------------------------- what's listed

def test_dial_outs_waiting_for_the_gate_come_first_in_gate_order():
    r = Rig()
    busy(r)
    a, b = r.world(3, env="normal"), r.world(4, env="normal")
    r.e.probe(a.id)
    r.e.send_uav(b.id)
    rows = [i.cells for i in view(r)]
    assert rows == [("1", f"MALP → {a.name.upper()}", "WAITING FOR THE GATE"),
                    ("2", f"UAV → {b.name.upper()}", "WAITING FOR THE GATE")]
    assert all(i.cancellable and i.movable and i.kind == "dial_out" for i in view(r))
    r.e.advance(1)                                        # still busy: they wait for the gate, in the same order
    assert [i.cells for i in view(r)] == rows


def test_the_view_is_the_order_the_gate_will_take_them():
    r = Rig()
    busy(r)
    a, b = r.world(3, env="normal"), r.world(4, env="normal")
    r.e.probe(a.id)
    r.e.advance(1)                                        # a now waits for the minute the gate frees up
    r.e.probe(b.id)                                       # b is due now, but will queue behind a
    assert [i.what for i in view(r)] == [f"MALP → {a.name.upper()}", f"MALP → {b.name.upper()}"]
    r.e.advance(60)
    assert sent_to(r) == [a.name.upper(), b.name.upper()]


def test_a_mission_in_the_field_shows_its_next_check_in_and_when_it_is_due_home():
    r = Rig(CHECKIN)
    w = probed(r)
    r.e.assign(w.id, "SG-2", "survey")
    first = view(r)[0]
    assert first.what == f"SG-2 → {w.name.upper()} (SURVEY)" and first.status == "WAITING FOR THE GATE"
    assert not any(i.kind == "mission" for i in view(r))           # not in the field until it has gone through
    r.e.advance(1)
    m = r.c.mission(1)
    item = next(i for i in view(r) if i.kind == "mission")
    checkin = r.c.events.find(lambda e: e.kind == "checkin")[0].due
    assert item.what == f"SG-2 SURVEY OF {w.name.upper()}"
    assert item.status == f"CHECK-IN {schedule.at(checkin, r.c.now)} · DUE HOME {clock.short(m.end)}"
    assert item.reason == "ALREADY THROUGH THE GATE"


def test_a_missing_team_shows_no_contact_and_what_is_being_done(monkeypatch):
    r = Rig(CHECKIN)
    w, m = missed(monkeypatch, r)
    assert next(i for i in view(r) if i.kind == "mission").status == "NO CONTACT"
    r.e.key("1")                                                     # send a MALP to search
    assert next(i for i in view(r) if i.kind == "mission").status == "NO CONTACT · SEARCH UNDER WAY"


def test_teams_standing_down_show_when_they_are_back_and_rows_go_by_time():
    r = Rig(CHECKIN)
    until = r.c.now + 2 * clock.DAY
    r.c.teams["SG-3"].status, r.c.teams["SG-3"].until = "injured", until
    r.c.teams["SG-4"].status, r.c.teams["SG-4"].until = "lost", r.c.now + clock.DAY
    w = probed(r)
    r.e.assign(w.id, "SG-2", "survey")
    r.e.advance(1)
    kinds = [(i.kind, i.what) for i in view(r)]
    assert kinds == [("mission", f"SG-2 SURVEY OF {w.name.upper()}"), ("team", "SG-4 RE-FORMING"),
                     ("team", "SG-3 INJURED")]
    sg3 = view(r)[-1]
    assert sg3.status == f"BACK {clock.short(until)}" and sg3.reason == "NOTHING TO CANCEL: SG-3 IS INJURED"


def test_incoming_wormholes_and_other_hidden_events_are_never_listed():
    r = Rig()
    r.c.events.push(r.c.now + 5, "incoming")
    assert view(r) == []                                   # the recovery tick and the incoming stay hidden
    assert not any("INCOMING" in " ".join(i.cells) for i in view(r))


# ---------------------------------------------------------------- cancel

def test_cancelling_a_probe_asks_first_then_puts_the_malp_back_in_stores():
    r = Rig()
    busy(r)
    w = r.world(5, env="normal")
    r.e.probe(w.id)
    item = view(r)[0]
    name = w.name.upper()
    assert r.e.cancel(item.id) == f"CANCEL THE MALP TO {name}?  x AGAIN TO CONFIRM"
    assert r.c.stock["malp"] == 3 and view(r)[0].id == item.id            # nothing changed yet
    saves = len(r.saves)
    assert r.e.cancel(item.id, confirm=True) == f"CANCELLED: MALP TO {name}"
    assert r.c.stock["malp"] == 4 and view(r) == [] and f"CANCELLED: MALP TO {name}" in r.logs
    assert len(r.saves) > saves
    r.e.advance(60)
    assert sent_to(r) == []


def test_cancelling_a_departure_stands_the_team_by_and_counts_no_mission():
    r = Rig()
    w = probed(r)
    r.e.assign(w.id, "SG-2", "survey")
    item = view(r)[0]
    assert r.e.cancel(item.id, confirm=True) == f"CANCELLED: SG-2 DEPARTURE FOR {w.name.upper()}"
    m, tm = r.c.mission(1), r.c.teams["SG-2"]
    assert m.state == "cancelled" and tm.status == "base" and tm.where == "" and tm.mission is None
    assert "SG-2" in available_teams(r.c) and r.c.record["missions"] == 0
    back = from_dict(json.loads(json.dumps(to_dict(r.c))))
    assert back.mission(1).state == "cancelled"
    r.e.advance(2 * clock.DAY)
    assert m.state == "cancelled" and tm.status == "base"


def test_cancelling_a_recall_leaves_the_drone_where_it_is():
    r = Rig()
    w = r.world(5, env="normal", drone="uav")
    r.e.recall_drone(w.id)
    item = view(r)[0]
    assert item.what == f"RECALL UAV FROM {w.name.upper()}"
    r.e.cancel(item.id, confirm=True)
    r.e.advance(60)
    assert w.drone == "uav" and r.c.stock["uav"] == 2


def test_cancelling_a_search_waits_twelve_hours_instead(monkeypatch):
    r = Rig(CHECKIN)
    w, m = missed(monkeypatch, r)
    r.e.key("1")                                          # a MALP is queued to search
    assert r.c.stock["malp"] == 3
    item = view(r)[0]
    assert item.what == f"MALP → {w.name.upper()} FOR SG-3"
    assert r.e.cancel(item.id, confirm=True) == "CANCELLED: MALP SEARCH FOR SG-3"
    assert r.c.stock["malp"] == 4 and m.state == "active" and "WAITING 12 HOURS FOR SG-3" in r.logs
    assert r.c.events.find(lambda e: e.kind == "overdue")[0].due == r.c.now + 12 * clock.HOUR


def test_cancelling_a_search_team_brings_it_back_to_base(monkeypatch):
    r = Rig(CHECKIN)
    w, m = missed(monkeypatch, r)
    r.e.key("2")                                          # the nearest available team goes
    helper = view(r)[0]
    assert helper.what == f"SG-1 → {w.name.upper()} FOR SG-3"
    r.e.cancel(helper.id, confirm=True)
    assert r.c.teams["SG-1"].status == "base" and r.c.teams["SG-1"].where == ""


def test_a_recall_withdraws_a_queued_malp_search_and_puts_the_malp_back(monkeypatch):
    r = Rig(CHECKIN)
    w, m = missed(monkeypatch, r)
    r.e.key("1")
    assert r.c.stock["malp"] == 3
    assert rules.recall(r.c, m) == []
    assert r.c.stock["malp"] == 4 and m.state == "aborted"
    assert not r.c.events.find(lambda e: e.kind in ("dial_out", "overdue"))     # no 12-hour wait for a recalled team


def test_a_recall_brings_a_queued_search_team_back_to_base(monkeypatch):
    r = Rig(CHECKIN)
    w, m = missed(monkeypatch, r)
    r.e.key("2")
    assert r.c.teams["SG-1"].status == "offworld"
    rules.recall(r.c, m)
    r.e.advance(clock.DAY)
    assert r.c.teams["SG-1"].status == "base" and r.c.teams["SG-1"].where == ""
    assert "SG-1" in available_teams(r.c) and not any(i.kind == "dial_out" for i in view(r))


def test_rows_that_cant_be_cancelled_say_why_even_when_confirmed():
    r = Rig(CHECKIN)
    w = probed(r)
    r.e.assign(w.id, "SG-2", "survey")
    r.e.advance(1)
    r.c.teams["SG-3"].status, r.c.teams["SG-3"].until = "injured", r.c.now + clock.DAY
    for item in view(r):
        assert r.e.cancel(item.id) == item.reason == r.e.cancel(item.id, confirm=True) != ""
    assert r.e.cancel("dial:malp:NOWHERE", confirm=True) == "NO LONGER SCHEDULED"


# ---------------------------------------------------------------- move

def three_probes(r):
    busy(r)
    worlds = [r.world(i, env="normal", inhabitants="none") for i in (3, 4, 5)]
    r.c.stock["malp"] = 6
    for w in worlds:
        r.e.probe(w.id)
    return [w.name.upper() for w in worlds]


def test_moving_a_dial_out_reorders_the_gate_queue_and_it_fires_in_that_order():
    r = Rig()
    a, b, c = three_probes(r)
    last = view(r)[2].id
    assert r.e.move(last, -1) == f"MALP TO {c}: NOW 2 IN THE GATE QUEUE"
    assert r.e.move(last, -1) == f"MALP TO {c}: NOW 1 IN THE GATE QUEUE"
    assert [i.what for i in view(r)] == [f"MALP → {c}", f"MALP → {a}", f"MALP → {b}"]
    assert [i.when for i in view(r)] == ["1", "2", "3"]
    assert r.e.move(last, -1) == "ALREADY FIRST IN THE GATE QUEUE"
    assert r.e.move(view(r)[2].id, 1) == "ALREADY LAST IN THE GATE QUEUE"
    r.e.advance(120)
    assert sent_to(r) == [c, a, b]


def test_inbound_traffic_keeps_its_priority_over_a_moved_dial_out():
    r = Rig(UNKNOWN)
    a, b, c = three_probes(r)
    r.e.move(view(r)[2].id, -1)
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(31)
    alarm = next(i for i, line in enumerate(r.logs) if line.startswith("ALARM: INCOMING"))
    first_sent = next((i for i, line in enumerate(r.logs) if " SENT TO " in line), len(r.logs))
    assert alarm < first_sent


def test_a_moved_order_survives_save_and_load():
    r = Rig()
    three_probes(r)
    r.e.move(view(r)[2].id, -1)
    b = Rig(campaign=from_dict(json.loads(json.dumps(to_dict(r.c)))))
    assert [i.id for i in view(b)] == [i.id for i in view(r)]
    for x in (r, b):
        x.e.advance(120)
    assert sent_to(b) == sent_to(r)


def test_only_dial_outs_waiting_for_the_gate_can_be_moved():
    r = Rig()
    r.c.teams["SG-3"].status, r.c.teams["SG-3"].until = "injured", r.c.now + clock.DAY
    assert r.e.move("team:SG-3", -1) == "ONLY DIAL-OUTS WAITING FOR THE GATE CAN BE MOVED"
    assert r.e.move("dial:malp:NOWHERE", 1) == "NO LONGER SCHEDULED"


def test_a_disbanded_team_s_old_missions_never_break_the_queue():
    r = Rig()
    w = r.world(3)
    r.c.teams["SG-7"] = Team("medical")
    r.c.missions.append(Mission(90, "SG-7", w.id, "survey", r.c.now - 600, r.c.now - 60, state="complete"))
    r.c.missions.append(Mission(91, "SG-7", w.id, "survey", r.c.now - 60, r.c.now + 600, state="lost"))
    del r.c.teams["SG-7"]                                  # a lost SG-5+ team is disbanded
    assert not any("SG-7" in i.what for i in view(r))


# ---------------------------------------------------------------- Stage 2 rows

def test_forming_and_training_teams_show_when_they_are_ready():
    r = Rig()
    r.c.teams["SG-5"] = Team("medical", status="forming", until=r.c.now + 2 * clock.DAY)
    r.c.teams["SG-3"].status, r.c.teams["SG-3"].until = "training", r.c.now + clock.DAY
    rows = {i.id: i for i in view(r)}
    assert rows["team:SG-5"].what == "SG-5 FORMING" and rows["team:SG-5"].status.startswith("READY D3")
    assert rows["team:SG-3"].what == "SG-3 TRAINING" and rows["team:SG-3"].status.startswith("BACK D2")
    assert not rows["team:SG-5"].cancellable
    assert r.e.cancel("team:SG-5", confirm=True) == "NOTHING TO CANCEL: SG-5 IS FORMING"


def test_a_trade_delivery_is_listed_but_a_faction_action_never_is():
    r = Rig()
    w = r.world(5, status="contact", env="normal", inhabitants="human", owner=None)
    trade.new_deal(r.c, w.id, "naquadah", 2)
    r.c.events.push(r.c.now + 60, "faction_action", {"faction": "sokar"})
    rows = view(r)
    [row] = [i for i in rows if i.kind == "delivery"]
    assert row.what == f"DELIVERY FROM {w.name.upper()}" and row.status == "2 NAQUADAH · 6 TO COME"
    assert row.when == schedule.at(r.c.now + trade.EVERY, r.c.now)
    assert not row.cancellable and not row.movable
    assert all("SOKAR" not in i.what and i.kind != "faction_action" for i in rows)
    assert r.e.cancel(row.id, confirm=True).startswith("NOTHING TO CANCEL")


def test_a_delivery_shows_no_disruption_odds_and_leaves_once_the_deal_ends():
    r = Rig()
    w = r.world(5, status="contact", env="normal", inhabitants="human", owner=None)
    trade.new_deal(r.c, w.id, "naquadah", 2)
    row = next(i for i in view(r) if i.kind == "delivery")
    assert "%" not in row.status and "RISK" not in row.status
    r.c.deals[0].state = "cut"
    assert not any(i.kind == "delivery" for i in view(r))


def test_cancelling_a_queued_recall_refunds_its_wear():
    r = Rig()
    w = r.world(5, env="normal", drone="uav")
    r.c.funding = 500
    r.e.recall_drone(w.id)
    assert r.c.funding == 485
    r.e.cancel(view(r)[0].id, confirm=True)
    assert r.c.funding == 500 and "RECALL WEAR REFUNDED (15)" in r.logs
    r.e.advance(60)
    assert r.c.funding == 500                             # refunded once, never again later


# ---------------------------------------------------------------- the GATE QUEUE box's rows

def test_each_row_has_a_brief_line_for_the_gate_queue_box():
    r = Rig(CHECKIN)
    busy(r)
    w, v = probed(r), r.world(4, env="normal")
    r.e.assign(w.id, "SG-2", "survey")
    r.e.probe(v.id)
    assert [i.brief for i in view(r)] == [f"1 SG-2 STAGING {w.name.upper()}", f"2 MALP → {v.name.upper()}"]
    r.c.teams["SG-3"].status, r.c.teams["SG-3"].until = "injured", r.c.now + 3 * clock.DAY + 10 * clock.HOUR
    r.e.advance(60)
    rows = {i.id: i.brief for i in view(r)}
    checkin = r.c.events.find(lambda e: e.kind == "checkin")[0].due
    assert rows["mission:1"] == f"SG-2 CHECK-IN {schedule.at(checkin, r.c.now)}"
    assert rows["team:SG-3"] == "SG-3 INJURED D4 18:00"


def test_a_mission_with_no_check_in_left_is_due_home_and_a_delivery_is_listed_by_world():
    r = Rig()
    w = probed(r)
    r.e.assign(w.id, "SG-2", "survey")
    r.e.advance(1)
    r.c.events.cancel(lambda e: e.kind == "checkin")
    m = r.c.mission(1)
    assert next(i for i in view(r) if i.kind == "mission").brief == f"SG-2 HOME {schedule.at(m.end, r.c.now)}"
    v = r.world(4, status="contact", env="normal", inhabitants="human", owner=None)
    trade.new_deal(r.c, v.id, "naquadah", 2)
    row = next(i for i in view(r) if i.kind == "delivery")
    assert row.brief == f"DELIVERY {v.name.upper()} {schedule.at(r.c.deals[0].next, r.c.now)}"
