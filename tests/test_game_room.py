from sgc.game import content
from sgc.game.engine import Engine
from sgc.game.room import Room
from sgc.game.state import new_campaign


def room():
    scenarios, _ = content.load(user=None)
    c = new_campaign("campaign", "officer", 2)
    c.stock["uav"] = 2
    c.upgrades.add("uav_program")
    return Room(Engine(c, scenarios)), c


def test_main_menu_and_leaving():
    r, _ = room()
    assert r.title == "BRIEFING ROOM" and [label for label, _ in r.items()][0] == "DIALING LIST"
    assert r.key("7") == ("close",)
    assert r.key("q") == ("close",)


def test_probe_uav_and_recall_from_the_dialing_list():
    r, c = room()
    r.key("1")
    assert r.screen == "worlds" and len(r.items()) == 21 and r.items()[0][0].startswith("Abydos")
    target = list(c.worlds)[3]
    for k in ("down", "down", "down", "enter"):
        r.key(k)
    assert r.screen == "world" and r.world_id == target and r.title == target
    assert r.items()[0] == ("MALP PROBE (4 LEFT)", True) and r.items()[2] == ("RECALL DRONE", False)
    assert r.items()[3] == ("ASSIGN TEAM", False)
    r.key("1")
    assert r.notice == f"MALP QUEUED FOR {target}" and c.stock["malp"] == 3
    r.key("2")
    assert r.notice.startswith("A DRONE IS ALREADY BOUND")
    c.worlds[target].drone = "malp"
    r.key("3")
    assert r.notice.startswith("RECALL QUEUED")
    assert r.detail()[0].startswith(f"{target} · UNEXPLORED")


def test_assigning_a_team_to_a_probed_world():
    r, c = room()
    w = list(c.worlds.values())[2]
    w.status = "probed"
    r.key("1")
    r.key("3")
    r.key("4")
    assert r.screen == "team_pick" and [label for label, _ in r.items()][0].startswith("SG-1 · ELITE · GREEN")
    r.key("3")
    assert r.screen == "type_pick" and r.items() == [("SURVEY", True), ("BACK", True)]
    r.key("1")
    assert r.notice.startswith("SG-3 ASSIGNED: SURVEY") and r.screen == "world"
    assert c.teams["SG-3"].status == "staging"


def test_adding_a_note_types_text():
    r, c = room()
    r.key("1")
    r.key("1")
    r.key("5")
    assert r.text_mode
    for ch in "Kasuf says hi":
        r.key(f"ch:{ch}")
    r.key("backspace")
    r.key("enter")
    w = next(iter(c.worlds.values()))
    assert w.notes[-1][1] == "Kasuf says h" and r.notice == "NOTE ADDED" and not r.text_mode


def test_standing_orders_cycle_and_pace_changes():
    r, c = room()
    r.key("3")
    assert r.items()[0] == ("Unknown or no IDC\nKeep the iris closed", True)
    r.key("1")
    assert c.orders["unknown_idc"] == "open_guarded" and r.items()[0][0].endswith("\nOpen for 30 seconds under guard")
    r.key("q")
    r.key("4")
    assert r.items()[1] == ("• STANDARD", True)
    r.key("3")
    assert c.pace == "busy" and r.items()[2] == ("• BUSY", True)


def test_revoking_an_idc_from_the_roster():
    r, c = room()
    c.teams["SG-2"].idc = "compromised"
    r.key("2")
    assert r.items()[1][0] == "SG-2 · RECON · GREEN\nBASE"
    r.key("2")
    assert r.title == "SG-2"
    r.key("1")
    r.key("1")
    assert c.teams["SG-2"].idc == "valid" and c.teams["SG-2"].until > c.now
    assert r.notice.startswith("SG-2 HAS A NEW IDC")
    r.key("q")
    assert r.items()[1][0] == "SG-2 · RECON · GREEN\nSTOOD DOWN 12H"
    for k in ("q",):
        r.key(k)
    assert r.screen == "main"


