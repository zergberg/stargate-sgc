"""Half-block fallback: two image pixels per cell with the upper-half-block character."""
from __future__ import annotations

from PIL import Image

from ...layout import Rect
from ..canvas import to256
from .base import Backend

UPPER = "▀".encode()


class BlocksBackend(Backend):
    name = "blocks"

    def __init__(self, caps):
        super().__init__(caps)
        self._prev: dict[int, tuple[Rect, list]] = {}

    def pixel_size(self, rect: Rect) -> tuple[int, int]:
        return rect.w, rect.h * 2

    def _color(self, fg: bool, c) -> bytes:
        base = 38 if fg else 48
        if self.caps.truecolor:
            return b"%d;2;%d;%d;%d" % (base, c[0], c[1], c[2])
        return b"%d;5;%d" % (base, to256(c))

    def show(self, slot: int, image: Image.Image, rect: Rect) -> bytes:
        im = image.convert("RGB")
        if im.size != (rect.w, rect.h * 2):
            im = im.resize((rect.w, rect.h * 2), Image.BILINEAR)
        px = im.load()
        cells = [(px[x, 2 * y], px[x, 2 * y + 1]) for y in range(rect.h) for x in range(rect.w)]
        prev = self._prev.get(slot)
        old = prev[1] if prev and prev[0] == rect else None
        out = bytearray()
        style = None
        for y in range(rect.h):
            x = 0
            while x < rect.w:
                i = y * rect.w + x
                if old is not None and old[i] == cells[i]:
                    x += 1
                    continue
                out += b"\x1b[%d;%dH" % (rect.y + y + 1, rect.x + x + 1)
                while x < rect.w and (old is None or old[y * rect.w + x] != cells[y * rect.w + x]):
                    top, bot = cells[y * rect.w + x]
                    if (top, bot) != style:
                        style = (top, bot)
                        out += b"\x1b[" + self._color(True, top) + b";" + self._color(False, bot) + b"m"
                    out += UPPER
                    x += 1
        self._prev[slot] = (rect, cells)
        if out:
            out += b"\x1b[0m"
        return bytes(out)

    def forget(self) -> bytes:
        self._prev.clear()
        return b""
