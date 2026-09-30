"""The UAV feed monitor's picture: seeded terrain scrolling past, scan lines, the HUD, and static when it's lost."""
from __future__ import annotations

import math
import random
from functools import lru_cache
from typing import Callable

from PIL import Image, ImageChops, ImageDraw, ImageFont

from ..model import Feed

FontLoader = Callable[[int], ImageFont.ImageFont]
EDGE_MARGIN = 5           # pixels reserved on the right so text never touches the screen's edge

RES = (48, 34)                       # the terrain is computed at this size, then scaled to the monitor
TINTS = {                            # (low ground, high ground)
    "neutral": ((52, 60, 48), (120, 132, 104)),
    "desert": ((92, 70, 40), (200, 168, 110)),
    "ice": ((70, 84, 100), (210, 225, 238)),
    "forest": ((24, 52, 28), (96, 150, 80)),
    "toxic": ((50, 60, 16), (170, 190, 60)),
    "ocean": ((12, 34, 70), (70, 130, 170)),
    "volcanic": ((40, 20, 16), (190, 80, 30)),
}
HUD = (140, 255, 150)
REC = (230, 40, 30)
CONTACT = (255, 190, 60)
LOST = (255, 80, 60)


def _hash01(seed: int, x: int, y: int) -> float:
    """A repeatable 0..1 value for a lattice point."""
    h = (seed * 0x9E3779B1 + x * 0x85EBCA77 + y * 0xC2B2AE3D) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x2C1B3C6D) & 0xFFFFFFFF
    h ^= h >> 12
    h = (h * 0x297A2D39) & 0xFFFFFFFF
    h ^= h >> 15
    return h / 0xFFFFFFFF


def value_noise(seed: int, x: float, y: float) -> float:
    """Smooth 0..1 noise: the lattice values around (x, y), blended with smoothstep."""
    xi, yi = math.floor(x), math.floor(y)
    fx, fy = x - xi, y - yi
    sx, sy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b = _hash01(seed, xi, yi), _hash01(seed, xi + 1, yi)
    c, d = _hash01(seed, xi, yi + 1), _hash01(seed, xi + 1, yi + 1)
    top, bottom = a + (b - a) * sx, c + (d - c) * sx
    return top + (bottom - top) * sy


@lru_cache(maxsize=128)
def terrain(seed: int, scroll: int, tint: str) -> Image.Image:
    """The ground under the UAV at RES, `scroll` rows further on: features move down, toward the viewer."""
    lo, hi = TINTS.get(tint, TINTS["neutral"])
    w, h = RES
    px = []
    for y in range(h):
        gy = y - scroll
        for x in range(w):
            v = 0.65 * value_noise(seed, x / 9, gy / 9) + 0.35 * value_noise(seed + 1, x / 3.5, gy / 3.5)
            px.append(tuple(int(a + (b - a) * v) for a, b in zip(lo, hi)))
    im = Image.new("RGB", RES)
    im.putdata(px)
    return im


@lru_cache(maxsize=8)
def _scanlines(w: int, h: int) -> Image.Image:
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for y in range(0, h, 2):
        d.line([(0, y), (w, y)], fill=(185, 185, 185))
    return im


def _static(feed: Feed, t: float, w: int, h: int) -> Image.Image:
    tick = int(t * 4) if feed.lost >= 1 else 0
    rng = random.Random(f"{feed.seed}/{int(feed.lost * 20)}/{tick}")
    im = Image.new("L", RES)
    im.putdata([rng.randrange(256) for _ in range(RES[0] * RES[1])])
    return im.resize((w, h), Image.NEAREST).convert("RGB")


def _fit(d: ImageDraw.ImageDraw, text: str, max_w: float, size: int, min_size: int,
         font_loader: FontLoader) -> ImageFont.ImageFont:
    """The largest font no bigger than `size` (down to `min_size`) that draws `text` within `max_w`."""
    font = font_loader(size)
    while size > min_size and d.textlength(text, font=font) > max_w:
        size -= 1
        font = font_loader(size)
    return font


def _hud(d: ImageDraw.ImageDraw, feed: Feed, w: int, h: int, font_loader: FontLoader) -> None:
    cx, cy, c = w / 2, h / 2, max(3, h // 8)
    for a, b in (((cx - c, cy), (cx - c / 3, cy)), ((cx + c / 3, cy), (cx + c, cy)),
                 ((cx, cy - c), (cx, cy - c / 3)), ((cx, cy + c / 3), (cx, cy + c))):
        d.line([a, b], fill=HUD, width=1)                                    # the crosshair
    max_w = max(1, w - EDGE_MARGIN)
    size, min_size = max(6, h // 9), max(4, h // 16)
    font = _fit(d, "REC", max_w, size, min_size, font_loader)
    d.text((3, 2), "REC", font=font, fill=HUD)
    if int(feed.p * 12) % 2 == 0:                                            # the REC light blinks
        x, r = 3 + d.textlength("REC", font=font) + 3, max(1, h // 28)
        d.ellipse([x, 3, x + 2 * r, 3 + 2 * r], fill=REC)
    if feed.hud:
        text = feed.hud
        font = _fit(d, text, max_w, size, min_size, font_loader)
        if d.textlength(text, font=font) > max_w and text.startswith("UAV "):
            text = text[4:]                                                  # still too wide: drop the prefix
            font = _fit(d, text, max_w, size, min_size, font_loader)
        d.text((3, h - 2), text, font=font, fill=HUD, anchor="ld")
    if feed.contact and 0.3 <= feed.p <= 0.7:
        bx = w * (0.15 + 0.5 * _hash01(feed.seed, 7, 1))
        by = h * (0.2 + 0.35 * _hash01(feed.seed, 3, 9))
        s = max(4, h // 5)
        d.rectangle([bx, by, bx + s, by + s * 0.8], outline=CONTACT, width=1)


def screen(feed: Feed, t: float, size: tuple[int, int], scroll: int, font_loader: FontLoader) -> Image.Image:
    """The monitor's picture at `size`: terrain, scan lines, the HUD while live, and static as it's lost."""
    w, h = size
    img = terrain(feed.seed, scroll, feed.tint).resize((w, h), Image.BILINEAR)
    if feed.lost > 0:
        img = Image.blend(img, _static(feed, t, w, h), min(1.0, feed.lost))
    img = ImageChops.multiply(img, _scanlines(w, h))
    d = ImageDraw.Draw(img)
    if feed.lost < 0.5:
        _hud(d, feed, w, h, font_loader)
    if feed.lost >= 1 and int(t * 4) % 2 == 0:
        text = "SIGNAL LOST"
        max_w = max(1, w - EDGE_MARGIN)
        font = _fit(d, text, max_w, max(7, h // 6), max(6, h // 14), font_loader)
        d.text((w / 2, h / 2), text, font=font, fill=LOST, anchor="mm")
    return img
