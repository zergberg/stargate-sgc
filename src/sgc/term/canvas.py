"""A grid of styled text cells that only re-sends what changed."""
from __future__ import annotations

from ..layout import Rect

Color = tuple[int, int, int]
_BLANK = (" ", None, None, False)


def to256(c: Color) -> int:
    r, g, b = (max(0, min(5, round(v / 255 * 5))) for v in c)
    return 16 + 36 * r + 6 * g + b


def sgr(fg: Color | None, bg: Color | None, bold: bool, truecolor: bool) -> bytes:
    parts = ["0"]
    if bold:
        parts.append("1")
    if fg is not None:
        parts.append("38;2;%d;%d;%d" % fg if truecolor else f"38;5;{to256(fg)}")
    if bg is not None:
        parts.append("48;2;%d;%d;%d" % bg if truecolor else f"48;5;{to256(bg)}")
    return ("\x1b[" + ";".join(parts) + "m").encode()


class Canvas:
    def __init__(self, cols: int, rows: int):
        self.cols, self.rows = cols, rows
        self._cells = [[_BLANK] * cols for _ in range(rows)]
        self._prev: list[list] | None = None
        self._holes: list[Rect] = []

    def set_holes(self, rects: list[Rect]) -> None:
        """Areas owned by images: never written by render()."""
        self._holes = [r for r in rects if r.w and r.h]

    def _in_hole(self, x: int, y: int) -> bool:
        return any(r.x <= x < r.x + r.w and r.y <= y < r.y + r.h for r in self._holes)

    def clear(self) -> None:
        self._cells = [[_BLANK] * self.cols for _ in range(self.rows)]

    def invalidate(self) -> None:
        self._prev = None

    def put(self, x: int, y: int, text: str, fg: Color | None, bg: Color | None = None, bold: bool = False) -> None:
        if not 0 <= y < self.rows:
            return
        row = self._cells[y]
        for i, ch in enumerate(text):
            if 0 <= x + i < self.cols:
                row[x + i] = (ch, fg, bg, bold)

    def fill(self, r: Rect, ch: str = " ", fg: Color | None = None, bg: Color | None = None) -> None:
        for y in range(r.y, r.y + r.h):
            self.put(r.x, y, ch * r.w, fg, bg)

    def box(self, r: Rect, title: str, color: Color, title_color: Color | None = None) -> None:
        if r.w < 2 or r.h < 2:
            return
        self.put(r.x, r.y, "┌" + "─" * (r.w - 2) + "┐", color)
        for y in range(r.y + 1, r.y + r.h - 1):
            self.put(r.x, y, "│", color)
            self.put(r.x + r.w - 1, y, "│", color)
        self.put(r.x, r.y + r.h - 1, "└" + "─" * (r.w - 2) + "┘", color)
        if title and r.w > 6:
            self.put(r.x + 2, r.y, f" {title[:r.w - 6]} ", title_color or color, bold=True)

    def text(self) -> str:
        return "\n".join("".join(c[0] for c in row) for row in self._cells)

    def render(self, truecolor: bool) -> bytes:
        out = bytearray()
        style = None
        for y, row in enumerate(self._cells):
            prev = self._prev[y] if self._prev else None
            x = 0
            while x < self.cols:
                if (prev is not None and prev[x] == row[x]) or self._in_hole(x, y):
                    x += 1
                    continue
                out += b"\x1b[%d;%dH" % (y + 1, x + 1)
                while x < self.cols and not ((prev is not None and prev[x] == row[x]) or self._in_hole(x, y)):
                    ch, fg, bg, bold = row[x]
                    if (fg, bg, bold) != style:
                        style = (fg, bg, bold)
                        out += sgr(fg, bg, bold, truecolor)
                    out += ch.encode()
                    x += 1
        self._prev = [list(r) for r in self._cells]
        if out:
            out += b"\x1b[0m"
        return bytes(out)
