import pytest

from sgc.game import screens
from sgc.game import content
from sgc.game.database import TAB_TITLES, TABS, Database
from sgc.game.engine import Engine
from sgc.game.menu import Menu
from sgc.game.room import Room
from sgc.game.state import Mission, new_campaign
from sgc.layout import compute_layout
from sgc.model import Prompt, Scene
from sgc.term.canvas import Canvas
from sgc import panels

RECORD = {"mode": "sandbox", "difficulty": "officer", "result": "overrun", "surveyed": 7, "days": 31}

DECISION_TEXT = ("Incoming wormhole. IDC is SG-3's code. SG-3 was captured on P3X-888 and "
                  "hasn't been recovered.")
DECISION_OPTIONS = [
    ("Open the iris", True),
    ("Keep the iris closed", True),
    ("Keep it closed and revoke SG-3's code", True),
    ("Send a MALP through first", True),
]


def full():
    layout = compute_layout(120, 36, 9, 18)
    return layout, Canvas(layout.cols, layout.rows)


def small(cols, rows):
    layout = compute_layout(cols, rows, 9, 18)
    return layout, Canvas(layout.cols, layout.rows)


def test_menu_lists_items_notice_and_records():
    layout, cv = full()
    m = Menu(False)
    m.notice = "SAVE DAMAGED — STARTING FRESH"
    screens.draw_menu(cv, layout, m, [RECORD])
    text = cv.text()
    for bit in ("STARGATE COMMAND", "1  AMBIENCE", "2  MISSIONS", "SAVE DAMAGED", "HALL OF RECORDS",
                "OVERRUN    70 7W 31D SAND OFF"):
        assert bit in text, bit


def test_prompt_shows_wrapped_options_and_countdown():
    layout, cv = full()
    prompt = Prompt("DECISION", "Incoming wormhole.\nIDC received: SG-3.",
                    [("Open the iris", True), ("Keep it closed and revoke SG-3's code", False)], 12.0, 7.2)
    screens.draw_prompt(cv, layout.side, prompt, 0.0)
    text = cv.text()
    for bit in ("DECISION", "IDC received: SG-3.", "1  Open the iris", "2  Keep it closed", " 8s"):
        assert bit in text, bit


@pytest.mark.parametrize("cols,rows", [(80, 22), (90, 24)])
def test_decision_fits_on_small_full_layouts(cols, rows):
    layout, cv = small(cols, rows)
    screens.draw_prompt(cv, layout.side, Prompt("DECISION", DECISION_TEXT, DECISION_OPTIONS, 15.0, 9.0), 0.0)
    text = cv.text()
    assert "Incoming" in text
    for i, (label, _) in enumerate(DECISION_OPTIONS):
        assert f"{i + 1}  {label.split()[0]}" in text


def test_countdown_bar_clamps_when_remaining_exceeds_total():
    layout, cv = full()
    s = Scene()
    s.prompt = Prompt("DECISION", "x", [("Open", True)], 10.0, 15.0)
    screens.draw_prompt(cv, layout.side, s.prompt, 0.0)
    r = layout.side
    border_col = r.x + r.w - 1
    row = cv.text().split("\n")[r.y + r.h - 2]
    assert row[border_col] == "│"


def test_room_label_only_in_the_briefing_room():
    layout, cv = full()
    screens.draw_room_label(cv, layout, Scene(view_p=0.0))
    assert "BRIEFING ROOM" in cv.text()
    layout, cv = full()
    screens.draw_room_label(cv, layout, Scene())
    assert "BRIEFING ROOM" not in cv.text()


