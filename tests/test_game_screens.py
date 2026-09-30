import pytest

from sgc.game import screens
from sgc.game import content
from sgc.game.database import Database
from sgc.game.engine import Engine
from sgc.game.menu import Menu
from sgc.game.room import Room
from sgc.game.state import Mission, new_campaign
from sgc.layout import compute_layout
from sgc.model import Prompt, Scene
from sgc.term.canvas import Canvas

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
                "OVERRUN 7W 31D SAND OFF"):
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
    for bit in ("SGC STATUS", "SECURITY", " 70", "PERSONNEL", "MALP 4  UAV 2", "NO TEAMS OUT"):
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
    room.key("5")
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
    assert "RE-FORMING 3D" in text and "5  BACK" in text


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
    assert "/ SEARCH" in bottom and "s SORT" in bottom and "f FILTER" in bottom and "⏎ OPEN" in bottom
    db.tab = "world"
    screens.draw_database(cv, layout, db, "bar")
    bottom = cv.text().split("\n")[-1]
    assert "/ SEARCH" not in bottom and "s SORT" not in bottom and "f FILTER" not in bottom
    assert "⏎ OPEN" not in bottom and "↑↓ SCROLL" in bottom
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
    assert "TAB 1/6 ADDRESSES" in text and "Abydos" in text and "q BACK" in text


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
    assert "TAB 6/6 QUEUE" in text and "UAV → P3X-774" in text and "x CANCEL" in text
    db.message = "CANCELLED: UAV TO P3X-774"
    screens.draw_database(cv, layout, db, "bar")
    assert "CANCELLED: UAV TO P3X-774" in cv.text().split("\n")[-1]
