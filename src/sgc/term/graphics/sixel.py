"""Sixel graphics (foot, WezTerm, Windows Terminal >= 1.22, xterm -ti vt340, mlterm, tmux >= 3.4)."""
from __future__ import annotations

import re
from functools import lru_cache

from PIL import Image

from ...layout import Rect
from .base import Backend, move

_RUN = re.compile(rb"(.)\1{3,}")
_PLUS63 = bytes(min(255, i + 63) for i in range(256))


@lru_cache(maxsize=4096)
def _bit_table(color: int, bit: int) -> bytes:
    return bytes((1 << bit) if i == color else 0 for i in range(256))


def _rle(s: bytes) -> bytes:
    return _RUN.sub(lambda m: b"!%d%c" % (len(m.group(0)), m.group(1)[0]), s)


def encode(image: Image.Image, colors: int = 128) -> bytes:
    im = image.convert("RGB")
    w, h = im.size
    q = im.quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
    data = q.tobytes()
    used = sorted(set(data))
    pal = q.getpalette()
    out = [b'\x1bP0;1;0q"1;1;%d;%d' % (w, h)]
    for c in used:
        r, g, b = pal[3 * c:3 * c + 3]
        out.append(b"#%d;2;%d;%d;%d" % (c, round(r * 100 / 255), round(g * 100 / 255), round(b * 100 / 255)))
    for top in range(0, h, 6):
        rows = [data[y * w:(y + 1) * w] for y in range(top, min(h, top + 6))]
        band = []
        for c in sorted(set(b"".join(rows))):
            acc = 0
            for k, row in enumerate(rows):
                acc |= int.from_bytes(row.translate(_bit_table(c, k)), "big")
            band.append(b"#%d" % c + _rle(acc.to_bytes(w, "big").translate(_PLUS63)))
        out.append(b"$".join(band) + (b"-" if top + 6 < h else b""))
    out.append(b"\x1b\\")
    return b"".join(out)


class SixelBackend(Backend):
    name = "sixel"

    def pixel_size(self, rect: Rect) -> tuple[int, int]:
        w = max(1, int(rect.w * self.caps.cell_w * self.scale))
        h = max(6, int(rect.h * self.caps.cell_h * self.scale) // 6 * 6)
        return w, h

    def show(self, slot: int, image: Image.Image, rect: Rect) -> bytes:
        cols = image.width / self.caps.cell_w
        dx = max(0, int((rect.w - cols) / 2))
        return move(Rect(rect.x + dx, rect.y, rect.w, rect.h)) + encode(image)
