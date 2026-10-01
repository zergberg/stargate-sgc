"""Part 8: the dialing list shows the selected address in brief, above the menu, as the selection moves."""
from sgc.game import screens
from sgc.game.clock import short
from sgc.layout import compute_layout
from sgc.term.canvas import Canvas
from tests.test_game_room import room


def open_list(r, c):
    """Open the dialing list and move the selection to wid; returns its row index."""
    r.key("1")
    wid = list(c.worlds)[3]
    r.sel = list(c.worlds).index(wid)
    return wid


def test_a_team_there_shows_offworld():
    r, c = room()
    wid = open_list(r, c)
    c.teams["SG-2"].status, c.teams["SG-2"].where = "offworld", wid
    assert r.detail()[1] == "SG-2 OFFWORLD"
    assert r.items()[r.sel][0].split("·")[-1].strip() == "SG-2"


def test_a_team_staging_shows_staging():
    r, c = room()
    wid = open_list(r, c)
    c.teams["SG-3"].status, c.teams["SG-3"].where = "staging", wid
    assert r.detail()[1] == "SG-3 STAGING"


def test_a_drone_shows_on_site():
    r, c = room()
    wid = open_list(r, c)
    c.worlds[wid].drone = "uav"
    assert r.detail()[1] == "UAV ON SITE"
    assert r.items()[r.sel][0].rstrip().endswith("UAV")


def test_a_wreck_shows_on_site_and_in_the_row():
    r, c = room()
    wid = open_list(r, c)
    c.worlds[wid].wreck = "crashed"
    assert r.detail()[1] == "UAV WRECK"
    assert r.items()[r.sel][0].rstrip().endswith("WRECK")


def test_a_drone_and_a_wreck_and_a_team_all_join_with_a_dot():
    r, c = room()
    wid = open_list(r, c)
    c.teams["SG-2"].status, c.teams["SG-2"].where = "staging", wid
    c.worlds[wid].drone, c.worlds[wid].wreck = "malp", "shot_down"
    assert r.detail()[1] == "SG-2 STAGING · MALP ON SITE · UAV WRECK"


def test_nobody_on_site_when_nothing_is_there():
    r, c = room()
    wid = open_list(r, c)
    assert c.worlds[wid].drone is None and c.worlds[wid].wreck is None
    assert r.detail()[1] == "NOBODY ON SITE"


def test_an_unexplored_address_says_not_yet_probed_and_never_visited():
    r, c = room()
    wid = open_list(r, c)
    w = c.worlds[wid]
    assert w.status == "unexplored" and w.last_visit is None
    lines = r.detail()
    assert lines[0] == f"{w.name} · UNEXPLORED"
    assert lines[2] == "NOT YET PROBED"
    assert lines[3] == "NEVER VISITED"


def test_known_readings_in_short_form():
    r, c = room()
    wid = open_list(r, c)
    w = c.worlds[wid]
    w.status = "probed"
    w.seen = {"env": "toxic atmosphere", "life": "humanoid life signs", "features": "ruins, energy readings"}
    w.last_visit = 100
    assert r.detail() == [f"{w.name} · PROBED", "NOBODY ON SITE", "TOXIC · LIFE SIGNS · RUINS · TECH",
                          f"LAST VISIT {short(100)}"]


def test_no_life_and_naquadah_and_a_uav_settlement_word():
    r, c = room()
    wid = open_list(r, c)
    w = c.worlds[wid]
    w.status, w.last_visit = "surveyed", 50
    w.seen = {"env": "breathable atmosphere", "life": "none detected", "features": "naquadah traces"}
    assert r.detail()[2] == "AIR OK · NO LIFE · NAQ"
    w.seen = {"env": "breathable atmosphere", "inhabitants": "Jaffa garrison"}
    assert r.detail()[2] == "AIR OK · JAFFA"
    w.seen = {"inhabitants": "settlement"}
    assert r.detail()[2] == "HUMANS"


def test_an_inconclusive_minimal_reading_adds_no_life_word():
    r, c = room()
    wid = open_list(r, c)
    w = c.worlds[wid]
    w.status, w.seen = "probed", {"env": "extreme temperatures", "life": "inconclusive"}
    assert r.detail()[2] == "EXTREME"


def test_hidden_traits_never_leak_only_seen_words_show():
    r, c = room()
    wid = open_list(r, c)
    w = c.worlds[wid]
    w.env, w.inhabitants, w.features = "radiation", "goauld", ("technology",)      # the hidden truth
    w.status = "probed"
    w.seen = {"env": "breathable atmosphere", "life": "none detected"}            # what the SGC was told
    assert r.detail()[2] == "AIR OK · NO LIFE"                                    # never RADIATION or GOAULD