def test_cancelling_a_note_with_ctrl_c_discards_it():
    r, c = room()
    r.key("1")
    r.key("1")
    r.key("5")
    assert r.text_mode
    for ch in "discard me":
        r.key(f"ch:{ch}")
    r.key("ctrl-c")
    w = next(iter(c.worlds.values()))
    assert r.screen == "world" and not r.text_mode and not w.notes and r.notice == ""


def test_cancelling_a_note_with_escape_discards_it():
    r, c = room()
    r.key("1")
    r.key("1")
    r.key("5")
    for ch in "nope":
        r.key(f"ch:{ch}")
    r.key("escape")
    w = next(iter(c.worlds.values()))
    assert r.screen == "world" and not r.text_mode and not w.notes and r.notice == ""


def test_disabled_world_actions_explain_why():
    r, c = room()
    r.key("1")
    target = list(c.worlds)[3]
    for k in ("down", "down", "down", "enter"):
        r.key(k)
    assert r.world_id == target
    w = c.worlds[target]
    c.stock["malp"] = 0
    r.key("1")
    assert r.notice == "NO MALPS LEFT"
    c.stock["uav"] = 0
    r.key("2")
    assert r.notice == "NO UAVS LEFT"
    r.key("3")
    assert r.notice == f"NO DRONE ON {target}"
    r.key("4")
    assert r.notice == "PROBE IT FIRST"
    w.status = "probed"
    for name in ("SG-1", "SG-2", "SG-3", "SG-4"):
        c.teams[name].status = "offworld"
    r.key("4")
    assert r.notice == "NO TEAM AVAILABLE"


def test_the_roster_shows_each_teams_real_status_and_time_left():
    from sgc.game.clock import DAY, HOUR
    r, c = room()
    w = list(c.worlds.values())[3]
    c.teams["SG-1"].status, c.teams["SG-1"].where = "offworld", w.id
    c.teams["SG-2"].until = c.now + 11 * HOUR
    c.teams["SG-3"].status, c.teams["SG-3"].until = "injured", c.now + 2 * DAY - 4 * HOUR
    c.teams["SG-4"].status, c.teams["SG-4"].until = "captured", c.now + 4 * DAY
    r.key("2")
    assert [label for label, _ in r.items()[:4]] == [
        f"SG-1 · ELITE · GREEN\nAWAY: {w.name}", "SG-2 · RECON · GREEN\nSTOOD DOWN 11H",
        "SG-3 · COMBAT · GREEN\nINJURED 1D 20H", "SG-4 · SCIENCE · GREEN\nCAPTURED 4D"]
    c.teams["SG-4"].status, c.teams["SG-4"].until = "lost", c.now + 2 * DAY + 5 * HOUR
    assert r.items()[3][0].endswith("\nRE-FORMING 2D 5H")
    c.teams["SG-2"].until = c.now + 30               # half an hour still shows as an hour
    assert r.items()[1][0].endswith("\nSTOOD DOWN 1H")
    c.teams["SG-2"].until = c.now
    assert r.items()[1][0].endswith("\nBASE")


def test_assign_a_team_lists_unavailable_teams_with_the_reason():
    from sgc.game.clock import HOUR
    r, c = room()
    w = list(c.worlds.values())[2]
    w.status = "probed"
    c.teams["SG-2"].until = c.now + 11 * HOUR
    r.key("1")
    r.key("3")
    r.key("4")
    items = r.items()
    assert items[0] == ("SG-1 · ELITE · GREEN", True)
    assert items[1] == ("SG-2 · RECON · GREEN\nSTOOD DOWN 11H", False)
    r.key("2")
    assert r.screen == "team_pick" and r.notice == "SG-2: STOOD DOWN 11H"
    r.key("3")
    assert r.screen == "type_pick" and r.team == "SG-3"


def test_revoking_an_idc_asks_first_and_says_the_team_stands_down():
    r, c = room()
    r.key("2")
    r.key("2")
    r.key("1")
    assert r.screen == "revoke" and c.teams["SG-2"].until == 0
    assert any("STANDS DOWN FOR 12 HOURS" in line for line in r.detail())
    r.key("q")
    assert r.screen == "team" and c.teams["SG-2"].until == 0
    r.key("1")
    r.key("1")
    assert r.screen == "team" and c.teams["SG-2"].until == c.now + 12 * 60
    assert r.notice == "SG-2 HAS A NEW IDC · STOOD DOWN 12H"


