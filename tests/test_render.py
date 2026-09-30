import math

from sgc.addresses import load_canon
from sgc.glyphs import find_font
from sgc.model import Figure, Scene
from sgc.render.addressbar import AddressBarRenderer
from sgc.render.gate import GateRenderer


def px(im, x, y):
    return im.getpixel((int(x), int(y)))


def test_sizes_and_idle():
    g = GateRenderer(300, find_font(None))
    im = g.render(Scene(), 0.0)
    assert im.size == (300, 300) and im.mode == "RGB"


def test_open_horizon_is_blue_at_center():
    g = GateRenderer(300, None)
    r, gg, b = px(g.render(Scene(horizon="open"), 0.3), g.cx, g.cy)
    assert b > 120 and b > r


def test_idle_center_is_dark():
    g = GateRenderer(300, None)
    r, gg, b = px(g.render(Scene(), 0.3), g.cx, g.cy)
    assert max(r, gg, b) < 80


def test_iris_closed_is_grey_at_center():
    g = GateRenderer(300, None)
    r, gg, b = px(g.render(Scene(horizon="open", iris=1.0), 0.3), g.cx, g.cy)
    assert abs(r - b) < 40 and r > 50


def test_lit_chevron_is_orange():
    g = GateRenderer(300, None)
    x, y = g.chevron_point(1)
    r, gg, b = px(g.render(Scene(lit={1}), 0.0), x, y)
    assert r > 200 and b < 120
    r, gg, b = px(g.render(Scene(), 0.0), x, y)
    assert r < 200


def test_ring_rotation_changes_image():
    g = GateRenderer(200, None)
    assert g.render(Scene(ring_angle=0), 0).tobytes() != g.render(Scene(ring_angle=40), 0).tobytes()


def test_dim_darkens():
    g = GateRenderer(200, None)
    bright = sum(g.render(Scene(horizon="open"), 0).convert("L").tobytes())
    dim = sum(g.render(Scene(horizon="open", dim=0.9), 0).convert("L").tobytes())
    assert dim < bright * 0.3


def test_figures_and_all_states_render():
    g = GateRenderer(240, None)
    for s in (Scene(horizon="kawoosh", horizon_p=0.4), Scene(horizon="collapse", horizon_p=0.5),
              Scene(horizon="open", figures=[Figure("person", 0.5), Figure("malp", 0.2), Figure("crate", 0.9)],
                    splashes=[[0.1, 0.7, 0.3]]),
              Scene(alert="red", impacts=[[0.2, 0.1, 1.0]], iris=1.0, horizon="open"),
              Scene(vaporize=1.0), Scene(dim=0.8, collapse_line=0.5)):
        assert g.render(s, 1.0).size == (240, 240)


def test_tiny_sizes_do_not_crash():
    for size in (16, 40, 64):
        assert GateRenderer(size, find_font(None)).render(Scene(horizon="open", iris=0.5), 0.2).size == (size, size)


def test_addressbar_locked_and_unknown():
    a = AddressBarRenderer(420, 60, find_font(None))
    s = Scene(address=load_canon()[1], locked=3)
    assert a.render(s, 0.0).size == (420, 60)
    assert a.render(Scene(incoming=True, identified=False, locked=4), 0.0).size == (420, 60)
    assert AddressBarRenderer(420, 60, None).render(s, 0.0).size == (420, 60)
    assert AddressBarRenderer(30, 8, None).render(s, 0.0).size == (30, 8)


def test_addressbar_shows_more_as_glyphs_lock():
    a = AddressBarRenderer(420, 60, find_font(None))
    addr = load_canon()[1]
    lit = lambda k: sum(a.render(Scene(address=addr, locked=k), 0.0).convert("L").tobytes())
    assert lit(0) < lit(3) < lit(7)


def test_ramp_is_in_front_of_the_gate():
    g = GateRenderer(300, None)
    x, y = g.cx, g.cy + g.R * 0.93          # bottom of the ring, behind the top of the ramp
    ramp = g.ramp_layer.getpixel((int(x), int(y)))
    assert ramp[3] == 255
    assert px(g.render(Scene(), 0.0), x, y) == ramp[:3]


def test_ramp_meets_the_horizon_and_leaves_bottom_chevrons_visible():
    g = GateRenderer(300, None)
    assert g.ramp_layer.getpixel((int(g.cx), int(g.cy + g.ri * 0.9)))[3] == 0      # horizon stays clear
    assert g.ramp_layer.getpixel((int(g.cx), int(g.cy + g.ri * 1.05)))[3] == 255   # ramp starts at its lower edge
    im = g.render(Scene(lit={4, 5}), 0.0)
    for k in (4, 5):
        for radius in (0.95, 0.89):                 # body and inner tip of each bottom chevron
            a = math.radians(k * 40)
            x, y = g.cx + g.R * radius * math.sin(a), g.cy - g.R * radius * math.cos(a)
            r, gg, b = px(im, x, y)
            assert r > 200 and b < 120, (k, radius)
        for dx in (-0.05, 0.05):                    # towards both corners of the chevron base
            a = math.radians(k * 40) + dx
            x, y = g.cx + g.R * 0.98 * math.sin(a), g.cy - g.R * 0.98 * math.cos(a)
            r, gg, b = px(im, x, y)
            assert r > 200 and b < 120, (k, dx)


def _figure_mask(kind):
    g = GateRenderer(300, None)
    base = g.render(Scene(), 0.0).convert("L")
    with_fig = g.render(Scene(figures=[Figure(kind, 0.15, 0.0)]), 0.0).convert("L")
    diff = [abs(a - b) > 8 for a, b in zip(base.tobytes(), with_fig.tobytes())]
    pts = [(i % 300, i // 300) for i, d in enumerate(diff) if d]
    return g, pts


def test_malp_is_seen_from_behind():
    g, pts = _figure_mask("malp")
    x, _, _ = g.figure_point(0.15, 0.0)
    left = sum(1 for px_, _ in pts if px_ < x - 1)
    right = sum(1 for px_, _ in pts if px_ > x + 1)
    assert pts and abs(left - right) <= 0.1 * max(left, right)     # symmetric rear view, not a side profile


def test_muzzle_flashes_draw_and_change_the_frame_key():
    from PIL import ImageChops
    from sgc.model import Scene
    from sgc.render.gate import GateRenderer
    g = GateRenderer(160, None)
    calm, hot = Scene(), Scene()
    hot.muzzle = [[0.0, 0.5, 1.0], [0.4, 0.3, 0.6]]
    assert g.frame_key(calm, 0.0) != g.frame_key(hot, 0.0)
    assert ImageChops.difference(g.render(calm, 0.0), g.render(hot, 0.0)).getbbox() is not None


def test_muzzle_flash_intensity_dims_the_glow():
    from sgc.model import Scene
    from sgc.render.gate import GateRenderer
    g = GateRenderer(160, None)
    x, y, s = g.figure_point(0.5, 0.0)
    y -= g.S * 0.12 * s
    px, py = int(x), int(y)

    def channel_sum(k):
        scene = Scene()
        scene.muzzle = [[0.0, 0.5, k]]
        im = g.render(scene, 0.0).convert("RGB")
        return sum(im.getpixel((px, py)))

    assert channel_sum(0.2) < channel_sum(1.0)
