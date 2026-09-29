"""Splits the pane into header, gate, address bar, side screens and log."""
from __future__ import annotations

from dataclasses import dataclass, field

TINY = (40, 12)
COMPACT = (80, 22)


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


EMPTY = Rect(0, 0, 0, 0)


@dataclass(frozen=True)
class Layout:
    mode: str
    cols: int
    rows: int
    header: Rect = EMPTY
    gate: Rect = EMPTY
    bar: Rect = EMPTY
    side: Rect = EMPTY
    log: Rect = EMPTY
    status: Rect = EMPTY

    def rects(self) -> list[Rect]:
        return [self.header, self.gate, self.bar, self.side, self.log, self.status]


def _square(max_w: int, max_h: int, cw: float, ch: float) -> tuple[int, int]:
    """Largest cell box that is square in pixels and fits in max_w x max_h cells."""
    h = max(1, max_h)
    w = max(1, round(h * ch / cw))
    if w > max_w:
        w = max(1, max_w)
        h = max(1, min(max_h, round(w * cw / ch)))
    return w, h


def compute_layout(cols: int, rows: int, cw: float, ch: float) -> Layout:
    if cols < TINY[0] or rows < TINY[1]:
        return Layout("tiny", cols, rows, status=Rect(0, rows // 2, cols, 1))
    if cols < COMPACT[0] or rows < COMPACT[1]:
        gw, gh = _square(cols, rows - 4, cw, ch)
        gate = Rect((cols - gw) // 2, 0, gw, gh)
        bw = min(cols, max(gw, 36))
        bar = Rect((cols - bw) // 2, gh, bw, 3)
        return Layout("compact", cols, rows, gate=gate, bar=bar, status=Rect(0, rows - 1, cols, 1))
    log_h = 5
    side_w = min(40, max(28, int(cols * 0.32)))
    left_w = cols - side_w - 2
    avail_h = rows - 1 - log_h - 3
    gw, gh = _square(left_w, avail_h, cw, ch)
    gate = Rect(1 + (left_w - gw) // 2, 1, gw, gh)
    return Layout(
        "full", cols, rows,
        header=Rect(0, 0, cols, 1),
        gate=gate,
        bar=Rect(1, 1 + gh, left_w, 3),
        side=Rect(cols - side_w, 1, side_w, rows - 1 - log_h),
        log=Rect(0, rows - log_h, cols, log_h),
    )