def test_revoking_a_captured_teams_idc_says_it_stands_no_one_down():
    r, c = room()
    c.teams["SG-2"].status, c.teams["SG-2"].until = "captured", c.now + 3 * 60
    r.key("2")
    r.key("2")
    r.key("1")
    assert r.items()[0] == ("REVOKE AND REISSUE", True)
    assert not any("STANDS DOWN" in line for line in r.detail())
    r.key("1")
    assert c.teams["SG-2"].until == c.now + 3 * 60 and r.notice == "SG-2 HAS A NEW IDC · CAPTURED 3H"


def test_revoking_an_injured_teams_idc_with_a_longer_timer_keeps_it_out_as_before():
    r, c = room()
    c.teams["SG-2"].status, c.teams["SG-2"].until = "injured", c.now + 30 * 60
    r.key("2")
    r.key("2")
    r.key("1")
    assert r.items()[0] == ("REVOKE AND REISSUE", True)
    assert any("STAYS OUT AS BEFORE" in line for line in r.detail())
    r.key("1")
    assert c.teams["SG-2"].until == c.now + 30 * 60


def test_standing_orders_show_the_situation_and_its_order():
    from sgc.game.orders import SITUATIONS, label
    r, c = room()
    r.key("3")
    for (text, _), (sid, s) in zip(r.items(), SITUATIONS.items()):
        head, order = text.split("\n")
        assert order == label(sid, c.orders[sid]) and len(head) <= 30


def test_pace_set_in_the_config_is_locked():
    scenarios, _ = content.load(user=None)
    c = new_campaign("campaign", "officer", 2)
    r = Room(Engine(c, scenarios, pace_override=30))
    assert r.items()[3] == ("PACE · LOCKED", False)
    r.key("4")
    assert r.screen == "main" and r.notice == "SET IN CONFIG (game_pace)" and c.pace == "standard"


def test_requisitions_buy_drones_and_upgrades():
    r, c = room()
    r.key("5")
    assert r.screen == "requisitions" and r.title == "REQUISITIONS"
    labels = [label for label, _ in r.items()]
    assert labels[:5] == ["BUY A MALP · 20 (4 IN STORES)", "BUY A UAV · 60 (2 IN STORES)", "MALP RESERVE: 2",
                          "UAV RESERVE: 0", "UAV PROGRAM · APPROVED"]
    assert "IRIS REINFORCEMENT · 100 + 5 NQ" in labels and labels[-1] == "BACK"
    r.key("1")
    assert r.notice.startswith("MALP PURCHASED") and c.funding == 480
    r.key("3")
    assert c.reserve["malp"] == 3 and r.items()[2][0] == "MALP RESERVE: 3"
    r.key("6")
    assert r.notice == "SECURITY DETAIL APPROVED" and "security_detail" in c.upgrades and c.funding == 360
    r.key("7")
    assert r.notice == "NOT ENOUGH NAQUADAH (5)" and "iris_reinforcement" not in c.upgrades
    detail = r.detail()
    assert detail[0] == "FUNDING 360 · NAQUADAH 0" and detail[1] == "NEXT REVIEW D8 08:00"
    assert "Cuts damage from impacts and breaches by a quarter." in detail
    r.key("5")
    assert r.notice == "ALREADY APPROVED"


def test_the_requisitions_detail_follows_the_selection_and_shows_the_last_review():
    r, c = room()
    c.reviews.append((7 * 24 * 60, 320, "summary"))
    r.key("5")
    assert r.detail()[2] == "LAST REVIEW D8 00:00: +320" and r.detail()[-1] == "Bought now, from funding."
    r.sel = 2
    assert r.detail()[-1].startswith("Topped up at midnight")
    r.sel = len(r.items()) - 1                      # BACK: nothing to explain
    assert r.detail()[-1] == "LAST REVIEW D8 00:00: +320"


