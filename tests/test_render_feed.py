from PIL import ImageChops, ImageStat

from sgc.model import Feed, Scene
from sgc.render.feed import HUD, LOST, REC, RES, terrain, value_noise
from sgc.render.gate import GateRenderer


def monitor(g, t=0.0, **kw):
    return g.render(Scene(feed=Feed(**{"seed": 5, **kw})), t).crop(g.feed_rect())


def inner_screen(g, t=0.0, **kw):
    """The monitor's picture alone, without its bezel: what `render.feed.screen` actually draws."""
    im = g.render(Scene(feed=Feed(**{"seed": 5, **kw})), t)
    x0, y0, x1, y1 = g.feed_rect()
    bezel = max(2, (x1 - x0) // 24)
    return im.crop((x0 + bezel, y0 + bezel, x1 - bezel, y1 - bezel))


def differ(a, b):
    return ImageChops.difference(a, b).getbbox() is not None


def has_color(im, color):
    return any(im.getpixel((x, y)) == color for x in range(im.width) for y in range(im.height))


def test_value_noise_is_deterministic_and_between_0_and_1():
    vals = [value_noise(7, x / 3, y / 5) for x in range(-20, 20) for y in range(-20, 20)]
    assert all(0.0 <= v <= 1.0 for v in vals) and len({round(v, 3) for v in vals}) > 50
    assert vals == [value_noise(7, x / 3, y / 5) for x in range(-20, 20) for y in range(-20, 20)]
    assert value_noise(7, 1.5, 2.5) != value_noise(8, 1.5, 2.5)


def test_the_same_world_always_looks_the_same():
    a, b = GateRenderer(300, None), GateRenderer(300, None)
    assert monitor(a).tobytes() == monitor(b).tobytes()
    assert differ(monitor(a), monitor(a, seed=6))
    assert terrain(5, 0, "neutral").size == RES


def test_the_tint_colours_the_ground():
    g = GateRenderer(400, None)
    r, _, b = ImageStat.Stat(monitor(g, tint="ocean")).mean
    assert b > r
    r, _, b = ImageStat.Stat(monitor(g, tint="desert")).mean
    assert r > b
    assert differ(monitor(g, tint="neutral"), monitor(g, tint="forest"))


def test_the_terrain_scrolls_past_and_the_frame_key_follows():
    g = GateRenderer(300, None)
    assert differ(monitor(g, p=0.0), monitor(g, p=0.5))
    key = lambda p: g.frame_key(Scene(feed=Feed(seed=5, p=p)), 0.0)
    assert key(0.0) != key(0.5) and key(0.0) == key(0.001)


def test_losing_the_signal_turns_the_picture_to_static_and_flashes_signal_lost():
    g = GateRenderer(300, None)
    live, gone = monitor(g), monitor(g, lost=1.0)
    assert differ(live, gone)
    sat = lambda im: ImageStat.Stat(im.convert("HSV")).mean[1]
    assert sat(gone) < sat(live)                                   # grey static
    assert differ(monitor(g, 0.0, lost=1.0), monitor(g, 0.3, lost=1.0))     # SIGNAL LOST on, then off


def test_a_contact_gets_a_box_mid_report():
    g = GateRenderer(300, None)
    assert differ(monitor(g, p=0.5, contact=True), monitor(g, p=0.5))
    assert monitor(g, p=0.1, contact=True).tobytes() == monitor(g, p=0.1).tobytes()


def test_the_hud_line_is_drawn():
    g = GateRenderer(300, None)
    assert differ(monitor(g, hud="UAV  ALT 1240M  HDG 047"), monitor(g, hud=""))


def test_the_hud_line_fits_the_monitor_at_the_smallest_and_a_large_size():
    long_hud = "UAV  ALT 1500M  HDG 090"
    for g in (GateRenderer(160, None), GateRenderer(1600, None)):
        im = inner_screen(g, hud=long_hud)
        edge = im.crop((im.width - 2, 0, im.width, im.height))
        assert not has_color(edge, HUD)


def test_the_rec_light_and_signal_lost_fit_the_smallest_monitor():
    g = GateRenderer(160, None)
    live = inner_screen(g, hud="UAV  ALT 1240M  HDG 047")
    assert not has_color(live.crop((live.width - 2, 0, live.width, live.height)), REC)
    gone = inner_screen(g, 0.0, lost=1.0)
    assert not has_color(gone.crop((gone.width - 2, 0, gone.width, gone.height)), LOST)
