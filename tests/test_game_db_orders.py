"""Part 9: orders from the Database -- o on Addresses, and Enter or o on the World file, opens the
briefing room's ORDERS panel for that address, hosting a Room (not a copy of its logic) in a box over the
lower part of the Database. Enter on Addresses still opens that address's World file, as it did before."""
from sgc.game import content, screens
from sgc.game.database import ORDERS_TABS, Database
from sgc.game.engine import Engine
from sgc.game.room import Room
from sgc.game.state import new_campaign
from sgc.layout import Rect, compute_layout
from sgc.term.canvas import Canvas


def db_engine():
    scenarios, _ = content.load(user=None)
    c = new_campaign("campaign", "officer", 2)
    c.stock["uav"] = 2
    c.upgrades.add("uav_program")
    engine = Engine(c, scenarios)
    db = Database(c, engine.schedule_view, engine)
    return db, c, engine


def select_address(db, wid):
    """Put the Addresses tab's selection on this world, wherever the sort puts it."""
    db.sel = next(i for i, r in enumerate(db.rows()) if r.key == wid)


def render(db, cols=100, rows=30):
    layout = compute_layout(cols, rows, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    screens.draw_database(cv, layout, db, "bar")
    return cv.text()


# ------------------------------------------------------------------ opening the panel

def test_enter_on_addresses_opens_the_world_file_not_orders():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    select_address(db, target)
    assert db.key("enter") is None
    assert db.orders is None
    assert db.tab == "world" and db.world_id == target


def test_o_on_addresses_opens_orders_for_the_selected_address():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    select_address(db, target)
    assert db.key("o") is None
    assert db.orders is not None and db.orders.world_id == target and db.orders.screen == "world"
    assert db.tab == "addresses"                   # staying put underneath the panel


def test_enter_on_the_world_file_opens_orders_for_its_address():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    db.world_id, db.tab = target, "world"
    db.key("enter")
    assert db.orders is not None and db.orders.world_id == target
    assert db.tab == "world"


def test_o_on_the_world_file_opens_orders_for_its_address():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    db.world_id, db.tab = target, "world"
    db.key("o")
    assert db.orders is not None and db.orders.world_id == target
    assert db.tab == "world"


def test_without_an_engine_o_does_nothing_on_those_tabs():
    c, _ = new_campaign("campaign", "officer", 2), None
    db = Database(c)                                # no engine: can't host a Room
    db.key("down")
    db.key("o")
    assert db.orders is None and db.tab == "addresses"
    db.tab = "world"
    db.key("o")
    db.key("enter")
    assert db.orders is None and db.tab == "world"


# ------------------------------------------------------------------ orders from the panel

def test_a_malp_probe_ordered_from_the_panel_queues_it_and_shows_the_reply():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    select_address(db, target)
    db.key("o")
    db.key("1")                                     # MALP PROBE
    assert db.orders.notice == f"MALP QUEUED FOR {target}"
    assert db.orders.screen == "world"              # back at the first step
    items = engine.schedule_view()
    assert any(i.kind == "dial_out" and i.id == f"dial:malp:{target}" for i in items)


def test_a_full_team_assignment_from_the_panel():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    c.worlds[target].status = "probed"
    select_address(db, target)
    db.key("o")
    db.key("5")                                     # ASSIGN TEAM
    assert db.orders.screen == "team_pick"
    db.key("1")                                     # the first available team
    assert db.orders.screen == "type_pick"
    db.key("1")                                     # the first mission type
    assert db.orders.screen == "world" and db.orders.notice
    assert c.teams["SG-1"].status in ("staging", "offworld") and c.teams["SG-1"].where == target


# ------------------------------------------------------------------ greyed rows match the briefing room

def test_greyed_rows_and_reasons_match_the_briefing_room_for_the_same_world():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]                      # still unexplored: ASSIGN TEAM is disabled
    select_address(db, target)
    db.key("o")
    db.key("5")                                     # the disabled ASSIGN TEAM row
    room = Room(engine)
    room.open_on_world(target)
    room.key("5")
    assert db.orders.items() == room.items()
    assert db.orders.notice == room.notice == "PROBE IT FIRST"


