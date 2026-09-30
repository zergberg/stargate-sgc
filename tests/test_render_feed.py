from PIL import ImageChops, ImageStat

from sgc.model import Feed, Scene
from sgc.render.feed import RES, terrain, value_noise
from sgc.render.gate import GateRenderer


def monitor(g, t=0.0, **kw):
    return g.render(Scene(feed=Feed(**{"seed": 5, **kw})), t).crop(g.feed_rect())


def differ(a, b):
    return ImageChops.difference(a, b).getbbox() is not None


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