def test_the_reserve_wraps_around():
    r, c = room()
    r.key("5")
    for _ in range(3):
        r.key("4")
    assert c.reserve["uav"] == 3
    r.key("4")
    r.key("4")
    assert c.reserve["uav"] == 0


def test_without_the_program_the_uav_says_so():
    r, c = room()
    c.upgrades.discard("uav_program")
    r.key("1")
    for k in ("down", "down", "down", "enter"):
        r.key(k)
    assert r.items()[1][1] is False
    r.key("2")
    assert r.notice == "NEEDS THE UAV PROGRAM"
    r.key("q")
    r.key("q")
    r.key("5")
    r.key("2")
    assert r.notice == "NEEDS THE UAV PROGRAM" and c.stock["uav"] == 2


def test_the_recall_item_shows_its_wear():
    r, c = room()
    target = list(c.worlds)[3]
    c.worlds[target].drone = "uav"
    r.key("1")
    for k in ("down", "down", "down", "enter"):
        r.key(k)
    assert r.items()[2] == ("RECALL DRONE · 15", True)


def test_commissioning_a_team_from_the_roster():
    r, c = room()
    r.key("2")
    assert r.items()[4] == ("COMMISSION A NEW TEAM · 200", True)
    r.key("5")
    assert r.screen == "commission" and r.detail()[1] == "SG-5 FORMS IN 2 DAYS, GREEN."
    r.key("5")
    assert r.screen == "teams" and r.notice.startswith("SG-5 COMMISSIONED: MEDICAL")
    assert r.items()[4][0] == "SG-5 · MEDICAL · GREEN\nFORMING 2D" and r.items()[5][0] == "COMMISSION A NEW TEAM · 200"


def test_commissioning_without_funding_says_why():
    r, c = room()
    c.funding = 150
    r.key("2")
    assert r.items()[4] == ("COMMISSION A NEW TEAM · 200", False)
    r.key("5")
    assert r.screen == "teams" and r.notice == "NOT ENOUGH FUNDING (200)"


def test_training_a_second_specialty():
    r, c = room()
    r.key("2")
    r.key("3")
    assert r.screen == "team" and r.items()[1] == ("TRAIN A SECOND SPECIALTY · 100", True)
    r.key("2")
    assert r.screen == "train" and r.items()[1] == ("COMBAT", False)
    r.key("2")
    assert r.notice == "SG-3 IS ALREADY COMBAT"
    r.key("5")
    assert r.screen == "team" and r.notice.startswith("SG-3 TRAINING: MEDICAL") and c.teams["SG-3"].secondary == "medical"
    assert r.detail()[0] == "SG-3 · COMBAT/MEDICAL · GREEN" and r.items()[1][1] is False
    r.key("2")
    assert r.notice == "SG-3 ALREADY HAS A SECOND SPECIALTY"


def test_sg1_cannot_train_and_says_why():
    r, c = room()
    r.key("2")
    r.key("1")
    assert r.items()[1][1] is False
    r.key("2")
    assert r.screen == "team" and r.notice == "SG-1 ALREADY TRAINS IN EVERY SPECIALTY"


def test_rescue_and_recovery_name_their_target():
    r, c = room()
    w = list(c.worlds.values())[2]
    w.status = "hostile"
    c.teams["SG-4"].status, c.teams["SG-4"].where = "captured", w.id
    w.options.append("rescue")
    for k in ("1", "3", "4", "3"):
        r.key(k)
    assert r.screen == "type_pick" and ("RESCUE SG-4", True) in r.items()


def test_retiring_asks_first_and_ends_the_campaign():
    r, c = room()
    r.key("6")
    assert r.screen == "retire" and r.detail()[0] == "SCORE SO FAR: 0"
    r.key("q")
    assert r.screen == "main" and not r.e.ended
    r.key("6")
    r.key("1")
    assert r.e.ended and c.ending == "retired" and r.notice == "COMMAND HANDED OVER"
