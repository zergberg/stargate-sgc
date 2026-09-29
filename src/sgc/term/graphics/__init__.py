"""Image backends: every backend turns a Pillow image into bytes that paint a cell rectangle."""
from __future__ import annotations

from ..detect import Caps
from .blocks import BlocksBackend
from .iterm import ItermBackend
from .kitty import KittyBackend
from .sixel import SixelBackend


def make_backend(caps: Caps):
    return {"kitty": KittyBackend, "sixel": SixelBackend, "iterm": ItermBackend}.get(caps.graphics, BlocksBackend)(caps)
