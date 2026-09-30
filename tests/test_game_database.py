from sgc.game.database import TABS, Database
from sgc.game.schedule import QueueItem
from sgc.game.state import Mission, new_campaign


def camp():
    c = new_campaign("campaign", "officer", 3)
    ws = list(c.worlds.values())
    ws[1].status, ws[1].drone, ws[1].last_visit = "probed", "malp", 600
    ws[1].seen = {"env": "toxic atmosphere", "life": "none detected"}
    ws[2].status = "surveyed"
    ws[2].names.append(("Tel'kar", "the locals", 700))
    ws[2].names.append(("Ha'shek", "the Jaffa", 800))
    ws[2].reports.append((700, "SG-2 surveyed ruins."))
    ws[2].notes.append((710, "Go back with SG-4."))
    ws[3].found = "intel from SG-2"
    c.missions.append(Mission(1, "SG-2", ws[2].id, "survey", 500, 1900, state="complete", findings=["ruins"]))
    c.missions.append(Mission(2, "SG-3", ws[1].id, "survey", 900, 2300))
    c.teams["SG-3"].status, c.teams["SG-3"].where = "offworld", ws[1].id
    return c, ws


def test_addresses_sort_by_status_then_name_and_show_flags():
    c, ws = camp()
    db = Database(c)
    rows = db.rows()
    assert len(rows) == 20 and rows[0].cells[0] == f"Abydos ({ws[0].id})" and rows[1].cells[0] == f"Ha'shek ({ws[2].id})"
    assert rows[1].cells[2] == "SURVEYED" and rows[1].cells[4] == "N"
    probed = next(r for r in rows if r.key == ws[1].id)
    assert probed.cells[2:] == ("PROBED", "D1 10:00", "T", "MALP")
    assert next(r for r in rows if r.key == ws[3].id).cells[4] == "L"
    db.key("s")
    assert db.sort == "name" and [r.cells[0] for r in db.rows()] == sorted((r.cells[0] for r in db.rows()),
                                                                           key=str.lower)


def test_filter_cycles_through_statuses():
    c, ws = camp()
    db = Database(c)
    db.key("f")
    assert db.filter == "unexplored" and all(r.cells[2] == "UNEXPLORED" for r in db.rows())
    db.key("f")
    assert [r.key for r in db.rows()] == [ws[1].id]
    for _ in range(5):
        db.key("f")
    assert db.filter is None and len(db.rows()) == 20


def test_search_matches_names_designations_and_glyphs():
    c, ws = camp()
    db = Database(c)
    db.key("/")
    assert db.text_mode
    for ch in "tel'":
        db.key(f"ch:{ch}")
    assert [r.key for r in db.rows()] == [ws[2].id]
    db.key("enter")
    assert not db.text_mode and db.query == "tel'"
    db.key("/")
    for ch in ws[4].id.lower():
        db.key(f"ch:{ch}")
    db.key("backspace")
    assert ws[4].id in [r.key for r in db.rows()]
    db.key("enter")
    db.key("/")
    for ch in ws[5].glyph_text[:5]:
        db.key(f"ch:{ch}")
    assert ws[5].id in [r.key for r in db.rows()]


def test_navigation_and_the_world_file():
    c, ws = camp()
    db = Database(c)
    db.key("down")
    assert db.selected().key == ws[2].id
    db.key("enter")
    assert db.tab == "world" and db.world_id == ws[2].id
    text = "\n".join(db.detail())
    for bit in ("HA'SHEK", "SURVEYED", "Tel'kar — the locals, D1 11:40", "Ha'shek — the Jaffa, D1 13:20",
                "SG-2 surveyed ruins.", "Go back with SG-4.", "MISSION OPTIONS: SURVEY", ws[2].glyph_text):
        assert bit in text, bit
    db.world_id = ws[1].id
    text = "\n".join(db.detail())
    assert "ENV: toxic atmosphere" in text and "MALP ON SITE" in text and "none known" in text


def test_tabs_cycle_both_ways_and_q_closes():
    c, _ = camp()
    db = Database(c)
    for tab in TABS[1:] + TABS[:1]:
        db.key("right")
        assert db.tab == tab
    db.key("left")
    assert db.tab == "queue"
    assert db.key("q") == ("close",)
    db.tab = "intel"
    db.key("/")                                    # only on the Addresses and Queue tabs
    assert not db.text_mode


QUEUE = [
    QueueItem("dial:uav:P3X-774", "dial_out", "1", "UAV → P3X-774", "WAITING FOR THE GATE", (0, 0), True, True),
    QueueItem("dial:malp:P2A-018", "dial_out", "2", "MALP → P2A-018", "WAITING FOR THE GATE", (0, 1), True, True),
    QueueItem("mission:3", "mission", "14:00", "SG-2 SURVEY OF ABYDOS", "CHECK-IN 14:00 · DUE HOME D3 09:00",
              (1, 840), reason="ALREADY THROUGH THE GATE"),
]


def queue_db(items=QUEUE):
    c, _ = camp()
    db = Database(c, lambda: list(items))
    db.tab = "queue"
    return db


def test_the_queue_tab_lists_the_schedule_it_is_given():
    db = queue_db()
    assert [r.cells for r in db.rows()] == [i.cells for i in QUEUE] and db.rows()[0].key == "dial:uav:P3X-774"
    assert Database(camp()[0]).tab == "addresses"
    bare = Database(camp()[0])
    bare.tab = "queue"
    assert bare.rows() == []                       # no schedule given: nothing listed


