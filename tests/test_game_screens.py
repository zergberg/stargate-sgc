import pytest

from sgc.game import screens
from sgc.game.menu import Menu
from sgc.game.state import new_campaign
from sgc.layout import Rect, compute_layout
from sgc.model import Prompt, Scene
from sgc.term.canvas import Canvas

RECORD = {"mode": "campaign", "difficulty": "officer", "result": "victory", "goauld_defeated": 3, "cycles": 40}

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
    for bit in ("STARGATE COMMAND", "1  AMBIENCE", "2  MISSIONS", "SAVE DAMAGED", "HALL OF RECORDS", "VICTORY"):
        assert bit in text, bit


def test_prompt_shows_wrapped_options_countdown_and_status():
    layout, cv = full()
    s = Scene()
    s.prompt = Prompt("DECISION", "Incoming wormhole.\nIDC received: SG-3.",
                      [("Open the iris", True), ("Keep it closed and revoke SG-3's code", False)], 12.0, 7.2)
    c = new_campaign("campaign", "officer", 1)
    c.inventory.add("ally.tokra")
    screens.draw_game(cv, layout, s, c, 0.0)
    text = cv.text()
    for bit in ("DECISION", "IDC received: SG-3.", "1  Open the iris", "2  Keep it closed", " 8s",
                "SGC STATUS", "SECURITY", "TOK'RA", c.lords[0].name.upper()[:10]):
        assert bit in text, bit


def test_no_prompt_still_shows_status():
    layout, cv = full()
    screens.draw_game(cv, layout, Scene(), new_campaign("endless", "recruit", 2), 0.0)
    assert "SGC STATUS" in cv.text() and "DECISION" not in cv.text()


def test_compact_prompt_uses_the_status_line():
    layout = compute_layout(60, 16, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    s = Scene()
    s.prompt = Prompt("DECISION", "x", [("Open", True), ("Close", True), ("Locked", False)], 12.0, 3.1)
    screens.draw_game(cv, layout, s, new_campaign("campaign", "officer", 1), 0.0)
    assert " 4s [1] OPEN [2] CLOSE" in cv.text()


@pytest.mark.parametrize("cols,rows", [(80, 22), (90, 24)])
def test_decision_fits_on_small_full_layouts(cols, rows):
    layout, cv = small(cols, rows)
    s = Scene()
    s.prompt = Prompt("DECISION", DECISION_TEXT, DECISION_OPTIONS, 15.0, 9.0)
    c = new_campaign("campaign", "officer", 1)
    screens.draw_game(cv, layout, s, c, 0.0)
    text = cv.text()
    assert "Incoming" in text
    for i, (label, _) in enumerate(DECISION_OPTIONS):
        assert f"{i + 1}  {label.split()[0]}" in text
    assert "SECURITY" in text


def test_question_keeps_at_least_some_text_at_90x24():
    layout, cv = small(90, 24)
    s = Scene()
    options = [
        ("Open the iris right now", True),
        ("Keep the iris closed for now", True),
        ("Send SG-3 a coded challenge", True),
        ("Scramble a MALP through first", True),
    ]
    s.prompt = Prompt("DECISION",
                       "Unscheduled off-world activation. IDC received: SG-3, but Colonel Reynolds "
                       "missed his last check-in.",
                       options, 15.0, 9.0)
    c = new_campaign("campaign", "officer", 1)
    screens.draw_game(cv, layout, s, c, 0.0)
    assert "IDC" in cv.text()


def test_aggression_never_truncates_the_number_at_narrow_width():
    layout = compute_layout(80, 22, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    c = new_campaign("campaign", "officer", 1)
    c.lords[0].aggression = 100
    screens.draw_status(cv, layout.side, c)
    assert "100" in cv.text()


def test_status_shows_more_marker_when_lords_dont_fit():
    layout = compute_layout(120, 36, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    c = new_campaign("campaign", "officer", 1)
    screens.draw_status(cv, Rect(layout.side.x, layout.side.y, layout.side.w, 9), c)
    text = cv.text()
    assert "MORE" in text


def test_countdown_bar_clamps_when_remaining_exceeds_total():
    layout, cv = full()
    s = Scene()
    s.prompt = Prompt("DECISION", "x", [("Open", True)], 10.0, 15.0)
    screens.draw_prompt(cv, layout.side, s.prompt, 0.0)
    r = layout.side
    border_col = r.x + r.w - 1
    row = cv.text().split("\n")[r.y + r.h - 2]
    assert row[border_col] == "│"


def test_unknown_flags_fall_back_to_a_short_label():
    layout, cv = full()
    c = new_campaign("campaign", "officer", 1)
    c.inventory.add("tech.mystery_widget")
    screens.draw_status(cv, layout.side, c)
    assert "MYSTERY_WIDGET" in cv.text()


def test_room_label_only_in_the_briefing_room():
    layout, cv = full()
    screens.draw_room_label(cv, layout, Scene(view_p=0.0))
    assert "BRIEFING ROOM" in cv.text()
    layout, cv = full()
    screens.draw_room_label(cv, layout, Scene())
    assert "BRIEFING ROOM" not in cv.text()
