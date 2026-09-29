"""iTerm2 inline images (OSC 1337)."""
from __future__ import annotations

import base64
import io

from PIL import Image

from ...layout import Rect
from .base import Backend, move


class ItermBackend(Backend):
    name = "iterm"

    def show(self, slot: int, image: Image.Image, rect: Rect) -> bytes:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG", compress_level=1)
        data = buf.getvalue()
        head = f"\x1b]1337;File=inline=1;width={rect.w};height={rect.h};preserveAspectRatio=0;size={len(data)}:"
        return b"\x1b7" + move(rect) + head.encode() + base64.standard_b64encode(data) + b"\x07\x1b8"
