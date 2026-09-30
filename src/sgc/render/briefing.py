"""The briefing room above the gate room: the gate seen through the big window, and the walk down."""
from __future__ import annotations

from PIL import Image, ImageDraw

WALL = (24, 26, 32)
WALL_LINE = (32, 35, 42)
FRAME = (78, 80, 88)
FRAME_DARK = (44, 46, 52)
TABLE = (62, 42, 26)
TABLE_EDGE = (92, 64, 40)
CHAIR = (18, 18, 22)
GLASS = (90, 140, 190, 22)
GATE_CY = 0.44          # GateRenderer puts the gate's centre at 44% of the image height


def _smooth(p: float) -> float:
    return p * p * (3 - 2 * p)


class BriefingRenderer:
    def __init__(self, size: int):
        self.S = S = max(16, int(size))
        self.window = (round(S * 0.12), round(S * 0.07), round(S * 0.88), round(S * 0.58))
        l, t, r, b = self.window
        cx, wh = (l + r) // 2, b - t
        self.zoom_box = (cx - wh // 2, t, cx - wh // 2 + wh, b)     # square target of the walk-down zoom
        self._back = Image.new("RGB", (S, S), WALL)
        d = ImageDraw.Draw(self._back)
        for x in range(0, S, max(2, S // 24)):                     # wall panelling
            d.line([(x, 0), (x, S)], fill=WALL_LINE, width=1)
        self._front = self._make_front()

    def _make_front(self) -> Image.Image:
        S = self.S
        l, t, r, b = self.window
        im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rectangle([l, t, r, b], fill=GLASS)
        fw = max(2, S // 60)
        d.rectangle([l - fw, t - fw, r + fw, b + fw], outline=FRAME, width=fw)
        for k in (1, 2):                                            # mullions
            x = l + (r - l) * k // 3
            d.rectangle([x - fw // 2, t, x + fw // 2, b], fill=FRAME_DARK)
        y = t + (b - t) * 2 // 3
        d.rectangle([l, y - fw // 2, r, y + fw // 2], fill=FRAME_DARK)
        cw, ch = S * 0.09, S * 0.12                                 # chair backs along the far side
        for k in range(6):
            x = S * (0.16 + 0.136 * k)
            d.rounded_rectangle([x - cw / 2, S * 0.66, x + cw / 2, S * 0.66 + ch], radius=max(1, S // 80),
                                fill=CHAIR)
        d.polygon([(S * 0.02, S), (S * 0.14, S * 0.74), (S * 0.86, S * 0.74), (S * 0.98, S)], fill=TABLE)
        d.line([(S * 0.14, S * 0.74), (S * 0.86, S * 0.74)], fill=TABLE_EDGE, width=max(1, S // 90))
        return im

    def render(self, gate: Image.Image) -> Image.Image:
        """The room, with `gate` (a GateRenderer frame) showing through the window."""
        S = self.S
        l, t, r, b = self.window
        ww, wh = r - l, b - t
        g = gate if gate.size == (S, S) else gate.resize((S, S))
        crop_h = S * wh / ww                                        # crop the gate to the window's shape
        top = max(0.0, min(S - crop_h, S * GATE_CY - crop_h / 2))
        view = g.crop((0, round(top), S, round(top + crop_h))).resize((ww, wh))
        im = self._back.copy()
        im.paste(view, (l, t))
        rgba = im.convert("RGBA")
        rgba.alpha_composite(self._front)
        return rgba.convert("RGB")


def render_transition(room: Image.Image, gate: Image.Image, p: float,
                      zoom_box: tuple[int, int, int, int]) -> Image.Image:
    """The walk down: p=0 is the briefing room, p=1 the gate room. Zooms toward the window, then cross-fades."""
    p = max(0.0, min(1.0, p))
    S = room.size[0]
    e = _smooth(p)
    l, t, r, b = zoom_box
    box = (round(l * e), round(t * e), round(S + (r - S) * e), round(S + (b - S) * e))
    frame = room.crop(box).resize(room.size)
    if p > 0.7:
        g = gate if gate.size == room.size else gate.resize(room.size)
        frame = Image.blend(frame, g, min(1.0, (p - 0.7) / 0.3))
    return frame
