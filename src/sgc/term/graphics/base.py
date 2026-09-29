"""Shared pieces for image backends."""
from __future__ import annotations

from ..detect import Caps
from ...layout import Rect

MAX_PX = 720


class Backend:
    name = "base"

    def __init__(self, caps: Caps):
        self.caps = caps
        self.scale = 1.0

    def pixel_size(self, rect: Rect) -> tuple[int, int]:
        """Image size to render for `rect`: its pixel size, capped, times the adaptive scale."""
        pw, ph = rect.w * self.caps.cell_w, rect.h * self.caps.cell_h
        k = min(1.0, MAX_PX / max(pw, ph, 1)) * self.scale
        return max(1, round(pw * k)), max(1, round(ph * k))

    def forget(self) -> bytes:
        return b""

    def cleanup(self) -> bytes:
        return b""


def move(rect: Rect) -> bytes:
    return b"\x1b[%d;%dH" % (rect.y + 1, rect.x + 1)
