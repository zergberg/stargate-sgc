"""Part 9: orders from the Database -- o on Addresses, and Enter or o on the World file, opens the
briefing room's ORDERS panel for that address, hosting a Room (not a copy of its logic) in a box over the
lower part of the Database. Enter on Addresses still opens that address's World file, as it did before."""
from sgc.game import content, screens
from sgc.game.database import ORDERS_TABS, Database
from sgc.game.engine import Engine
from sgc.game.room import Room
from sgc.game.state import new_campaign
from sgc.layout import compute_layout
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
