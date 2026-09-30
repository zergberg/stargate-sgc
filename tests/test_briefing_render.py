from PIL import ImageChops, ImageStat

from sgc.model import Scene
from sgc.render.briefing import BriefingRenderer, render_transition
from sgc.render.gate import GateRenderer


def gate_img(size, horizon="off"):
    s = Scene()
    s.horizon = horizon
    return GateRenderer(size, None).render(s, 0.0)


def same(a, b):
    return ImageChops.difference(a, b).getbbox() is None


def test_room_renders_at_several_sizes():
    for size in (64, 120, 300):
        room = BriefingRenderer(size).render(gate_img(size))
        assert room.size == (size, size) and room.mode == "RGB"


def test_the_live_gate_shows_through_the_window():
    b = BriefingRenderer(160)
    off, on = b.render(gate_img(160)), b.render(gate_img(160, "open"))
    box = ImageChops.difference(off, on).getbbox()
    l, t, r, bot = b.window
    assert box is not None and l <= box[0] and box[2] <= r and t <= box[1] and box[3] <= bot


def test_transition_endpoints_are_the_two_rooms():
    b = BriefingRenderer(160)
    g = gate_img(160)
    room = b.render(g)
    assert same(render_transition(room, g, 0.0, b.zoom_box), room)
    assert same(render_transition(room, g, 1.0, b.zoom_box), g)


def test_transition_changes_smoothly():
    b = BriefingRenderer(160)
    g = gate_img(160)
    room = b.render(g)
    frames = [render_transition(room, g, i / 40, b.zoom_box) for i in range(41)]
    steps = [sum(ImageStat.Stat(ImageChops.difference(a, c)).mean) / 3 for a, c in zip(frames, frames[1:])]
    assert max(steps) < 25
