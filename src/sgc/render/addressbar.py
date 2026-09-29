"""The big address readout under the gate: one slot per chevron, filled as glyphs lock."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from ..glyphs import GLYPH_NAMES, glyph_char, load_glyph_font
from ..model import Scene
from .gate import label_font

AMBER = (255, 160, 40)
DIM = (95, 58, 22)
BG = (12, 12, 16)


class AddressBarRenderer:
    def __init__(self, width: int, height: int, font_path: Path | None):
        self.W, self.H = max(8, int(width)), max(4, int(height))
        self.font_path = font_path
        self._fonts: dict[int, tuple] = {}

    def _fonts_for(self, slot_w: int):
        if slot_w not in self._fonts:
            size = int(min(self.H * (0.52 if self.H >= 44 else 0.72), slot_w * 0.8))
            glyph = load_glyph_font(self.font_path, size)
            self._fonts[slot_w] = (glyph, label_font(max(6, int(size * 0.8))), label_font(max(5, int(self.H * 0.16))))
        return self._fonts[slot_w]

    def render(self, scene: Scene, t: float) -> Image.Image:
        W, H = self.W, self.H
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        unknown = scene.incoming and not scene.identified
        addr = None if unknown else scene.address
        slots = addr.chevrons if addr else 7
        slot_w = W // slots
        glyph_font, number_font, small = self._fonts_for(slot_w)
        show_labels = H >= 44
        glyph_h = H * (0.74 if show_labels else 1.0)
        pad = max(1, slot_w // 14)
        for i in range(slots):
            x0 = i * slot_w + pad
            x1 = (i + 1) * slot_w - pad
            locked = i < scene.locked
            current = i == scene.locked and scene.spinning
            border = AMBER if locked else DIM
            if current:
                k = 0.5 + 0.5 * math.sin(t * 8)
                border = tuple(int(a * k + b * (1 - k)) for a, b in zip(AMBER, DIM))
            d.rectangle([x0, pad, x1, int(glyph_h) - pad - 1], outline=border, width=max(1, H // 30))
            if not locked:
                continue
            cx, cy = (x0 + x1) / 2, glyph_h / 2
            if unknown or addr is None:
                _centered(d, cx, cy, "?", number_font, AMBER)
                continue
            g = addr.full[i]
            if glyph_font:
                _centered(d, cx, cy, glyph_char(g), glyph_font, AMBER)
            else:
                _centered(d, cx, cy, str(g), number_font, AMBER)
            if show_labels:
                label = f"{g:02d}" if slot_w < H * 1.6 else f"{g:02d} {GLYPH_NAMES[g - 1][:10].upper()}"
                _centered(d, cx, (glyph_h + H) / 2, label, small, (200, 140, 60))
        if unknown:
            font = label_font(max(6, int(H * 0.3)))
            x0, y0, x1, y1 = d.textbbox((W / 2, H / 2), "UNKNOWN ORIGIN", font=font, anchor="mm")
            m = max(2, H // 10)
            d.rectangle([x0 - m, y0 - m, x1 + m, y1 + m], fill=BG, outline=(255, 90, 60))
            d.text((W / 2, H / 2), "UNKNOWN ORIGIN", font=font, anchor="mm", fill=(255, 90, 60))
        return im


def _centered(d: ImageDraw.ImageDraw, cx: float, cy: float, text: str, font, fill) -> None:
    """Draw text centred on its actual ink box (glyph fonts have unusual metrics)."""
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=font)
    d.text((cx - (x0 + x1) / 2, cy - (y0 + y1) / 2), text, font=font, fill=fill)