GATE_KEYS = [("b", "BRIEFING"), ("d", "DATABASE"), ("?", "HELP"), ("q", "QUIT")]


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_the_header_shows_the_sgc_clock(cols, rows):
    layout, cv = small(cols, rows)
    c = new_campaign("campaign", "officer", 1)
    c.minutes = 11 * 1440 + 14 * 60 + 30
    screens.draw_header(cv, layout, c, None, 0.0)
    assert "DAY 12 · 14:30 SGC · DEFCON 5" in cv.text().split("\n")[0]
    screens.draw_header(cv, layout, c, "Missed check-in", 0.0)
    top = cv.text().split("\n")[0]
    assert "ALARM · MISSED CHECK-IN" in top and "DEFCON 2" in top


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_legend_bar_overlay_and_off(cols, rows):
    layout, cv = small(cols, rows)
    screens.draw_legend(cv, layout, "bar", GATE_KEYS)
    assert "b BRIEFING  d DATABASE  ? HELP  q QUIT" in cv.text().split("\n")[rows - 1]
    layout, cv = small(cols, rows)
    screens.draw_legend(cv, layout, "full", GATE_KEYS)
    text = cv.text()
    assert "CONTROLS" in text
    for key, label in screens.KEYS_HELP:
        assert label in text, label
    layout, cv = small(cols, rows)
    screens.draw_legend(cv, layout, "off", GATE_KEYS)
    assert cv.text().strip() == ""


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_full_legend_falls_back_to_the_bar_over_an_open_alarm(cols, rows):
    layout, cv = small(cols, rows)
    s = Scene()
    s.prompt = Prompt("MISSED CHECK-IN", "SG-2 missed its check-in from P3X-866.",
                      [("Send a MALP", True), ("Send SG-1", True), ("Wait 12 hours", True)], 60.0, 42.0)
    c = new_campaign("campaign", "officer", 1)
    screens.draw_game(cv, layout, s, c, 0.0)
    screens.draw_legend(cv, layout, "full", GATE_KEYS, busy=True)
    text = cv.text()
    assert "1  Send a MALP" in text and "3  Wait 12 hours" in text and "CONTROLS" not in text


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_full_legend_falls_back_to_the_bar_in_the_briefing_room(cols, rows):
    layout, cv = small(cols, rows)
    scenarios, _ = content.load(user=None)
    room = Room(Engine(new_campaign("campaign", "officer", 2), scenarios))
    screens.draw_room(cv, layout, room)
    screens.draw_legend(cv, layout, "full", GATE_KEYS, busy=True)
    text = cv.text()
    assert "1  DIALING LIST" in text and "CONTROLS" not in text


def test_full_legend_still_shows_when_not_busy():
    layout, cv = small(80, 22)
    screens.draw_legend(cv, layout, "full", GATE_KEYS)
    assert "CONTROLS" in cv.text()


def test_legend_cycles_bar_full_off():
    assert [screens.next_legend(s) for s in ("bar", "full", "off", "junk")] == ["full", "off", "bar", "bar"]


def test_status_shows_meters_drones_and_missions():
    layout, cv = full()
    c = new_campaign("campaign", "officer", 1)
    screens.draw_game(cv, layout, Scene(), c, 0.0)
    text = cv.text()
    for bit in ("SGC STATUS", "SECURITY", " 70", "PERSONNEL", "MALP 4  UAV 0", "NO TEAMS OUT"):
        assert bit in text, bit


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_an_alarm_prompt_sits_above_the_status(cols, rows):
    layout, cv = small(cols, rows)
    s = Scene()
    s.prompt = Prompt("MISSED CHECK-IN", "SG-2 missed its check-in from P3X-866.",
                      [("Send a MALP", True), ("Send SG-1", True), ("Wait 12 hours", True)], 60.0, 42.0)
    screens.draw_game(cv, layout, s, new_campaign("campaign", "officer", 1), 0.0)
    text = cv.text()
    for bit in ("MISSED CHECK-IN", "1  Send a MALP", "3  Wait 12 hours", "42s", "SECURITY"):
        assert bit in text, bit


