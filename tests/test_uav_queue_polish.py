import random
import time

from PIL import ImageChops, ImageStat

from sgc.addresses import AddressPicker, load_canon
from sgc.config import Config
from sgc.director import Director
from sgc.events import REGISTRY
from sgc.game import screens
from sgc.game.clock import DAY
from sgc.game.database import Database
from sgc.game.schedule import QueueItem
from sgc.game.state import from_dict, to_dict
from sgc.layout import compute_layout
from sgc.model import Feed, Figure, Scene, Step
from sgc.render import feed as feed_mod
from sgc.render.gate import GateRenderer
from sgc.term.canvas import Canvas
from tests.test_game_app import queue_tab, raise_alarm, run_until, start
from tests.test_game_screens import WIDEST, db_campaign
from tests.test_game_state import busy_campaign
from tests.test_game_uav import step, uav_of
from tests.test_game_engine import Rig


# ---------------------------------------------------------------- the QUEUE reply and the clock

def test_a_long_reply_never_runs_into_the_clock_and_keeps_x_again_visible():
    layout = compute_layout(80, 22, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    c = db_campaign()[0]
    c.minutes = 12 * DAY + 4 * 60 + 14
    db = Database(c, lambda: WIDEST)
    db.tab = "queue"
    db.message = "CANCEL THE SG-1 SEARCH FOR SG-3 ON THE LAND OF THE LIGHT (P3X-774)?  x AGAIN TO CONFIRM"
    screens.draw_database(cv, layout, db, "bar")
    line = cv.text().split("\n")[1]
    assert line.rstrip().endswith("DAY 13 · 04:14 SGC")
    assert "x AGAIN TO CONFIRM DAY" not in line and "CONFIRMDAY" not in line
    assert "x AGAIN TO CONFIRM" in line and "…" in line
    clock = line.index("DAY 13")
    assert line[clock - 2:clock] == "  "                   # a clear gap before the clock


def test_a_short_reply_is_drawn_whole_beside_the_clock():
    layout = compute_layout(80, 22, 9, 18)
    cv = Canvas(layout.cols, layout.rows)
    c = db_campaign()[0]
    c.minutes = 12 * DAY
    db = Database(c, lambda: WIDEST)
    db.tab = "queue"
    db.message = "CANCELLED: UAV TO P3X-774"
    screens.draw_database(cv, layout, db, "bar")
    line = cv.text().split("\n")[1]
    assert db.message in line and "…" not in line and "DAY 13" in line


# ---------------------------------------------------------------- no matches

def test_a_search_with_no_matches_says_so():
    for cols, rows in ((80, 22), (60, 16)):
        layout = compute_layout(cols, rows, 9, 18)
        cv = Canvas(layout.cols, layout.rows)
        db = Database(db_campaign()[0], lambda: WIDEST)
        db.tab = "queue"
        db.query = "zzzz"
        screens.draw_database(cv, layout, db, "bar")
        assert "NO MATCHES" in cv.text() and "NOTHING SCHEDULED" not in cv.text()
        empty = Database(db_campaign()[0], lambda: [])
        empty.tab = "queue"
        screens.draw_database(cv, layout, empty, "bar")
        assert "NOTHING SCHEDULED" in cv.text() and "NO MATCHES" not in cv.text()


def test_x_with_no_matches_says_no_matches():
    db = Database(db_campaign()[0], lambda: WIDEST)
    db.tab, db.query = "queue", "zzzz"
    db.key("x")
    assert db.message == "NO MATCHES"


# ---------------------------------------------------------------- Esc clears a kept search

def test_escape_clears_a_search_kept_with_enter():
    db = Database(db_campaign()[0], lambda: WIDEST)
    db.tab = "queue"
    for k in ("/", "ch:s", "ch:g", "enter"):
        db.key(k)
    assert not db.searching and db.query == "sg"
    assert db.key("escape") is None
    assert db.query == "" and db.tab == "queue" and len(db.rows()) == len(WIDEST)


# ---------------------------------------------------------------- the drone in flight

def test_a_drone_in_flight_cannot_be_recalled_from_the_queue():
    from sgc.game import schedule
    from sgc.game.engine import TRAVEL
    r = Rig()
    w = r.world(5)
    r.c.events.push(r.c.now + 600, "malp_return", {"world": w.id, "drone": "malp", "sent": r.c.now})
    item = next(i for i in schedule.view(r.c, TRAVEL) if i.kind == "drone")
    assert item.reason == "ALREADY THROUGH THE GATE: WAIT FOR ITS REPORT"


# ---------------------------------------------------------------- the save

def test_a_drone_reports_sent_time_must_be_a_whole_number():
    import pytest
    c = busy_campaign()[0]
    d = to_dict(c)
    wid = d["worlds"][0]["id"]
    d["events"].append({"due": 2000, "seq": 999, "kind": "malp_return",
                        "data": {"world": wid, "drone": "uav", "sent": "noon"}})
    with pytest.raises(ValueError):
        from_dict(d)
    d["events"][-1]["data"]["sent"] = 1500
    from_dict(d)


# ---------------------------------------------------------------- the app

def test_d_with_an_alarm_open_stays_in_the_gate_room(tmp_path):
    app = start(tmp_path)
    run_until(app, 15, lambda a: a.director.idle)
    raise_alarm(app)
    app._handle_keys(["d", "d", "d"])
    assert app.view == "gate" and app.engine.prompt is not None and app.db is None
    assert sum("ALARM OPEN — GIVE AN ORDER FIRST" in line for line in app.logs) == 1


def test_reopening_the_database_clears_a_stale_confirm_prompt(tmp_path):
    app = start(tmp_path)
    c = app.engine.c
    app.engine.probe(list(c.worlds.values())[1].id)
    queue_tab(app)
    app._handle_keys(["x"])
    assert app.db.message.startswith("CANCEL THE MALP")
    app._back_to_the_gate_room()
    app._handle_keys(["d"])
    assert app.db.armed is None and app.db.message == ""


# ---------------------------------------------------------------- the director

def test_a_step_that_ends_on_a_frame_boundary_gets_exactly_p_1():
    d = Director(Config(), random.Random(1), AddressPicker(load_canon(), 0.6, random.Random(1)), REGISTRY)
    d.auto = False
    ps = []
    d.run_steps([Step(7 / 30, lambda s, p: ps.append(p))])
    for _ in range(7):
        d.advance(1 / 30)
    assert ps[-1] == 1.0 and d.idle
    assert ps.count(1.0) == 1


# ---------------------------------------------------------------- figures, alpha and the roll-out

def test_alpha_fades_a_figure():
    g = GateRenderer(300, None)
    solid = g.render(Scene(figures=[Figure("malp", 0.2)]), 0.0)
    faint = g.render(Scene(figures=[Figure("malp", 0.2, alpha=0.02)]), 0.0)
    empty = g.render(Scene(), 0.0)
    assert ImageChops.difference(solid, faint).getbbox() is not None
    diff = ImageStat.Stat(ImageChops.difference(faint, empty).convert("L")).extrema[0][1]
    assert diff <= 12                                      # 2% of a MALP barely shows


def test_figures_stay_cheap_to_draw():
    g = GateRenderer(420, None)
    figs = [Figure("person", 0.1 * i, lane=0.3 * (i % 3 - 1)) for i in range(4)] + [Figure("rail", 0.12),
                                                                                   Figure("uav", 0.12)]
    g.render(Scene(figures=figs), 0.0)
    t0 = time.perf_counter()
    for i in range(10):
        g.render(Scene(figures=figs), i * 0.1)
    with_figs = time.perf_counter() - t0
    t0 = time.perf_counter()
    for i in range(10):
        g.render(Scene(), i * 0.1)
    bare = time.perf_counter() - t0
    assert with_figs - bare < 0.25                         # well under 25 ms a frame for six figures


def test_the_landed_uav_rolls_off_the_bottom_of_the_frame():
    r = Rig(director=True)
    w = r.world(5, env="normal")
    steps = r.e._v_drone(w, "uav", home=True)
    s = r.d.scene
    out = step(steps, "UAV RECOVERED")
    out.update(s, 0.5)
    mid = uav_of(s)
    assert mid.alpha == 1.0 and mid.pos < 0.05
    out.update(s, 0.99)
    assert uav_of(s).pos < 0
    for size in (300, 420):
        g = GateRenderer(size, None)
        _, y, _ = g.figure_point(uav_of(s).pos, 0.0)
        assert y > g.S                                     # below the bottom edge by the end...
        gone = g.render(Scene(figures=list(s.figures)), 0.0)
        assert ImageChops.difference(gone, g.render(Scene(), 0.0)).getbbox() is None   # ...and wholly out of shot
    out.update(s, 1.0)
    assert s.figures == []


def test_a_figure_below_the_ramp_is_drawn_lower():
    g = GateRenderer(300, None)
    assert g.figure_point(-0.2, 0.0)[1] > g.figure_point(0.0, 0.0)[1]


# ---------------------------------------------------------------- the MALP at small sizes

def test_the_malp_wheels_draw_at_every_size_even_on_old_pillow(monkeypatch):
    from PIL import ImageDraw
    orig = ImageDraw.ImageDraw.rounded_rectangle

    def strict(self, xy, radius=0, *a, **kw):              # Pillow 10.2 raises when 2 * radius > the box
        x0, y0, x1, y1 = xy                                # ...and on fractional corners near that size
        if radius and (x1 - x0 < 2 * radius + 2 or y1 - y0 < 2 * radius + 2
                       or any(v != int(v) for v in (x0, y0, x1, y1, radius))):
            raise ValueError("y1 must be greater than or equal to y0")
        return orig(self, xy, radius, *a, **kw)
    monkeypatch.setattr(ImageDraw.ImageDraw, "rounded_rectangle", strict)
    for size in (16, 32, 64, 100, 159, 300):
        g = GateRenderer(size, None)
        for pos in (0.0, 0.5, 1.0):
            g.render(Scene(figures=[Figure("malp", pos), Figure("person", pos)]), 0.3)


# ---------------------------------------------------------------- the feed's looks

def test_the_ocean_is_teal_not_the_event_horizons_blue():
    lo, hi = feed_mod.TINTS["ocean"]
    for col in (lo, hi):
        r, g, b = col
        assert g >= b * 0.85 and g > r                     # green-blue, not the horizon's blue


def test_every_tint_has_contrast_enough_to_scroll():
    for name in feed_mod.TINTS:
        im = feed_mod.terrain(5, 0, name).convert("L")
        lo, hi = ImageStat.Stat(im).extrema[0]
        assert hi - lo >= 70, name


def test_signal_lost_sits_on_a_dark_band():
    g = GateRenderer(420, None)
    im = g.render(Scene(feed=Feed(seed=5, lost=1.0)), 0.0).crop(g.feed_rect())
    w, h = im.size
    band = im.crop((2, int(h * 0.42), w - 2, int(h * 0.58))).convert("L")
    rows_off = im.crop((2, int(h * 0.15), w - 2, int(h * 0.3))).convert("L")
    assert ImageStat.Stat(band).mean[0] < 0.6 * ImageStat.Stat(rows_off).mean[0]


def test_the_rail_stands_out_from_the_ramp():
    g = GateRenderer(420, None)
    base = g.render(Scene(), 0.0)
    img = g.render(Scene(figures=[Figure("rail", 0.12)]), 0.0)
    x, y, s = g.figure_point(0.12, 0.0)
    box = (int(x - g.S * 0.03), int(y - g.S * 0.1), int(x + g.S * 0.03), int(y - g.S * 0.02))
    a = ImageStat.Stat(base.crop(box).convert("L")).mean[0]
    b = ImageStat.Stat(img.crop(box).convert("L")).mean[0]
    assert abs(b - a) > 25