def test_a_queued_malp_shows_its_gate_position():
    r, c = room()
    wid = open_list(r, c)
    r.e.probe(wid)
    assert r.detail()[-1] == "NEVER VISITED · MALP QUEUED #1"


def test_an_uplink_window_shows_as_a_queued_line():
    r, c = room()
    wid = open_list(r, c)
    w = c.worlds[wid]
    w.drone = "malp"
    c.events.push(c.now + 60, "uplink", {"world": wid, "drone": "malp", "from": c.now + 60, "to": c.now + 300})
    item = next(i for i in r.e.schedule_view() if i.kind == "uplink")
    window = item.status.removeprefix("EXPECTED ")
    assert r.detail()[-1] == f"NEVER VISITED · UPLINK {window}"


def test_a_teams_check_in_shows_up_for_its_world():
    r, c = room()
    wid = open_list(r, c)
    c.worlds[wid].status = "probed"
    r.e.assign(wid, "SG-2", "survey")
    r.e.advance(1)
    item = next(i for i in r.e.schedule_view() if i.kind == "mission")
    assert r.detail()[-1] == f"NEVER VISITED · {item.brief}" and "CHECK-IN" in item.brief


def test_a_parked_drone_shows_its_next_checkin():
    r, c = room()
    wid = open_list(r, c)
    c.worlds[wid].drone = "malp"
    c.events.push(c.now + 60, "drone_checkin", {"world": wid, "drone": "malp"})
    item = next(i for i in r.e.schedule_view() if i.kind == "drone_checkin")
    assert r.detail()[-1] == f"NEVER VISITED · CHECK-IN {item.when}"


def test_a_pending_drone_home_shows_its_line():
    r, c = room()
    wid = open_list(r, c)
    c.worlds[wid].drone = "malp"
    c.events.push(c.now + 15, "drone_home", {"world": wid, "mission": 1, "team": "SG-2", "drone": "malp"})
    item = next(i for i in r.e.schedule_view() if i.kind == "drone_home")
    assert r.detail()[-1] == f"NEVER VISITED · {item.brief}"
    assert "SG-2 SENDING MALP HOME" in item.brief


def test_a_note_shows_its_first_line_clipped():
    r, c = room()
    wid = open_list(r, c)
    c.worlds[wid].notes.append((0, "Kasuf says hi\nsecond line"))
    assert r.detail()[-1] == "NOTE: Kasuf says hi"


def test_back_selected_has_no_detail():
    r, c = room()
    r.key("1")
    r.sel = len(r.items()) - 1
    assert r.items()[r.sel][0] == "BACK"
    assert r.detail() == []


def test_the_detail_never_exceeds_five_lines():
    r, c = room()
    wid = open_list(r, c)
    w = c.worlds[wid]
    w.status, w.last_visit = "surveyed", 10
    w.seen = {"env": "toxic atmosphere", "life": "humanoid life signs", "features": "ruins, energy readings"}
    c.teams["SG-2"].status, c.teams["SG-2"].where = "offworld", wid
    w.drone, w.wreck = "malp", "crashed"
    r.e.probe(list(c.worlds)[0])                        # something else queued, for good measure
    c.worlds[wid].notes.append((0, "a note"))
    assert len(r.detail()) <= 5


def test_list_rows_name_a_team_on_site():
    r, c = room()
    wid = open_list(r, c)
    c.teams["SG-2"].status, c.teams["SG-2"].where = "staging", wid
    c.worlds[wid].drone = "malp"
    label, ok = r.items()[r.sel]
    assert "SG-2" in label and label.rstrip().endswith("MALP") and ok


def test_list_row_shows_wreck_when_no_drone():
    r, c = room()
    wid = open_list(r, c)
    c.worlds[wid].wreck = "shot_down"
    label, _ = r.items()[r.sel]
    assert label.rstrip().endswith("WRECK")


def _draw(room_obj, cols, rows):
    layout = compute_layout(cols, rows, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    screens.draw_room(cv, layout, room_obj)
    return cv.text()


def test_detail_lines_are_clipped_not_wrapped_to_the_panel_width():
    r, c = room()
    wid = open_list(r, c)
    c.worlds[wid].notes.append((0, "A very long note indeed, much longer than the narrow side panel allows"))
    text = _draw(r, 100, 30)
    assert "…" in text or "NOTE:" in text          # clipped somewhere, never silently dropped
    # the note's tail never reappears wrapped onto its own row
    assert "narrow side panel allows" not in text