# ------------------------------------------------------------------ keys inside the panel

def test_esc_steps_back_then_closes():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    c.worlds[target].status = "probed"
    select_address(db, target)
    db.key("o")
    db.key("5")                                     # ASSIGN TEAM -> team_pick
    assert db.orders.screen == "team_pick"
    db.key("escape")
    assert db.orders is not None and db.orders.screen == "world"
    db.key("escape")
    assert db.orders is None


def test_ctrl_c_also_steps_back_and_closes():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    select_address(db, target)
    db.key("o")
    assert db.key("ctrl-c") is None
    assert db.orders is None


def test_note_typing_works_as_in_the_briefing_room():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    select_address(db, target)
    db.key("o")
    db.key("6")                                     # ADD NOTE
    assert db.orders.screen == "note" and db.text_mode
    for ch in "hi":
        db.key(f"ch:{ch}")
    db.key("enter")
    assert c.worlds[target].notes[-1][1] == "hi"
    assert db.orders.screen == "world" and db.orders.notice == "NOTE ADDED"
    assert not db.text_mode


def test_other_keys_are_ignored_while_the_panel_is_open():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    select_address(db, target)
    db.key("o")
    before = (db.tab, db.sel, db.sort, db.filter, db.query)
    for k in ("q", "right", "left", "tab", "s", "f", "/", "x", "[", "]", "o"):
        db.key(k)
    assert db.orders is not None
    assert (db.tab, db.sel, db.sort, db.filter, db.query) == before


# ------------------------------------------------------------------ alarms and what's kept

def test_an_alarm_drops_the_panel():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    select_address(db, target)
    db.key("o")
    assert db.orders is not None
    db.close_orders()                                # what app._back_to_the_gate_room calls on an alarm
    assert db.orders is None


def test_the_tab_search_and_scroll_are_kept_after_an_order():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    db.sort = "name"
    db.query = target.lower()
    select_address(db, target)
    sel_before = db.sel
    db.key("o")
    db.key("1")                                      # a MALP probe from the panel: already back at the
    assert db.orders.notice.startswith("MALP QUEUED")  # first step, so one Esc closes the panel
    db.key("escape")
    assert db.orders is None
    assert db.tab == "addresses" and db.query == target.lower() and db.sort == "name" and db.sel == sel_before


def test_the_world_file_scroll_is_kept_after_an_order():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    db.world_id, db.tab, db.scroll = target, "world", 3
    db.key("o")
    db.key("escape")
    assert db.orders is None and db.tab == "world" and db.scroll == 3


# ------------------------------------------------------------------ the legend

def test_the_legend_shows_orders_on_addresses_and_world_tabs():
    for tab in ORDERS_TABS:
        key = ("⏎", "ORDERS") if tab == "world" else ("o", "ORDERS")
        assert key in screens._db_keys(tab)
        assert any(k == key[0] for k, _ in screens._db_help_entries(tab))


def test_the_full_legend_lists_the_panels_keys():
    text = "\n".join(screens._db_help_rows(100, "addresses"))
    assert "choose in the ORDERS panel" in text
    assert "back a step in the panel" in text


# ------------------------------------------------------------------ drawing the panel

def test_the_panel_draws_as_a_box_over_the_database_with_numbered_items():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    select_address(db, target)
    db.key("o")
    text = render(db)
    assert f"ORDERS · {c.worlds[target].name.upper()}" in text
    assert "MALP PROBE" in text and "ASSIGN TEAM" in text


def test_a_greyed_row_and_the_notice_draw_in_the_panel():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]                      # unexplored: ASSIGN TEAM greyed
    select_address(db, target)
    db.key("o")
    db.key("5")
    text = render(db)
    assert "PROBE IT FIRST" in text


# ------------------------------------------------------------------ review: the panel is invisible compact

def test_o_in_a_compact_database_gives_a_notice_instead_of_the_panel():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    select_address(db, target)
    db.set_compact(True)
    assert db.key("o") is None
    assert db.orders is None
    assert db.message == "ORDERS NEED A LARGER PANE"
    assert db.tab == "addresses"


