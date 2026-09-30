from datetime import datetime

from sgc.addresses import load_canon
from sgc.layout import compute_layout
from sgc.model import Scene
from sgc.panels import draw_panels
from sgc.term.canvas import Canvas


def test_canvas_diff_only_changes():
    c = Canvas(10, 2)
    c.put(0, 0, "AB", (255, 0, 0))
    first = c.render(True)
    assert b"AB" in first and c.render(True) == b""
    c.put(1, 0, "C", (255, 0, 0))
    out = c.render(True)
    assert b"C" in out and b"A" not in out


def test_canvas_clips_and_256_colors():
    c = Canvas(4, 1)
    c.put(2, 0, "XYZW", (255, 0, 0))
    c.put(-1, 3, "Q", (0, 0, 0))
    out = c.render(False)
    assert b"XY" in out and b"Z" not in out and b"38;5;" in out


def test_panels_draw_all_modes():
    for cols, rows in ((100, 27), (70, 20), (160, 50), (30, 10)):
        L = compute_layout(cols, rows, 9, 18)
        c = Canvas(cols, rows)
        s = Scene(alert="red", address=load_canon()[1], locked=3, panel_rows=[("GRAVITY", "1.02 G")],
                  panel_trace=[0.1, 0.5, 0.9])
        draw_panels(c, L, s, ["12:00:00  WORMHOLE ESTABLISHED"], datetime(2026, 9, 29, 12), 0.5)
        assert c.render(True)


def test_panels_show_address_and_log():
    L = compute_layout(100, 27, 9, 18)
    c = Canvas(100, 27)
    s = Scene(address=load_canon()[1], locked=2)
    draw_panels(c, L, s, ["12:00:01  CHEVRON 2 ENCODED"], datetime(2026, 9, 29, 12), 0.0)
    text = c.text()
    assert s.address.label.upper() in text and "CHEVRON 2 ENCODED" in text and "12:00:00" in text


def test_blank_panels_hide_side_screens():
    L = compute_layout(100, 27, 9, 18)
    c = Canvas(100, 27)
    s = Scene(address=load_canon()[1], blank_panels=4)
    draw_panels(c, L, s, [], datetime(2026, 9, 29, 12), 0.0)
    assert s.address.label.upper() not in c.text()


def test_an_unnamed_canon_world_is_not_marked_canon():
    from sgc.game.world import canon_world
    L = compute_layout(100, 27, 9, 18)
    c = Canvas(100, 27)
    w = canon_world("Chulak")
    assert w.canon and w.name == w.id
    draw_panels(c, L, Scene(address=w.address(), locked=2), [], datetime(2026, 9, 29, 12), 0.0)
    assert "CANON" not in c.text() and w.id in c.text()
    w.names.append(("Chulak", "the Jaffa", 1))
    draw_panels(c, L, Scene(address=w.address(), locked=2), [], datetime(2026, 9, 29, 12), 0.0)
    assert "CHULAK" in c.text() and w.id in c.text()


def test_a_campaign_team_at_base_is_marked_home_like_an_ambient_one():
    L = compute_layout(100, 27, 9, 18)
    c = Canvas(100, 27)
    s = Scene()
    s.teams = {"SG-1": "BASE", "SG-2": "AT BASE", "SG-3": "CAPTURED", "SG-4": "AWAY: ABYDOS"}
    draw_panels(c, L, s, [], datetime(2026, 9, 29, 12), 0.0)
    rows = {line.split("SG-")[1][:1]: line for line in c.text().splitlines() if "SG-" in line}
    assert "○" in rows["1"] and "○" in rows["2"] and "●" in rows["3"] and "●" in rows["4"]