def test_search_matches_the_queue_rows_text():
    db = queue_db()
    db.key("/")
    for ch in "abydos":
        db.key(f"ch:{ch}")
    assert [r.key for r in db.rows()] == ["mission:3"]


def test_x_asks_then_confirms_and_any_other_key_disarms():
    db = queue_db()
    assert db.key("x") == ("cancel", "dial:uav:P3X-774", False)
    assert db.key("x") == ("cancel", "dial:uav:P3X-774", True)
    assert db.key("x") == ("cancel", "dial:uav:P3X-774", False)
    db.key("down")
    assert db.key("x") == ("cancel", "dial:malp:P2A-018", False)
    db.key("up")
    db.key("down")
    assert db.key("x") == ("cancel", "dial:malp:P2A-018", False)
    db.armed = "dial:malp:P2A-018"
    db.key("tab")
    assert db.armed is None


def test_brackets_move_the_selected_row_and_select_follows_a_key():
    db = queue_db()
    db.key("down")
    assert db.key("[") == ("move", "dial:malp:P2A-018", -1)
    assert db.key("]") == ("move", "dial:malp:P2A-018", 1)
    db.select("mission:3")
    assert db.sel == 2
    db.select("gone")
    assert db.sel == 2


def test_the_engines_reply_shows_until_the_next_key_and_queue_keys_do_nothing_elsewhere():
    db = queue_db()
    db.message = "CANCEL THE UAV TO P3X-774?  x AGAIN TO CONFIRM"
    db.key("down")
    assert db.message == ""
    empty = queue_db([])
    empty.key("x")
    assert empty.message == "NOTHING SCHEDULED"
    other = queue_db()
    other.tab = "addresses"
    assert other.key("x") is None and other.key("[") is None and other.armed is None


def test_a_cancelled_mission_is_not_in_a_teams_history():
    c, ws = camp()
    c.missions.append(Mission(3, "SG-2", ws[4].id, "survey", 900, 2300, state="cancelled"))
    db = Database(c)
    db.tab = "teams"
    sg2 = next(r for r in db.rows() if r.key == "SG-2")
    assert sg2.cells[5] == f"1 missions, last {db._world_name(ws[2].id)}"


def test_world_file_scroll_moves_and_clamps_at_zero():
    c, ws = camp()
    db = Database(c)
    db.tab, db.world_id, db.scroll = "world", ws[2].id, 5
    db.key("up")
    assert db.scroll == 4
    for _ in range(10):
        db.key("up")
    assert db.scroll == 0
    db.key("down")
    db.key("down")
    assert db.scroll == 2
    db.key("enter")                                # does nothing on the world tab
    assert db.tab == "world" and db.scroll == 2


def test_opening_a_world_file_resets_the_scroll():
    c, ws = camp()
    db = Database(c)
    db.scroll = 9
    db.key("down")
    db.key("enter")
    assert db.tab == "world" and db.scroll == 0
    db.scroll = 4
    db.key("left")                                 # back to addresses
    assert db.tab == "addresses"
    db.key("right")                                # reopens the world tab
    assert db.tab == "world" and db.scroll == 0


def test_missions_teams_and_intel_tabs():
    c, ws = camp()
    db = Database(c)
    db.tab = "missions"
    assert [r.cells[:3] + r.cells[4:] for r in db.rows()] == [
        ("SG-2", "Ha'shek", "SURVEY", "COMPLETE", "0", "ruins"), ("SG-3", ws[1].id, "SURVEY", "ACTIVE", "0", "—")]
    db.key("enter")
    assert db.tab == "world" and db.world_id == ws[2].id
    db.tab = "teams"
    rows = {r.key: r.cells for r in db.rows()}
    assert rows["SG-1"] == ("SG-1", "ELITE", "GREEN", "BASE", "SGC", "0 missions")
    assert rows["SG-2"][5] == "1 missions, last Ha'shek" and rows["SG-3"][3:5] == ("OFFWORLD", ws[1].id)
    db.tab = "intel"
    kinds = [(r.cells[0], r.cells[1]) for r in db.rows()]
    assert ("NAME", "Abydos") in kinds and ("NAME", "Tel'kar") in kinds and ("NAME", "Ha'shek") in kinds
    assert ("LEAD", "address not yet visited") in kinds


def test_the_teams_tab_shows_time_left_on_a_stand_down_injury_capture_or_re_forming():
    from sgc.game.clock import DAY, HOUR
    c, ws = camp()
    c.teams["SG-1"].until = c.now + 11 * HOUR
    c.teams["SG-2"].status, c.teams["SG-2"].until = "injured", c.now + 2 * DAY - 4 * HOUR
    c.teams["SG-3"].status, c.teams["SG-3"].until = "captured", c.now + 4 * DAY
    c.teams["SG-4"].status, c.teams["SG-4"].until = "lost", c.now + 2 * DAY + 5 * HOUR
    db = Database(c)
    db.tab = "teams"
    status = {r.key: r.cells[3] for r in db.rows()}
    assert status == {"SG-1": "STOOD DOWN 11H", "SG-2": "INJURED 1D 20H", "SG-3": "CAPTURED 4D",
                      "SG-4": "RE-FORMING 2D 5H"}


def test_the_world_file_header_names_an_unnamed_world_once():
    c, ws = camp()
    db = Database(c)
    db.world_id = ws[1].id
    assert db.detail()[0] == f"{ws[1].id} · PROBED"
    db.world_id = ws[2].id
    assert db.detail()[0] == f"HA'SHEK · {ws[2].id} · SURVEYED"