def test_enter_on_the_world_file_in_a_compact_database_gives_the_same_notice():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    db.world_id, db.tab = target, "world"
    db.set_compact(True)
    db.key("enter")
    assert db.orders is None and db.message == "ORDERS NEED A LARGER PANE"


def test_other_keys_still_work_in_a_compact_database():
    db, c, engine = db_engine()
    db.set_compact(True)
    before = db.sel
    db.key("down")
    assert db.sel != before
    db.key("right")
    assert db.tab == "world"


def test_becoming_compact_closes_an_open_panel():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    select_address(db, target)
    db.key("o")
    assert db.orders is not None
    db.set_compact(True)
    assert db.orders is None


def test_compact_database_never_draws_the_panel_even_if_one_is_forced_open():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    select_address(db, target)
    db.key("o")
    db.orders.open_on_world(target)                 # a panel somehow still open underneath
    layout = compute_layout(60, 16, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    screens.draw_database(cv, layout, db, "bar")     # must not hang or crash drawing an invisible panel
    assert "ORDERS ·" not in cv.text()


def test_the_compact_hint_line_shows_the_notice_on_the_addresses_tab():
    db, c, engine = db_engine()
    target = list(c.worlds)[3]
    select_address(db, target)
    db.set_compact(True)
    db.key("o")
    layout = compute_layout(60, 16, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    screens.draw_database(cv, layout, db, "bar")
    assert "ORDERS NEED A LARGER PANE" in cv.text().split("\n")[-1]


# ------------------------------------------------------------------ review: two-line labels in the panel

def injured_team_target(c, engine):
    """A probed world with one team injured (a two-line, greyed TEAM_PICK row), so ASSIGN TEAM shows a
    reason."""
    target = list(c.worlds)[3]
    c.worlds[target].status = "probed"
    from sgc.game.clock import DAY, HOUR
    c.teams["SG-2"].status, c.teams["SG-2"].until = "injured", c.now + 1 * DAY + 20 * HOUR
    return target


def test_the_team_picker_shows_an_injured_teams_reason():
    db, c, engine = db_engine()
    target = injured_team_target(c, engine)
    select_address(db, target)
    db.key("o")
    db.key("5")                                      # ASSIGN TEAM -> team_pick
    assert db.orders.screen == "team_pick"
    text = render(db)
    assert "INJURED 1D 20H" in text


class _FakeRoom:
    """Just enough of Room's interface for draw_orders_panel: a fixed item list and selection."""
    world_id = None
    text_mode = False
    notice = ""

    def __init__(self, items, sel):
        self._items, self.sel = items, sel
        self.c = type("C", (), {"worlds": {}})()

    def items(self):
        return self._items


def test_two_line_labels_render_every_line_not_just_the_first():
    items = [("MALP PROBE", True), ("ASSIGN TEAM\nSTAGING: ABYDOS", False)]
    room = _FakeRoom(items, sel=0)
    cv = Canvas(40, 10)
    screens.draw_orders_panel(cv, Rect(0, 0, 40, 10), room)
    text = cv.text()
    assert "ASSIGN TEAM" in text and "STAGING: ABYDOS" in text


def test_orders_height_counts_every_label_line_not_just_the_item_count():
    one_liners = [("A", True), ("B", True)]
    two_liners = [("A\nreason", False), ("B\nreason", False)]
    assert screens._orders_height(two_liners, 30, 20) > screens._orders_height(one_liners, 30, 20)


def test_the_panel_scrolls_to_keep_a_later_selection_on_screen():
    items = [(f"ITEM {i}\nREASON {i}", True) for i in range(6)]
    room = _FakeRoom(items, sel=5)                    # the last, two-line item selected
    cv = Canvas(40, 10)                               # too short to show all 12 label lines
    screens.draw_orders_panel(cv, Rect(0, 0, 40, 10), room)
    text = cv.text()
    assert "ITEM 5" in text and "REASON 5" in text
    assert "ITEM 0" not in text                       # scrolled the earliest items off screen
