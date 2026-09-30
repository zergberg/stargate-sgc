"""Draws the Stargate, its ramp and everything happening in front of it, as a Pillow image."""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from ..glyphs import glyph_char, load_glyph_font
from ..model import Feed, Figure, Scene
from .feed import screen as feed_screen

LIT = (255, 140, 30)
UNLIT = (110, 60, 30)
HOUSING = (58, 60, 66)
N_HORIZON_FRAMES = 16
UAV_LIFT = 0.55          # a uav at alt 1 flies this fraction of the image above its point on the ramp
FEED_MIN = 160           # gate images smaller than this skip the feed monitor (the side panel still reads out)
FEED_W, FEED_H = 0.42, 0.30        # the monitor's size, as fractions of the image
FEED_SCROLL = 48         # terrain rows that scroll past over a whole report; the frame key's quantum
_LABEL_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf")


def label_font(size: int) -> ImageFont.ImageFont:
    for path in _LABEL_FONTS:
        try:
            return ImageFont.truetype(path, max(1, size))
        except OSError:
            continue
    return ImageFont.load_default(size=max(1, size))


@lru_cache(maxsize=8)
def _feed_fonts(h: int) -> tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
    """The feed monitor's HUD font and its SIGNAL LOST font, for a screen h pixels high."""
    return label_font(max(6, h // 9)), label_font(max(7, h // 6))


@lru_cache(maxsize=1)
def _ripple_frames(lo: int = 64) -> tuple[Image.Image, ...]:
    """Low-resolution event-horizon ripple frames; scaled per renderer size."""
    frames = []
    for f in range(N_HORIZON_FRAMES):
        ph = f * math.tau / N_HORIZON_FRAMES
        px = []
        for y in range(lo):
            for x in range(lo):
                dx, dy = (x - lo / 2) / (lo / 2), (y - lo / 2) / (lo / 2)
                r = math.hypot(dx, dy)
                v = (0.52 + 0.26 * math.sin(r * 13 - ph) + 0.12 * math.sin(dx * 8 + ph) * math.sin(dy * 6 - ph)
                     + 0.08 * math.sin((dx + dy) * 11 - 2 * ph))
                v = max(0.0, min(1.0, v))
                px.append((int(20 + 150 * v * v * v), int(70 + 150 * v), int(160 + 95 * v)))
        im = Image.new("RGB", (lo, lo))
        im.putdata(px)
        frames.append(im)
    return tuple(frames)


def _cw(cx: float, cy: float, r: float, deg: float) -> tuple[float, float]:
    """Point at radius r, `deg` degrees clockwise from the top."""
    a = math.radians(deg)
    return cx + r * math.sin(a), cy - r * math.cos(a)


class GateRenderer:
    def __init__(self, size: int, font_path: Path | None):
        self.S = S = max(16, int(size))
        self.cx, self.cy = S * 0.5, S * 0.44
        self.R = S * 0.40
        self.ri = self.R * 0.72                 # event horizon radius
        self.track_out = self.R * 0.86          # outer edge of the rotating glyph track
        self.font_path = font_path
        self._bg = self._make_background()
        self.ramp_layer = self._make_ramp()
        self._outer = self._make_outer()
        self._inner = self._make_inner()
        self._inner_cache: tuple[float, Image.Image] | None = None
        self._frames = self._make_horizon_frames()
        d = int(2 * self.ri)
        self._hmask = Image.new("L", (d, d), 0)
        ImageDraw.Draw(self._hmask).ellipse([0, 0, d - 1, d - 1], fill=255)
        self._circle = Image.new("L", (S, S), 0)
        ImageDraw.Draw(self._circle).ellipse([self.cx - self.ri, self.cy - self.ri, self.cx + self.ri, self.cy + self.ri],
                                             fill=255)
        self._iris_cache: dict[int, Image.Image] = {}
        self._glow = self._make_glow()

    # ------------------------------------------------------------ static layers

    def _make_background(self) -> Image.Image:
        S, cx, cy, R, ri = self.S, self.cx, self.cy, self.R, self.ri
        im = Image.new("RGB", (S, S))
        d = ImageDraw.Draw(im)
        for y in range(S):                       # gate-room wall: dark blue-grey gradient
            k = y / S
            d.line([(0, y), (S, y)], fill=(int(14 + 10 * k), int(17 + 11 * k), int(24 + 12 * k)))
        step = max(4, S // 14)                   # faint wall panels
        for x in range(0, S, step):
            d.line([(x, 0), (x, cy + R)], fill=(22, 26, 34))
        floor = cy + R * 0.95
        d.rectangle([0, floor, S, S], fill=(30, 32, 38))
        return im

    def _make_ramp(self) -> Image.Image:
        """The ramp runs from the viewer up to the lower edge of the event horizon, in front of the
        ring's bottom arc; it stays narrower than the two bottom chevrons so they remain visible."""
        S, cx, cy, R, ri = self.S, self.cx, self.cy, self.R, self.ri
        top_y, bot_y = cy + ri * 0.99, S
        top_hw, bot_hw = R * 0.14, R * 0.4
        self._ramp = (top_y, bot_y, top_hw, bot_hw)
        im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.polygon([(cx - top_hw, top_y), (cx + top_hw, top_y), (cx + bot_hw, bot_y), (cx - bot_hw, bot_y)],
                  fill=(84, 88, 97, 255))
        for i in range(1, 14):                   # grating, closer together towards the gate
            k = (i / 14) ** 1.6
            y = bot_y + (top_y - bot_y) * (1 - k)
            hw = bot_hw + (top_hw - bot_hw) * (1 - k)
            d.line([(cx - hw, y), (cx + hw, y)], fill=(64, 67, 74, 255), width=max(1, S // 300))
        d.line([(cx - top_hw, top_y), (cx + top_hw, top_y)], fill=(52, 54, 60, 255), width=max(1, S // 200))
        rail_w = max(1, S // 170)
        for side in (-1, 1):                     # hand rails with posts
            d.line([(cx + side * top_hw, top_y), (cx + side * bot_hw, bot_y)], fill=(150, 155, 165, 255),
                   width=rail_w)
            for i in range(1, 5):
                k = i / 5
                x = cx + side * (top_hw + (bot_hw - top_hw) * k)
                y = top_y + (bot_y - top_y) * k
                d.line([(x, y), (x, y - S * 0.035 * (0.4 + 0.6 * k))], fill=(150, 155, 165, 255), width=rail_w)
        return im

    def _make_outer(self) -> Image.Image:
        S, cx, cy, R = self.S, self.cx, self.cy, self.R
        im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(96, 99, 107, 255))
        r2 = self.track_out
        d.ellipse([cx - r2, cy - r2, cx + r2, cy + r2], fill=(0, 0, 0, 0))
        bevel = R * 0.97                          # darker outer lip
        d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=(70, 72, 80, 255), width=max(1, int(R - bevel)))
        w = max(1, S // 260)
        for k in range(36):                       # segment seams
            p0, p1 = _cw(cx, cy, r2, k * 10), _cw(cx, cy, R, k * 10)
            d.line([p0, p1], fill=(72, 75, 82, 255), width=w)
        for k in range(9):                        # chevron housings
            a = k * 40
            pts = [_cw(cx, cy, R * 1.05, a - 5.5), _cw(cx, cy, R * 1.05, a + 5.5),
                   _cw(cx, cy, R * 0.84, a + 3.5), _cw(cx, cy, R * 0.84, a - 3.5)]
            d.polygon(pts, fill=HOUSING + (255,), outline=(40, 42, 46, 255))
        return im

    def _make_inner(self) -> Image.Image:
        S, cx, cy = self.S, self.cx, self.cy
        ro, ri = self.track_out, self.ri
        im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse([cx - ro, cy - ro, cx + ro, cy + ro], fill=(60, 63, 70, 255))
        d.ellipse([cx - ri, cy - ri, cx + ri, cy + ri], fill=(0, 0, 0, 0))
        band = ro - ri
        gsz = max(6, int(band * 0.78))
        font = load_glyph_font(self.font_path, gsz)
        numbers = label_font(max(5, int(band * 0.45)))
        rm = (ro + ri) / 2
        for g in range(1, 40):
            a = (g - 1) * 360 / 39
            tile = Image.new("RGBA", (gsz * 2, gsz * 2), (0, 0, 0, 0))
            td = ImageDraw.Draw(tile)
            if font:
                td.text((gsz, gsz), glyph_char(g), font=font, anchor="mm", fill=(205, 208, 215, 255))
            else:
                td.text((gsz, gsz), str(g), font=numbers, anchor="mm", fill=(205, 208, 215, 255))
            tile = tile.rotate(-a, resample=Image.BICUBIC)
            x, y = _cw(cx, cy, rm, a)
            im.alpha_composite(tile, (int(x - gsz), int(y - gsz)))
            p0, p1 = _cw(cx, cy, ri, a + 360 / 78), _cw(cx, cy, ro, a + 360 / 78)
            d.line([p0, p1], fill=(48, 50, 56, 255), width=max(1, S // 400))
        return im

    def _make_horizon_frames(self) -> list[Image.Image]:
        d = int(2 * self.ri)
        return [f.resize((d, d), Image.BICUBIC) for f in _ripple_frames()]

    def _make_glow(self) -> Image.Image:
        g = max(8, int(self.R * 0.22))
        im = Image.new("RGBA", (g * 2, g * 2), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse([g * 0.4, g * 0.4, g * 1.6, g * 1.6], fill=LIT + (140,))
        return im.filter(ImageFilter.GaussianBlur(g * 0.3))

    def _iris(self, level: float) -> Image.Image:
        key = int(round(level * 24))
        if key not in self._iris_cache:
            ri = self.ri
            d = int(2 * ri)
            im = Image.new("RGBA", (d, d), (0, 0, 0, 0))
            dr = ImageDraw.Draw(im)
            c = d / 2
            ap = ri * (1 - key / 24)
            dr.ellipse([0, 0, d - 1, d - 1], fill=(128, 132, 138, 255))
            if ap > 1:
                dr.ellipse([c - ap, c - ap, c + ap, c + ap], fill=(0, 0, 0, 0))
            w = max(1, d // 150)
            start = max(ap, ri * 0.06)
            for k in range(20):                   # spiral blade seams
                a0 = k * 18
                p0 = (c + start * math.sin(math.radians(a0)), c - start * math.cos(math.radians(a0)))
                p1 = (c + ri * math.sin(math.radians(a0 + 55)), c - ri * math.cos(math.radians(a0 + 55)))
                dr.line([p0, p1], fill=(78, 80, 86, 255), width=w)
            dr.ellipse([0, 0, d - 1, d - 1], outline=(70, 72, 78, 255), width=max(1, d // 60))
            self._iris_cache[key] = im
        return self._iris_cache[key]

    # ------------------------------------------------------------ geometry helpers

    def chevron_point(self, k: int) -> tuple[float, float]:
        return _cw(self.cx, self.cy, self.R * 0.95, k * 40)

    def figure_point(self, pos: float, lane: float) -> tuple[float, float, float]:
        top_y, bot_y, top_hw, bot_hw = self._ramp
        pos = max(0.0, min(1.0, pos))
        y = bot_y + (top_y - bot_y) * pos
        hw = bot_hw + (top_hw - bot_hw) * pos
        return self.cx + lane * hw * 0.8, y, 1 - 0.68 * pos

    # ------------------------------------------------------------ frame

    def frame_key(self, scene: Scene, t: float) -> tuple:
        """Everything that affects the picture; equal keys mean the last frame can be reused."""
        live = scene.horizon != "off"
        people = any(f.kind == "person" for f in scene.figures)
        return (
            round(scene.ring_angle, 2), frozenset(scene.lit), round(scene.clamp, 3), scene.horizon,
            round(scene.horizon_p, 3), round(scene.iris, 3),
            tuple((f.kind, round(f.pos, 3), f.lane, round(f.alpha, 2), round(f.alt, 3), f.facing)
                  for f in scene.figures),
            tuple(tuple(round(v, 3) for v in sp) for sp in scene.splashes),
            tuple(tuple(round(v, 3) for v in im) for im in scene.impacts),
            round(scene.vaporize, 3), scene.alert, round(scene.dim, 3), round(scene.collapse_line, 3),
            int(t * 10) if live else None,                                           # ripple frame
            round(math.sin(t * math.tau * 2), 1) if scene.alert == "red" else None,   # alarm pulse
            round(t * 12) if people or scene.horizon == "kawoosh" else None,         # strides, plume wobble
            tuple(tuple(round(v, 2) for v in m) for m in scene.muzzle),
            self._feed_key(scene.feed, t),
        )

    def render(self, scene: Scene, t: float) -> Image.Image:
        S, cx, cy, ri = self.S, self.cx, self.cy, self.ri
        im = self._bg.copy()
        hx, hy = int(cx - ri), int(cy - ri)
        frame = self._frames[int(t * 10) % N_HORIZON_FRAMES]
        if scene.horizon == "open" or (scene.horizon == "kawoosh" and scene.horizon_p > 0.55):
            im.paste(frame, (hx, hy), self._hmask)
            if scene.splashes:
                self._draw_splashes(im, scene.splashes)
        elif scene.horizon == "collapse":
            k = max(0.02, 1 - scene.horizon_p)
            d = max(2, int(2 * ri * k))
            small = frame.resize((d, d))
            mask = self._hmask.resize((d, d))
            im.paste(small, (int(cx - d / 2), int(cy - d / 2)), mask)
        if scene.iris > 0.01:
            im.paste(self._iris(scene.iris), (hx, hy), self._iris(scene.iris))
            if scene.impacts:
                self._draw_impacts(im, scene.impacts)
        rgba = im.convert("RGBA")
        rgba.alpha_composite(self._rotated_inner(scene.ring_angle))
        rgba.alpha_composite(self._outer)
        self._draw_chevrons(rgba, scene)
        rgba.alpha_composite(self.ramp_layer)
        if scene.horizon == "kawoosh":
            self._draw_kawoosh(rgba, scene.horizon_p, t)
        for f in sorted(scene.figures, key=lambda f: (f.kind != "rail", -f.pos)):   # a drone sits on its rail
            self._draw_figure(rgba, f, t)
        if scene.muzzle:
            self._draw_muzzle(rgba, scene.muzzle)
        if scene.vaporize > 0:
            self._draw_vaporize(rgba, scene.vaporize)
        if scene.feed is not None and S >= FEED_MIN:
            self._draw_feed(rgba, scene.feed, t)
        if scene.alert == "red":
            self._draw_alert(rgba, t)
        out = rgba.convert("RGB")
        if scene.dim > 0:
            out = ImageEnhance.Brightness(out).enhance(max(0.0, 1 - scene.dim))
        if scene.collapse_line > 0:
            out = self._crt_collapse(out, scene.collapse_line)
        return out

    def _rotated_inner(self, angle: float) -> Image.Image:
        if self._inner_cache is None or abs(self._inner_cache[0] - angle) > 1e-3:
            img = self._inner.rotate(-angle, resample=Image.BILINEAR, center=(self.cx, self.cy))
            self._inner_cache = (angle, img)
        return self._inner_cache[1]

    def _draw_chevrons(self, im: Image.Image, scene: Scene) -> None:
        cx, cy, R = self.cx, self.cy, self.R
        d = ImageDraw.Draw(im)
        for k in range(9):
            on = k in scene.lit
            off = scene.clamp * R * 0.05 if k == 0 else 0.0
            a = k * 40
            r_out, r_in = R * 1.03 - off, R * 0.86 - off
            pts = [_cw(cx, cy, r_out, a - 4.8), _cw(cx, cy, r_out, a + 4.8), _cw(cx, cy, r_in, a)]
            if on:
                gx, gy = _cw(cx, cy, R * 0.95 - off, a)
                g = self._glow
                im.alpha_composite(g, (int(gx - g.width / 2), int(gy - g.height / 2)))
            d = ImageDraw.Draw(im)
            d.polygon(pts, fill=(LIT if on else UNLIT) + (255,), outline=(35, 30, 25, 255))

    def _draw_splashes(self, im: Image.Image, splashes) -> None:
        cx, cy, ri = self.cx, self.cy, self.ri
        over = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        for x, y, age in splashes:
            if not 0 <= age < 1:
                continue
            px, py = cx + x * ri, cy + y * ri
            for ring in (0.0, 0.35):
                a = age - ring
                if a <= 0:
                    continue
                r = ri * (0.06 + 0.4 * a)
                d.ellipse([px - r, py - r * 0.9, px + r, py + r * 0.9], outline=(225, 242, 255, int(200 * (1 - a))),
                          width=max(1, int(ri / 30)))
        im.paste(over, (0, 0), ImageChops.multiply(over.getchannel("A"), self._circle))

    def _draw_impacts(self, im: Image.Image, impacts) -> None:
        d = ImageDraw.Draw(im)
        ri = self.ri
        for x, y, k in impacts:
            px, py = self.cx + x * ri * 0.8, self.cy + y * ri * 0.8
            for s, col in ((0.2, (255, 170, 60)), (0.11, (255, 235, 190))):
                r = ri * s * (0.5 + 0.5 * k)
                d.ellipse([px - r, py - r, px + r, py + r], fill=tuple(int(c * k + 128 * (1 - k)) for c in col))

    def _draw_kawoosh(self, im: Image.Image, p: float, t: float) -> None:
        cx, cy, ri = self.cx, self.cy, self.ri
        s = math.sin(math.pi * min(1.0, p / 0.8))
        if s <= 0.01:
            return
        over = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        base = ri * (0.25 + 1.35 * s)
        for i in range(9, 0, -1):
            k = i / 9
            r = base * k
            pts = []
            for j in range(48):
                a = j * math.tau / 48
                wob = 1 + 0.09 * math.sin(7 * a + t * 9 + i) + 0.05 * math.sin(13 * a - t * 7)
                pts.append((cx + r * wob * math.cos(a), cy + r * wob * math.sin(a)))
            col = (int(120 + 110 * (1 - k)), int(175 + 70 * (1 - k)), 255, int(40 + 160 * (1 - k) * s))
            d.polygon(pts, fill=col)
        im.alpha_composite(over)

    def _draw_figure(self, im: Image.Image, f: Figure, t: float) -> None:
        x, y, s = self.figure_point(f.pos, f.lane)
        S = self.S
        d = ImageDraw.Draw(im)
        a = int(255 * max(0.0, min(1.0, f.alpha)))
        if f.kind == "person":
            h = S * 0.2 * s
            stride = math.sin(t * 7 + f.lane * 5) * h * 0.07
            lw = max(1, int(h * 0.1))
            body, dark = (72, 80, 60, a), (44, 48, 38, a)
            d.line([(x - h * 0.06, y - h * 0.45), (x - h * 0.06 + stride, y)], fill=dark, width=lw)
            d.line([(x + h * 0.06, y - h * 0.45), (x + h * 0.06 - stride, y)], fill=dark, width=lw)
            d.rounded_rectangle([x - h * 0.16, y - h * 0.84, x + h * 0.16, y - h * 0.42], radius=h * 0.06, fill=body)
            d.rectangle([x - h * 0.11, y - h * 0.8, x + h * 0.11, y - h * 0.55], fill=(58, 54, 42, a))   # pack
            d.line([(x - h * 0.17, y - h * 0.8), (x - h * 0.2 - stride * 0.5, y - h * 0.5)], fill=body, width=lw)
            d.line([(x + h * 0.17, y - h * 0.8), (x + h * 0.2 + stride * 0.5, y - h * 0.5)], fill=body, width=lw)
            r = h * 0.085
            d.ellipse([x - r, y - h * 0.84 - 2 * r, x + r, y - h * 0.84], fill=(60, 64, 50, a))
        elif f.kind == "malp":                    # seen from behind, driving towards the gate
            w, h = S * 0.17 * s, S * 0.07 * s
            tw = w * 0.2
            for side in (-1, 1):
                for i in (2, 1, 0):                  # far wheels first: higher and slightly smaller
                    k = 1 - 0.1 * i
                    wx = x + side * (w / 2 + tw * 0.45)
                    top, bot = y - h * (1.25 + 0.3 * i), y - h * 0.3 * i
                    d.rounded_rectangle([wx - tw / 2 * k, top, wx + tw / 2 * k, bot], radius=tw * 0.3,
                                        fill=(30 + 8 * i, 30 + 8 * i, 32 + 8 * i, a))
            d.rectangle([x - w / 2, y - h * 1.9, x + w / 2, y - h * 0.55], fill=(150, 150, 140, a))
            d.rectangle([x - w * 0.36, y - h * 1.65, x + w * 0.36, y - h * 0.8], fill=(118, 118, 110, a))
            for side in (-1, 1):                     # tail lights
                d.rectangle([x + side * w * 0.42 - w * 0.04, y - h * 1.1, x + side * w * 0.42 + w * 0.04, y - h * 0.8],
                            fill=(200, 60, 40, a))
            d.line([(x, y - h * 1.9), (x, y - h * 3.3)], fill=(125, 125, 120, a), width=max(1, int(h * 0.18)))
            d.rectangle([x - w * 0.14, y - h * 3.75, x + w * 0.14, y - h * 3.2], fill=(90, 90, 88, a))
            d.ellipse([x - w * 0.05, y - h * 3.6, x + w * 0.05, y - h * 3.35], fill=(40, 60, 90, a))
        elif f.kind == "uav":
            self._draw_uav(im, x, y - f.alt * S * UAV_LIFT, s, a, f.facing)
        elif f.kind == "rail":
            self._draw_rail(d, x, y, s, a)
        else:  # crate
            w = S * 0.13 * s
            d.rectangle([x - w / 2, y - w * 0.8, x + w / 2, y], fill=(122, 96, 60, a), outline=(80, 62, 38, a),
                        width=max(1, int(w / 14)))
            d.line([(x - w / 2, y - w * 0.8), (x + w / 2, y)], fill=(90, 70, 44, a), width=max(1, int(w / 16)))
            d.line([(x + w / 2, y - w * 0.8), (x - w / 2, y)], fill=(90, 70, 44, a), width=max(1, int(w / 16)))

    def _draw_uav(self, im: Image.Image, x: float, y: float, s: float, a: int, facing: str) -> None:
        """A small straight-wing pusher drone: from behind, its propeller a blurred ring, or nose on coming home.
        Port light red, starboard green: so red is on our left from behind and on our right nose on."""
        w = self.S * 0.24 * 1.3 * s               # wingspan, scaled up for legibility
        th = max(2, int(w * 0.09))                # wing chord: a real surface, not a line
        bth = max(2, int(th * 0.72))               # boom/fin/tailplane thickness
        wy = y - w * 0.22                          # the wing, above its point on the ramp
        d = ImageDraw.Draw(im)
        lead, under = (222, 226, 230, a), (146, 150, 158, a)     # the wing: a lit leading edge, a shadowed underside
        boom, dark = (172, 176, 182, a), (44, 48, 54, a)
        by = wy + w * 0.14 if facing == "away" else wy - w * 0.07     # tail booms run toward the tailplane
        for side in (-1, 1):
            bx = x + side * w * 0.17
            d.line([(bx, wy), (bx, by)], fill=boom, width=bth)
            d.line([(bx, by), (bx, by - w * 0.12)], fill=boom, width=bth)        # the fins
        d.line([(x - w * 0.17, by), (x + w * 0.17, by)], fill=lead, width=bth)   # the tailplane
        d.rectangle([x - w / 2, wy - th, x + w / 2, wy], fill=lead)              # the wing, leading edge up
        d.rectangle([x - w / 2, wy, x + w / 2, wy + th], fill=under)             # ...its underside in shadow
        r = w * 0.1
        d.ellipse([x - r, wy - r, x + r, wy + r], fill=dark)                     # the fuselage pod, end on
        if facing == "away":
            rr = w * 0.16
            size = int(rr * 2) + 8
            x0, y0 = int(x - size / 2), int(wy - size / 2)
            if x0 >= 0 and y0 >= 0 and x0 + size <= im.width and y0 + size <= im.height:
                ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                ImageDraw.Draw(ring).ellipse([4, 4, size - 4, size - 4], outline=(210, 214, 220, int(a * 0.55)),
                                             width=max(1, int(rr * 0.35)))
                im.alpha_composite(ring.filter(ImageFilter.GaussianBlur(max(0.5, rr * 0.12))), (x0, y0))
            left, right = (230, 40, 30), (60, 230, 90)
        else:
            n = r * 0.55
            d.ellipse([x - n, wy - n, x + n, wy + n], fill=(20, 22, 26, a))      # the nose
            left, right = (60, 230, 90), (230, 40, 30)
        lr = max(2.0, w * 0.032)
        for side, col in ((-1, left), (1, right)):
            lx = x + side * w / 2
            d.ellipse([lx - lr, wy - lr, lx + lr, wy + lr], fill=col + (a,))

    def _draw_rail(self, d: ImageDraw.ImageDraw, x: float, y: float, s: float, a: int) -> None:
        """The launch rail: a short ramp on a trestle at the foot of the gate ramp, pointing up at the gate."""
        w = self.S * 0.16 * s
        top = y - w * 0.3
        for side in (-1, 1):                                   # trestle legs
            d.line([(x + side * w * 0.3, y), (x + side * w * 0.12, top)], fill=(112, 116, 124, a),
                   width=max(1, int(w * 0.04)))
        d.polygon([(x - w * 0.14, top + w * 0.06), (x + w * 0.14, top + w * 0.06),
                   (x + w * 0.07, top - w * 0.3), (x - w * 0.07, top - w * 0.3)], fill=(74, 78, 86, a))

    def _draw_muzzle(self, im: Image.Image, flashes) -> None:
        over = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        for lane, pos, k in flashes:
            x, y, s = self.figure_point(pos, lane)
            y -= self.S * 0.12 * s                                    # at chest height
            r = self.S * 0.018 * s * (0.6 + 0.8 * k)
            d.ellipse([x - r * 2.2, y - r * 2.2, x + r * 2.2, y + r * 2.2], fill=(255, 150, 40, int(90 * k)))
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 240, 200, int(255 * k)))
        im.alpha_composite(over)

    def _draw_vaporize(self, im: Image.Image, k: float) -> None:
        x, y, s = self.figure_point(0.93, 0.25)
        over = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        r = self.S * 0.12 * (1.4 - 0.4 * k)
        d.ellipse([x - r, y - r * 1.2, x + r, y + r * 0.4], fill=(235, 245, 255, int(230 * k)))
        for i in range(10):
            a = i * math.tau / 10
            rr = r * (1.2 + 0.8 * (1 - k))
            px, py = x + rr * math.cos(a), y - r * 0.4 + rr * 0.6 * math.sin(a)
            d.ellipse([px - 2, py - 2, px + 2, py + 2], fill=(255, 220, 160, int(255 * k)))
        im.alpha_composite(over)

    def _draw_alert(self, im: Image.Image, t: float) -> None:
        pulse = 0.5 + 0.5 * math.sin(t * math.tau * 2)
        over = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        R = self.R
        for i in range(6):
            r = R * (1.06 + 0.035 * i)
            d.ellipse([self.cx - r, self.cy - r, self.cx + r, self.cy + r], outline=(255, 30, 20, int(90 * pulse * (1 - i / 6))),
                      width=max(1, int(R * 0.03)))
        for bx in (self.S * 0.08, self.S * 0.92):   # alarm beacons
            r = self.S * 0.035
            d.ellipse([bx - r * 3, self.S * 0.06 - r * 3, bx + r * 3, self.S * 0.06 + r * 3], fill=(255, 20, 10, int(70 * pulse)))
            d.ellipse([bx - r, self.S * 0.06 - r, bx + r, self.S * 0.06 + r], fill=(255, 60, 40, int(120 + 135 * pulse)))
        im.alpha_composite(over)

    def _crt_collapse(self, im: Image.Image, k: float) -> Image.Image:
        S = self.S
        h = max(1, int(S * (1 - k)))
        out = Image.new("RGB", (S, S))
        out.paste(im.resize((S, h)), (0, (S - h) // 2))
        if k > 0.8:
            d = ImageDraw.Draw(out)
            w = S * (1 - (k - 0.8) / 0.2)
            d.line([(S / 2 - w / 2, S / 2), (S / 2 + w / 2, S / 2)], fill=(200, 220, 255), width=max(1, S // 200))
        return out

    # ------------------------------------------------------------ the UAV feed monitor

    def feed_rect(self) -> tuple[int, int, int, int]:
        """The feed monitor's box (x0, y0, x1, y1): bottom right, about 42% of the image wide and 30% high."""
        S = self.S
        m = max(2, S // 40)
        return S - m - int(S * FEED_W), S - m - int(S * FEED_H), S - m, S - m

    def _feed_key(self, feed: Feed | None, t: float) -> tuple | None:
        if feed is None or self.S < FEED_MIN:
            return None
        return (feed.seed, feed.tint, int(feed.p * FEED_SCROLL), round(feed.lost, 2), feed.hud, feed.contact,
                int(t * 4) if feed.lost >= 1 else None)             # SIGNAL LOST flashes at 2 Hz

    def _draw_feed(self, im: Image.Image, feed: Feed, t: float) -> None:
        """A control-room monitor inset bottom right, showing the UAV's camera."""
        x0, y0, x1, y1 = self.feed_rect()
        bezel = max(2, (x1 - x0) // 24)
        d = ImageDraw.Draw(im)
        d.rectangle([x0, y0, x1, y1], fill=(34, 36, 42, 255), outline=(96, 100, 110, 255), width=max(1, bezel // 2))
        w, h = x1 - x0 - 2 * bezel, y1 - y0 - 2 * bezel
        if w < 4 or h < 4:
            return
        im.paste(self._feed_screen(feed, t, w, h), (x0 + bezel, y0 + bezel))

    def _feed_screen(self, feed: Feed, t: float, w: int, h: int) -> Image.Image:
        """The monitor's picture: the UAV's camera and HUD, turning to static as the signal goes."""
        small, big = _feed_fonts(h)
        return feed_screen(feed, t, (w, h), int(feed.p * FEED_SCROLL), small, big)
