from PIL import ImageChops

from sgc.model import Figure, Scene
from sgc.render.gate import UAV_LIFT, GateRenderer


def changed(g, figures, t=0.0):
    """Pixels the figures change, against an empty gate."""
    base = g.render(Scene(), t)
    img = g.render(Scene(figures=figures), t)
    a, b = base.load(), img.load()
    return [(x, y, b[x, y]) for y in range(g.S) for x in range(g.S)
            if sum(abs(u - v) for u, v in zip(a[x, y], b[x, y])) > 24]


def image(g, figures):
    return g.render(Scene(figures=figures), 0.0)


def test_the_uav_and_the_rail_are_not_a_malp_or_a_crate():
    g = GateRenderer(300, None)
    uav, rail = image(g, [Figure("uav", 0.3)]), image(g, [Figure("rail", 0.3)])
    for other in ("malp", "crate"):
        assert ImageChops.difference(uav, image(g, [Figure(other, 0.3)])).getbbox() is not None
        assert ImageChops.difference(rail, image(g, [Figure(other, 0.3)])).getbbox() is not None
    assert changed(g, [Figure("uav", 0.3)]) and changed(g, [Figure("rail", 0.3)])


def test_altitude_lifts_the_uav_off_the_ramp():
    g = GateRenderer(300, None)
    low = min(y for _, y, _ in changed(g, [Figure("uav", 0.4)]))
    high = min(y for _, y, _ in changed(g, [Figure("uav", 0.4, alt=0.8)]))
    assert low - high > 0.8 * UAV_LIFT * g.S * 0.8


def nav_lights(g, facing):
    pts = changed(g, [Figure("uav", 0.05, alt=0.3, facing=facing)])
    red = [x for x, _, (r, gg, b) in pts if r > 190 and gg < 90 and b < 90]
    green = [x for x, _, (r, gg, b) in pts if gg > 170 and r < 120]
    assert red and green
    return sum(red) / len(red), sum(green) / len(green)


def test_nav_lights_red_on_the_left_wing_and_green_on_the_right():
    g = GateRenderer(400, None)
    x, _, _ = g.figure_point(0.05, 0.0)
    red, green = nav_lights(g, "away")                   # from behind: its left wing is on our left
    assert red < x < green
    red, green = nav_lights(g, "toward")                 # nose on: its left wing is on our right
    assert green < x < red


def test_coming_home_is_drawn_nose_on():
    g = GateRenderer(300, None)
    away = image(g, [Figure("uav", 0.3, alt=0.2)])
    toward = image(g, [Figure("uav", 0.3, alt=0.2, facing="toward")])
    assert ImageChops.difference(away, toward).getbbox() is not None


def test_the_rail_is_drawn_under_the_drone():
    g = GateRenderer(300, None)
    a = image(g, [Figure("uav", 0.12), Figure("rail", 0.12)])
    b = image(g, [Figure("rail", 0.12), Figure("uav", 0.12)])
    assert a.tobytes() == b.tobytes()
    assert ImageChops.difference(a, image(g, [Figure("uav", 0.12)])).getbbox() is not None


def test_uav_frames_render_at_every_size():
    for size in (16, 64, 159, 240):
        g = GateRenderer(size, None)
        for f in (Figure("uav", 0.95, alt=0.8), Figure("uav", 0.0, alpha=0.3, facing="toward"), Figure("rail", 0.12)):
            assert g.render(Scene(horizon="open", figures=[f]), 0.5).size == (g.S, g.S)