def test_compact_shows_the_prompt_or_the_clock():
    layout = compute_layout(60, 16, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    s = Scene()
    s.prompt = Prompt("DECISION", "x", [("Open", True), ("Close", True), ("Locked", False)], 12.0, 3.1)
    c = new_campaign("campaign", "officer", 1)
    screens.draw_game(cv, layout, s, c, 0.0)
    assert " 4s [1] OPEN [2] CLOSE" in cv.text()
    screens.draw_game(cv, layout, Scene(), c, 0.0)
    assert "DAY 1 · 08:00 SGC" in cv.text()


def db_campaign():
    c = new_campaign("campaign", "officer", 3)
    ws = list(c.worlds.values())
    ws[1].status, ws[1].drone = "probed", "malp"
    ws[2].status = "surveyed"
    ws[2].names.append(("Tel'kar", "the locals", 700))
    ws[2].reports.append((700, "SG-2 surveyed the ruins."))
    c.missions.append(Mission(1, "SG-2", ws[2].id, "survey", 500, 1900, state="complete", findings=["ruins"]))
    return c, ws


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_the_database_fills_the_screen(cols, rows):
    layout, cv = small(cols, rows)
    c, ws = db_campaign()
    db = Database(c)
    screens.draw_database(cv, layout, db, "bar")
    text = cv.text()
    lines = text.split("\n")
    for bit in ("SGC DATABASE", "ADDRESSES", "WORLD FILE", "INTEL", "NAME", "GLYPHS", "STATUS",
                "SORT STATUS · FILTER ALL", "Abydos", "Tel'kar", "SURVEYED", "MALP", ws[1].id):
        assert bit in text, bit
    assert "q BACK" in lines[-1] and "DAY 1 · 08:00 SGC" in lines[1]
    db.key("/")
    db.key("ch:t")
    screens.draw_database(cv, layout, db, "off")
    assert "SEARCH: t_" in cv.text() and "q BACK" not in cv.text().split("\n")[-1]


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_database_world_file_missions_and_teams(cols, rows):
    layout, cv = small(cols, rows)
    c, ws = db_campaign()
    db = Database(c)
    db.world_id, db.tab = ws[2].id, "world"
    screens.draw_database(cv, layout, db, "bar")
    text = cv.text()
    assert "TEL'KAR · " in text and "Tel'kar — the locals" in text and "SG-2 surveyed the ruins." in text
    db.tab = "missions"
    screens.draw_database(cv, layout, db, "bar")
    assert "COMPLETE" in cv.text() and "Tel'kar" in cv.text()
    db.tab = "teams"
    screens.draw_database(cv, layout, db, "bar")
    assert "ELITE" in cv.text() and "RECON" in cv.text()


def test_the_selection_scrolls_into_view():
    layout, cv = small(80, 22)
    c, ws = db_campaign()
    db = Database(c)
    db.sort = "name"
    for _ in range(19):
        db.key("down")
    screens.draw_database(cv, layout, db, "bar")
    assert db.selected().cells[0][:20] in cv.text()


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_the_briefing_room_list(cols, rows):
    layout, cv = small(cols, rows)
    scenarios, _ = content.load(user=None)
    c = new_campaign("campaign", "officer", 2)
    room = Room(Engine(c, scenarios))
    screens.draw_room(cv, layout, room)
    assert "BRIEFING ROOM" in cv.text() and "1  DIALING LIST" in cv.text()
    room.key("1")
    room.key("2")
    room.key("1")
    screens.draw_room(cv, layout, room)
    text = cv.text()
    assert "1  MALP PROBE (3 LEFT)" in text and "MALP QUEUED" in text and "UNEXPLORED" in text
    room.key("6")
    room.key("ch:x")
    screens.draw_room(cv, layout, room)
    assert "> x_" in cv.text()


def test_the_briefing_room_on_a_small_terminal():
    layout = compute_layout(60, 16, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    scenarios, _ = content.load(user=None)
    room = Room(Engine(new_campaign("campaign", "officer", 2), scenarios))
    screens.draw_room(cv, layout, room)
    assert "1 DIALING LIST  2 TEAMS" in cv.text()


def test_the_world_file_wraps_and_scrolls_at_80_columns():
    layout, cv = small(80, 22)
    c, ws = db_campaign()
    w = ws[2]
    w.reports += [(701 + i, "A" * 90) for i in range(8)]
    w.notes.append((900, "REMEMBER TO BRING BACK A SAMPLE"))
    db = Database(c)
    db.world_id, db.tab = w.id, "world"
    screens.draw_database(cv, layout, db, "bar")
    text = cv.text()
    assert "↓ MORE" in text and "REMEMBER TO BRING BACK A SAMPLE" not in text
    for _ in range(50):
        db.key("down")
    screens.draw_database(cv, layout, db, "bar")
    text = cv.text()
    assert "REMEMBER TO BRING BACK A SAMPLE" in text and "↑ MORE" in text


def test_addresses_table_fits_80_columns_and_the_name_column_grows():
    layout, cv = small(80, 22)
    db = Database(db_campaign()[0])
    screens.draw_database(cv, layout, db, "bar")
    lines = cv.text().split("\n")
    header = next(line for line in lines if "GLYPHS" in line)
    assert "DRONE" in header
    assert header.index("DRONE") + len("DRONE") <= 79

    layout2, cv2 = small(200, 40)
    db2 = Database(db_campaign()[0])
    screens.draw_database(cv2, layout2, db2, "bar")
    header2 = next(line for line in cv2.text().split("\n") if "GLYPHS" in line)
    assert header2.index("GLYPHS") > header.index("GLYPHS")


def test_table_cells_truncate_with_ellipsis():
    layout, cv = small(80, 22)
    c, ws = db_campaign()
    ws[2].names.append(("A Very Long World Name That Overflows The Column", "test", 1))
    db = Database(c)
    db.sel = next(i for i, r in enumerate(db.rows()) if r.key == ws[2].id)
    screens.draw_database(cv, layout, db, "bar")
    assert "…" in cv.text()


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_the_roster_fits_the_panel(cols, rows):
    layout, cv = small(cols, rows)
    scenarios, _ = content.load(user=None)
    c = new_campaign("campaign", "officer", 2)
    w = list(c.worlds.values())[5]
    c.teams["SG-4"].status, c.teams["SG-4"].where = "offworld", w.id
    c.teams["SG-2"].status, c.teams["SG-2"].until = "lost", c.now + 3 * 24 * 60
    room = Room(Engine(c, scenarios))
    room.key("2")                                   # TEAMS
    screens.draw_room(cv, layout, room)
    text = cv.text()
    assert "…" not in text and "SG-4 · SCIENCE ·" in text and f"AWAY: {w.name}" in text
    if layout.side.w >= 38:
        assert "SG-4 · SCIENCE · GREEN" in text
    assert "RE-FORMING 3D" in text and "5  COMMISSION" in text
    if layout.side.w >= 38:
        assert "6  BACK" in text


def test_standing_orders_are_readable_in_full():
    from sgc.game.orders import SITUATIONS, label
    layout, cv = full()
    assert layout.side.w == 38
    scenarios, _ = content.load(user=None)
    c = new_campaign("campaign", "officer", 2)
    room = Room(Engine(c, scenarios))
    room.key("3")
    screens.draw_room(cv, layout, room)
    text = cv.text()
    assert "…" not in text and "Send a MALP to search" in text and "Team under fire" in text
    side = [line[layout.side.x:layout.side.x + layout.side.w] for line in text.split("\n")]
    flat = " ".join(" ".join(line.strip(" │").split()) for line in side)
    for sid in SITUATIONS:
        assert label(sid, c.orders[sid]) in flat, sid
    assert "8  BACK" in text


def test_a_long_room_item_wraps_and_the_selection_stays_on_screen():
    layout, cv = small(80, 22)
    scenarios, _ = content.load(user=None)
    room = Room(Engine(new_campaign("campaign", "officer", 2), scenarios))
    room.key("3")                                   # STANDING ORDERS: two or three rows each
    room.sel = 7                                    # BACK, at the bottom
    screens.draw_room(cv, layout, room)
    assert "8  BACK" in cv.text()


def test_the_database_full_legend_explains_the_keys():
    layout, cv = small(120, 36)
    db = Database(db_campaign()[0])
    screens.draw_database(cv, layout, db, "bar")
    bar = cv.text()
    screens.draw_database(cv, layout, db, "full")
    text = cv.text()
    assert text != bar and "sort by status or name" in text and "close the Database" in text


def test_the_database_legend_is_tab_aware():
    layout, cv = small(80, 22)
    db = Database(db_campaign()[0])
    screens.draw_database(cv, layout, db, "bar")
    bottom = cv.text().split("\n")[-1]
    assert "/ SEARCH" in bottom and "s SORT" in bottom and "f FILTER" in bottom and "⏎ ORDERS" in bottom
    db.tab = "world"
    screens.draw_database(cv, layout, db, "bar")
    bottom = cv.text().split("\n")[-1]
    assert "/ SEARCH" not in bottom and "s SORT" not in bottom and "f FILTER" not in bottom
    assert "⏎ OPEN" not in bottom and "⏎ ORDERS" in bottom and "↑↓ SCROLL" in bottom
    db.tab = "teams"
    screens.draw_database(cv, layout, db, "bar")
    bottom = cv.text().split("\n")[-1]
    assert "⏎ OPEN" not in bottom and "↑↓ SELECT" in bottom


def test_the_database_on_a_small_terminal():
    layout = compute_layout(60, 16, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    db = Database(db_campaign()[0])
    screens.draw_database(cv, layout, db, "bar")
    text = cv.text()
    assert f"TAB 1/{len(TABS)} ADDRESSES" in text and "Abydos" in text and "q BACK" in text


def test_the_database_on_a_tiny_terminal():
    layout = compute_layout(30, 10, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    db = Database(db_campaign()[0])
    screens.draw_database(cv, layout, db, "bar")
    assert "ENLARGE PANE" in cv.text()


from sgc.game.schedule import QueueItem

WIDEST = [
    QueueItem("dial:uav:P3X-774", "dial_out", "1", "UAV → P3X-774", "WAITING FOR THE GATE", (0, 0), True, True),
    QueueItem("dial:search:3:SG-1", "dial_out", "2", "SG-1 → P3X-774 FOR SG-3", "WAITING FOR THE GATE", (0, 1),
              True, True),
    QueueItem("drone:P2A-018", "drone", "D12 23:40", "MALP AT P2A-018", "REPORT EXPECTED D12 23:40–D13 00:40",
              (1, 1)),
    QueueItem("mission:3", "mission", "D12 02:00", "SG-2 CONTACT OF P3X-774",
              "CHECK-IN D12 02:00 · DUE HOME D13 09:00", (1, 2)),
    QueueItem("team:SG-4", "team", "D13 18:00", "SG-4 RE-FORMING", "READY D13 18:00", (1, 3)),
]


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_the_queue_tab_draws_every_row_in_full(cols, rows):
    layout, cv = small(cols, rows)
    db = Database(db_campaign()[0], lambda: WIDEST)
    db.tab = "queue"
    screens.draw_database(cv, layout, db, "bar")
    text = cv.text()
    lines = text.split("\n")
    assert "QUEUE" in lines[0] and "WHEN" in text and "WHAT" in text and "STATUS" in text
    for item in WIDEST:
        for cell in item.cells:
            assert cell in text, cell
    assert "…" not in text
    assert "x CANCEL" in lines[-1] and "[ ] MOVE" in lines[-1] and "/ SEARCH" in lines[-1]
    assert "s SORT" not in lines[-1]


def test_the_queue_tab_shows_the_engines_reply_and_nothing_scheduled():
    layout, cv = small(80, 22)
    db = Database(db_campaign()[0], lambda: WIDEST)
    db.tab = "queue"
    db.message = "CANCEL THE UAV TO P3X-774?  x AGAIN TO CONFIRM"
    screens.draw_database(cv, layout, db, "bar")
    assert db.message in cv.text().split("\n")[1]
    empty = Database(db_campaign()[0], lambda: [])
    empty.tab = "queue"
    screens.draw_database(cv, layout, empty, "bar")
    assert "NOTHING SCHEDULED" in cv.text()


def test_the_full_database_legend_explains_the_queue_keys_on_the_queue_tab():
    layout, cv = small(80, 22)
    db = Database(db_campaign()[0], lambda: WIDEST)
    screens.draw_database(cv, layout, db, "full")
    assert "cancel a dial-out" not in cv.text()
    db.tab = "queue"
    screens.draw_database(cv, layout, db, "full")
    text = cv.text()
    assert "cancel a dial-out, x to confirm" in text and "move a dial-out up, down" in text


def test_the_queue_tab_on_a_small_terminal():
    layout = compute_layout(60, 16, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    db = Database(db_campaign()[0], lambda: WIDEST)
    db.tab = "queue"
    screens.draw_database(cv, layout, db, "bar")
    text = cv.text()
    assert f"TAB {len(TABS)}/{len(TABS)} QUEUE" in text and "UAV → P3X-774" in text and "x CANCEL" in text
    db.message = "CANCELLED: UAV TO P3X-774"
    screens.draw_database(cv, layout, db, "bar")
    assert "CANCELLED: UAV TO P3X-774" in cv.text().split("\n")[-1]


DAY100 = [
    QueueItem("dial:uav:P3X-774", "dial_out", "1", "UAV → P3X-774", "WAITING FOR THE GATE", (0, 0), True, True),
    QueueItem("dial:search:3:SG-1", "dial_out", "2", "SG-1 → P3X-774 FOR SG-3", "WAITING FOR THE GATE", (0, 1),
              True, True),
    QueueItem("drone:P2A-018", "drone", "D100 23:40", "MALP AT P2A-018", "REPORT EXPECTED D100 23:40–D101 00:40",
              (1, 1)),
    QueueItem("mission:3", "mission", "D100 02:00", "SG-2 CONTACT OF P3X-774",
              "CHECK-IN D100 02:00 · DUE HOME D101 09:00", (1, 2)),
    QueueItem("team:SG-4", "team", "D101 18:00", "SG-4 RE-FORMING", "READY D101 18:00", (1, 3)),
]


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_the_queue_tab_fits_rows_past_day_100(cols, rows):
    layout, cv = small(cols, rows)
    db = Database(db_campaign()[0], lambda: DAY100)
    db.tab = "queue"
    screens.draw_database(cv, layout, db, "bar")
    text = cv.text()
    for item in DAY100:
        for cell in item.cells:
            assert cell in text, cell
    assert "…" not in text


def test_the_full_database_legend_only_lists_keys_that_do_something_on_the_tab():
    layout, cv = small(80, 22)
    db = Database(db_campaign()[0], lambda: WIDEST)
    db.tab = "queue"
    screens.draw_database(cv, layout, db, "full")
    text = cv.text()
    assert "search the schedule" in text
    for bit in ("search names, ids and glyphs", "sort by status or name", "filter by status",
                "open a world's file"):
        assert bit not in text, bit
    db.tab = "addresses"
    screens.draw_database(cv, layout, db, "full")
    text = cv.text()
    assert "search names, ids and glyphs" in text and "search the schedule" not in text


def test_the_engines_reply_is_clipped_never_silently_cut():
    layout, cv = small(80, 22)
    db = Database(db_campaign()[0], lambda: WIDEST)
    db.tab = "queue"
    db.message = "CANCEL THE UAV TO P3X-774?  x AGAIN TO CONFIRM"
    screens.draw_database(cv, layout, db, "bar")
    assert db.message in cv.text().split("\n")[1]        # never cut at 80 columns
    db.message = "X" * 200
    screens.draw_database(cv, layout, db, "bar")
    assert "…" in cv.text().split("\n")[1]                # a truly oversized reply is clipped visibly, not cut


def test_open_is_in_the_legend_wherever_enter_opens_a_world():
    from sgc.game.database import OPENS, ORDERS_TABS
    for tab in TABS:
        opens, orders = tab in OPENS, tab in ORDERS_TABS
        assert (("⏎", "OPEN") in screens._db_keys(tab)) == opens, tab
        assert (("⏎", "ORDERS") in screens._db_keys(tab)) == orders, tab
        assert ("⏎ OPEN" in screens._db_hint(tab)) == opens, tab
        assert any(k == "⏎" for k, _ in screens._db_help_entries(tab)) == (opens or orders), tab


@pytest.mark.parametrize("tab", ["factions", "trade", "arcs"])
def test_the_new_tabs_draw_and_the_current_tab_stays_in_the_strip(tab):
    layout, cv = small(80, 22)
    db = Database(db_campaign()[0])
    db.tab = tab
    screens.draw_database(cv, layout, db, "bar")
    assert TAB_TITLES[tab] in cv.text().split("\n")[0]


from sgc.game import arcs, database, factions, trade


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_the_status_panel_shows_funding_and_naquadah(cols, rows):
    layout, cv = small(cols, rows)
    c = new_campaign("campaign", "officer", 1)
    c.naquadah = 7
    screens.draw_game(cv, layout, Scene(), c, 0.0)
    if layout.mode == "full":
        assert "FUNDING 500 · NAQUADAH 7" in cv.text()


def test_defcon_follows_what_the_sgc_knows():
    layout, cv = full()
    c = new_campaign("campaign", "officer", 1)
    c.factions["sokar"].attention = 60
    screens.draw_header(cv, layout, c, None, 0.0)
    assert "DEFCON 5" in cv.text().split("\n")[0]
    factions.know(c, "sokar", "the Jaffa")
    screens.draw_header(cv, layout, c, None, 0.0)
    assert "DEFCON 3" in cv.text().split("\n")[0]


def test_the_tab_strip_scrolls_to_keep_the_current_tab_in_view():
    parts = screens._tab_strip(database.TABS, "arcs", 60)
    text = "".join(label for label, _ in parts)
    assert " ARCS " in text and text.startswith("‹") and len(text) <= 60
    assert [on for label, on in parts if label.strip() == "ARCS"] == [True]
    wide = "".join(label for label, _ in screens._tab_strip(database.TABS, "addresses", 200))
    assert "‹" not in wide and "›" not in wide and " ADDRESSES " in wide


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_the_new_tabs_draw_at_both_sizes(cols, rows):
    layout, cv = small(cols, rows)
    c, ws = db_campaign()
    factions.know(c, "apophis", "the Jaffa")
    trade.new_deal(c, ws[2].id, "naquadah", 2)
    arcs.start(c, "apophis")
    db = Database(c)
    for tab, bit in (("factions", "Apophis"), ("trade", "2 NAQUADAH"), ("arcs", "Apophis and Chulak")):
        db.tab = tab
        screens.draw_database(cv, layout, db, "bar")
        text = cv.text()
        assert database.TAB_TITLES[tab] in text.split("\n")[0] and bit in text, (tab, cols)


def test_the_hint_shows_on_the_addresses_tab():
    layout, cv = full()
    c, ws = db_campaign()
    c.hints.add("explored")
    screens.draw_database(cv, layout, Database(c), "bar")
    assert database.HINT in cv.text()


def test_every_database_key_is_in_its_legend():
    for tab in database.TABS:
        help_keys = {k for k, _ in screens._db_help_entries(tab)}
        for key, _ in screens._db_keys(tab):
            assert key in help_keys, (tab, key)
    assert "⏎" in [k for k, _ in screens._db_keys("trade")] and "⏎" in [k for k, _ in screens._db_keys("arcs")]
    assert "⏎" not in [k for k, _ in screens._db_keys("factions")]


def test_every_room_key_is_in_the_help():
    keys = {k for k, _ in screens.KEYS_HELP}
    for k in ("b", "d", "1-9", "↑↓ ⏎", "?", "q", "^C Esc"):
        assert k in keys


# ---------------------------------------------------------------- the GATE QUEUE box

from sgc.layout import Rect

BRIEFS = [
    QueueItem("dial:depart:1", "dial_out", "1", "SG-2 → ABYDOS (SURVEY)", "WAITING FOR THE GATE", (0, 0),
              brief="1 SG-2 STAGING ABYDOS"),
    QueueItem("dial:malp:P3X-888", "dial_out", "2", "MALP → P3X-888", "WAITING FOR THE GATE", (0, 1),
              brief="2 MALP → P3X-888"),
    QueueItem("mission:3", "mission", "14:00", "SG-1 SURVEY OF P3X-774", "CHECK-IN 14:00", (1, 840),
              brief="SG-1 CHECK-IN 14:00"),
    QueueItem("team:SG-4", "team", "D2 09:00", "SG-4 RE-FORMING", "READY D2 09:00", (1, 1980),
              brief="SG-4 RE-FORMING D2 09:00"),
    QueueItem("team:SG-3", "team", "D4 18:00", "SG-3 INJURED", "BACK D4 18:00", (1, 5400),
              brief="SG-3 INJURED D4 18:00"),
    QueueItem("deal:1", "delivery", "D5 08:00", "DELIVERY FROM LANGARA", "2 NAQUADAH · 5 TO COME", (1, 6240),
              brief="DELIVERY LANGARA D5 08:00"),
    QueueItem("deal:2", "delivery", "D6 08:00", "DELIVERY FROM K'TAU", "2 NAQUADAH · 5 TO COME", (1, 7680),
              brief="DELIVERY K'TAU D6 08:00"),
]


def box_lines(cv, r):
    return [line[r.x:r.x + r.w] for line in cv.text().split("\n")[r.y:r.y + r.h]]


def test_the_queue_box_height_fits_its_items_and_shrinks_then_goes():
    assert screens.queue_height(0, 20) == 3                  # "NOTHING QUEUED" takes one row
    assert screens.queue_height(2, 20) == 4
    assert screens.queue_height(9, 20) == screens.QUEUE_ROWS + 2
    assert screens.queue_height(9, 5) == 5                  # shrunk to the rows there are
    assert screens.queue_height(9, 3) == 3
    assert screens.queue_height(9, 2) == 0 and screens.queue_height(0, -4) == 0


def test_the_queue_box_draws_every_row_when_they_fit():
    cv = Canvas(40, 10)
    r = Rect(0, 0, 38, 7)
    screens.draw_queue(cv, r, BRIEFS[:5])
    lines = box_lines(cv, r)
    assert "GATE QUEUE" in lines[0]
    assert [line[2:].rstrip(" │") for line in lines[1:6]] == [i.brief for i in BRIEFS[:5]]


def test_an_overflowing_queue_ends_with_how_many_more():
    cv = Canvas(40, 10)
    r = Rect(0, 0, 38, 7)
    screens.draw_queue(cv, r, BRIEFS)
    lines = box_lines(cv, r)
    assert lines[4][2:].rstrip(" │") == BRIEFS[3].brief
    assert lines[5][2:].rstrip(" │") == "+3 MORE · d"


def test_an_empty_queue_says_nothing_is_queued():
    cv = Canvas(40, 5)
    r = Rect(0, 0, 38, 3)
    screens.draw_queue(cv, r, [])
    assert "NOTHING QUEUED" in box_lines(cv, r)[1]


def test_long_rows_are_cut_with_an_ellipsis():
    cv = Canvas(20, 5)
    r = Rect(0, 0, 16, 3)
    screens.draw_queue(cv, r, BRIEFS[:1])
    row = box_lines(cv, r)[1]
    assert row[2:14] == "1 SG-2 STAG…" and row[15] == "│"


def test_the_side_split_gives_the_prompt_the_queue_rows_first():
    assert screens.side_split(30, None, 3) == (0, 5, 7)             # no alarm: the box sits over the screens
    assert screens.side_split(30, None, None) == (0, 0, 7)          # no box at all
    assert screens.side_split(16, None, 3) == (0, 3, 7)             # 80x22: shrunk, DESTINATION kept
    assert screens.side_split(15, None, 3) == (0, 0, 7)             # too short for even one row
    assert screens.side_split(30, 10, 3) == (18, 5, 7)              # a short alarm leaves the box alone
    assert screens.side_split(30, 20, 3) == (20, 3, 7)              # a longer one shrinks it...
    assert screens.side_split(30, 22, 3) == (23, 0, 7)              # ...then takes it...
    assert screens.side_split(30, 26, 3) == (26, 0, 4)              # ...then rows from SGC STATUS


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_the_gate_room_shows_the_queue_above_the_status(cols, rows):
    layout, cv = small(cols, rows)
    screens.draw_game(cv, layout, Scene(), new_campaign("campaign", "officer", 1), 0.0, BRIEFS[:3])
    lines = cv.text().split("\n")
    top = next(i for i, line in enumerate(lines) if "GATE QUEUE" in line)
    status = next(i for i, line in enumerate(lines) if "SGC STATUS" in line)
    assert top < status
    if layout.side.h >= 7 + 6 + 5:                 # room for every row
        assert "1 SG-2 STAGING ABYDOS" in lines[top + 1] and "SG-1 CHECK-IN 14:00" in lines[top + 3]
        assert status == top + 5
    else:                                           # 80x22: one row, and it counts them
        assert "+3 MORE · d" in lines[top + 1] and status == top + 3


def test_an_alarm_takes_the_queue_box_s_rows_first():
    layout, cv = small(80, 22)
    s = Scene()
    s.prompt = Prompt("DECISION", DECISION_TEXT, DECISION_OPTIONS, 15.0, 9.0)
    screens.draw_game(cv, layout, s, new_campaign("campaign", "officer", 1), 0.0, BRIEFS)
    text = cv.text()
    assert "GATE QUEUE" not in text and "1  Open the iris" in text and "SGC STATUS" in text


@pytest.mark.parametrize("cols,rows", [(80, 22), (120, 36)])
def test_the_briefing_room_keeps_the_queue_at_the_foot_of_its_list(cols, rows):
    layout, cv = small(cols, rows)
    scenarios, _ = content.load(user=None)
    room = Room(Engine(new_campaign("campaign", "officer", 2), scenarios))
    room.key("3")                                   # STANDING ORDERS: a long list
    room.sel = 7                                    # BACK, at the bottom
    screens.draw_room(cv, layout, room, BRIEFS[:2])
    text = cv.text()
    assert "GATE QUEUE" in text and "2 MALP → P3X-888" in text and "8  BACK" in text
    lines = text.split("\n")
    assert next(i for i, line in enumerate(lines) if "8  BACK" in line) < \
        next(i for i, line in enumerate(lines) if "GATE QUEUE" in line)


def test_the_queue_box_stays_while_typing_a_note():
    layout, cv = full()
    scenarios, _ = content.load(user=None)
    room = Room(Engine(new_campaign("campaign", "officer", 2), scenarios))
    for k in ("1", "1", "6", "ch:x"):              # dialing list, Abydos, ADD NOTE, type
        room.key(k)
    screens.draw_room(cv, layout, room, [])
    assert "> x_" in cv.text() and "NOTHING QUEUED" in cv.text()


def test_no_queue_box_on_a_small_terminal():
    layout = compute_layout(60, 16, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    c = new_campaign("campaign", "officer", 1)
    screens.draw_game(cv, layout, Scene(), c, 0.0, BRIEFS)
    assert "GATE QUEUE" not in cv.text() and "STAGING" not in cv.text()
    scenarios, _ = content.load(user=None)
    screens.draw_room(cv, layout, Room(Engine(c, scenarios)), BRIEFS)
    assert "GATE QUEUE" not in cv.text()


def test_a_quitting_scene_blanks_the_queue_box_before_the_status_box():
    layout, cv = full()
    c = new_campaign("campaign", "officer", 1)
    s = Scene(blank_panels=panels.BASE_PANELS + 1, dim=0.3)
    screens.draw_game(cv, layout, s, c, 0.0, BRIEFS[:3])
    text = cv.text()
    assert "GATE QUEUE" not in text and "NO SIGNAL" in text
    assert "SGC STATUS" in text


def test_a_quitting_scene_blanks_the_queue_and_status_boxes_in_the_game():
    layout, cv = full()
    c = new_campaign("campaign", "officer", 1)
    s = Scene(blank_panels=panels.TOTAL_PANELS, dim=0.3)
    screens.draw_game(cv, layout, s, c, 0.0, BRIEFS[:3])
    text = cv.text()
    assert "GATE QUEUE" not in text and "SGC STATUS" not in text
    assert text.count("NO SIGNAL") >= 2


def test_a_quitting_scene_blanks_the_queue_and_the_prompt():
    layout, cv = small(80, 22)
    s = Scene(blank_panels=panels.TOTAL_PANELS, dim=0.3)
    s.prompt = Prompt("DECISION", DECISION_TEXT, DECISION_OPTIONS, 15.0, 9.0)
    screens.draw_game(cv, layout, s, new_campaign("campaign", "officer", 1), 0.0, BRIEFS)
    text = cv.text()
    assert "DECISION" not in text and "Open the iris" not in text
    assert "NO SIGNAL" in text


def test_a_quitting_scene_blanks_the_briefing_rooms_boxes_too():
    layout, cv = full()
    scenarios, _ = content.load(user=None)
    room = Room(Engine(new_campaign("campaign", "officer", 2), scenarios))
    s = Scene(blank_panels=panels.TOTAL_PANELS, dim=0.3)
    screens.draw_room(cv, layout, room, BRIEFS[:2], s)
    text = cv.text()
    assert "GATE QUEUE" not in text and "BRIEFING ROOM" not in text and "DIALING LIST" not in text
    assert text.count("NO SIGNAL") >= 2
