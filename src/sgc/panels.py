"""Text screens around the gate: header, destination, data, wormhole, teams and the log."""
from __future__ import annotations

from datetime import datetime

from .layout import Layout, Rect
from .model import Scene
from .term.canvas import Canvas, Color

AMBER: Color = (255, 170, 60)
DIM: Color = (150, 100, 40)
CYAN: Color = (100, 200, 255)
RED: Color = (255, 70, 55)
GREEN: Color = (100, 220, 130)
WHITE: Color = (215, 220, 228)
SPARK = "▁▂▃▄▅▆▇█"
BASE_PANELS = 4                  # the side screens: DESTINATION, DATA, WORMHOLE, OFFWORLD TEAMS
GAME_PANELS = 2                  # then the GATE QUEUE box, then SGC STATUS (or the briefing room's box/prompt)
TOTAL_PANELS = BASE_PANELS + GAME_PANELS      # how far scene.blank_panels counts up to, on quit


def _fade(c: Color, dim: float) -> Color:
    k = max(0.0, 1 - dim)
    return (int(c[0] * k), int(c[1] * k), int(c[2] * k))


def blank_box(canvas: Canvas, r: Rect, dim: float) -> None:
    """An empty box reading '-- NO SIGNAL --': how a blanked side screen or game box looks during shutdown.
    Shared so a blanked box always looks the same, wherever it's drawn."""
    color = _fade(DIM, max(dim, 0.5))
    canvas.box(r, "", color)
    canvas.put(r.x + 2, r.y + r.h // 2, "-- NO SIGNAL --"[:r.w - 4], color)


def _log_color(line: str) -> Color:
    if any(w in line for w in ("CODE RED", "IMPACT", "UNSCHEDULED", "NO-GO", "ABORT", "WILL NOT", "VAPORIZED")):
        return RED
    if any(w in line for w in ("ESTABLISHED", "IDC", "ARRIVING", "CLEARED", "COMPLETE")):
        return CYAN
    return AMBER


def draw_panels(canvas: Canvas, layout: Layout, scene: Scene, logs: list[str], now: datetime, t: float) -> None:
    canvas.clear()
    dim = scene.dim
    if layout.mode == "tiny":
        msg = "ENLARGE PANE"
        canvas.put(max(0, (layout.cols - len(msg)) // 2), layout.rows // 2, msg, _fade(AMBER, dim), bold=True)
        hint = "(min 40x12)"
        canvas.put(max(0, (layout.cols - len(hint)) // 2), layout.rows // 2 + 1, hint, _fade(DIM, dim))
        return
    if layout.mode == "compact":
        _status_line(canvas, layout.status, scene, logs, now, dim)
        return
    _header(canvas, layout.header, scene, now, t, dim)
    _side(canvas, layout.side, scene, t, dim)
    _log(canvas, layout.log, logs, dim)


def _header(canvas: Canvas, r: Rect, scene: Scene, now: datetime, t: float, dim: float) -> None:
    flash = int(t * 2) % 2 == 0
    if scene.alert == "red":
        bg, fg = ((120, 10, 5) if flash else (50, 5, 5)), (255, 220, 210)
    elif scene.alert == "incoming":
        bg, fg = ((90, 60, 5) if flash else (45, 30, 5)), (255, 230, 180)
    else:
        bg, fg = (38, 26, 8), AMBER
    bg, fg = _fade(bg, dim), _fade(fg, dim)
    defcon = {"red": 2, "incoming": 3}.get(scene.alert, 5)
    canvas.fill(r, " ", fg, bg)
    left = " SGC · STARGATE COMMAND · CHEYENNE MOUNTAIN"
    if scene.alert == "red":
        left = " !! CODE RED · UNSCHEDULED OFFWORLD ACTIVATION"
    right = f"{now:%H:%M:%S} · DEFCON {defcon} "
    canvas.put(r.x, r.y, left[:max(0, r.w - len(right) - 1)], fg, bg, bold=True)
    canvas.put(r.x + r.w - len(right), r.y, right, fg, bg, bold=True)


def _side(canvas: Canvas, r: Rect, scene: Scene, t: float, dim: float) -> None:
    boxes = [("DESTINATION", 6, _destination), ("DATA", 0, _data), ("WORMHOLE", 5, _wormhole),
             ("OFFWORLD TEAMS", 6, _teams)]
    fixed = sum(h for _, h, _ in boxes)
    while len(boxes) > 2 and fixed + 4 > r.h:          # drop boxes that don't fit
        boxes.pop()
        fixed = sum(h for _, h, _ in boxes)
    data_h = max(3, r.h - fixed)
    y = r.y
    for i, (title, h, fn) in enumerate(boxes):
        h = h or data_h
        h = min(h, r.y + r.h - y)
        if h < 3:
            break
        box = Rect(r.x, y, r.w, h)
        blank = i < scene.blank_panels
        color = _fade(RED if scene.alert == "red" else DIM, dim)
        if blank:
            blank_box(canvas, box, dim)
        else:
            title_text = scene.panel_title if title == "DATA" else title
            canvas.box(box, title_text, color, _fade(AMBER, dim))
            fn(canvas, Rect(box.x + 2, box.y + 1, box.w - 4, h - 2), scene, t, dim)
        y += h


def _destination(canvas: Canvas, r: Rect, scene: Scene, t: float, dim: float) -> None:
    a = scene.address
    if scene.incoming and not scene.identified:
        canvas.put(r.x, r.y, "UNKNOWN ORIGIN"[:r.w], _fade(RED, dim), bold=True)
        canvas.put(r.x, r.y + 1, "INCOMING WORMHOLE"[:r.w], _fade(AMBER, dim))
        canvas.put(r.x, r.y + 3, f"LOCK {scene.locked}/7"[:r.w], _fade(AMBER, dim))
        return
    if a is None:
        canvas.put(r.x, r.y, "NO DESTINATION"[:r.w], _fade(DIM, dim), bold=True)
        canvas.put(r.x, r.y + 1, scene.status[:r.w], _fade(DIM, dim))
        return
    canvas.put(r.x, r.y, a.label.upper()[:r.w], _fade(AMBER, dim), bold=True)
    if a.designation and a.designation != a.name:
        sub = a.designation
    elif a.canon and a.name != a.designation:       # a canon world stays unmarked until its name is known
        sub = "CANON"
    else:
        sub = "UNCHARTED"
    canvas.put(r.x, r.y + 1, sub.upper()[:r.w], _fade(DIM, dim))
    x = r.x
    for i, g in enumerate(a.full):
        if x + 2 > r.x + r.w:
            break
        canvas.put(x, r.y + 2, f"{g:02d}", _fade(AMBER if i < scene.locked else DIM, dim), bold=i < scene.locked)
        x += 3
    canvas.put(r.x, r.y + 3, f"LOCK {scene.locked}/{a.chevrons}  {scene.status}"[:r.w], _fade(CYAN, dim))


def _data(canvas: Canvas, r: Rect, scene: Scene, t: float, dim: float) -> None:
    rows = scene.panel_rows[:max(0, r.h - 1)]
    for i, (label, value) in enumerate(rows):
        canvas.put(r.x, r.y + i, label[:12], _fade(DIM, dim))
        canvas.put(r.x + 13, r.y + i, value[:max(0, r.w - 13)], _fade(WHITE, dim))
    if scene.panel_trace and r.h >= 1:
        vals = scene.panel_trace[-r.w:]
        line = "".join(SPARK[min(7, int(v * 8))] for v in vals)
        canvas.put(r.x, r.y + r.h - 1, line, _fade(CYAN, dim))
    elif not rows:
        canvas.put(r.x, r.y, "AWAITING DATA"[:r.w], _fade(DIM, dim))


def _wormhole(canvas: Canvas, r: Rect, scene: Scene, t: float, dim: float) -> None:
    state = {"off": "INACTIVE", "kawoosh": "ESTABLISHING", "open": "ACTIVE", "collapse": "DISENGAGING"}[scene.horizon]
    canvas.put(r.x, r.y, "STATE", _fade(DIM, dim))
    canvas.put(r.x + 8, r.y, state[:r.w - 8], _fade(CYAN if scene.horizon == "open" else AMBER, dim), bold=True)
    secs = int(scene.open_elapsed) if scene.horizon in ("open", "collapse") else 0
    canvas.put(r.x, r.y + 1, "TIME", _fade(DIM, dim))
    canvas.put(r.x + 8, r.y + 1, f"{secs // 60:02d}:{secs % 60:02d} / 38:00"[:r.w - 8], _fade(WHITE, dim))
    iris = "CLOSED" if scene.iris >= 0.99 else "OPEN" if scene.iris <= 0.01 else "MOVING"
    canvas.put(r.x, r.y + 2, "IRIS", _fade(DIM, dim))
    canvas.put(r.x + 8, r.y + 2, iris, _fade(RED if iris == "CLOSED" else GREEN if iris == "OPEN" else AMBER, dim),
               bold=True)


def _teams(canvas: Canvas, r: Rect, scene: Scene, t: float, dim: float) -> None:
    for i, (team, status) in enumerate(list(scene.teams.items())[:r.h]):
        home = status in ("AT BASE", "BASE")          # the ambient gate's words, and the campaign's
        canvas.put(r.x, r.y + i, team, _fade(AMBER, dim), bold=True)
        canvas.put(r.x + 6, r.y + i, "●" if not home else "○", _fade(GREEN if home else AMBER, dim))
        canvas.put(r.x + 8, r.y + i, status[:max(0, r.w - 8)], _fade(WHITE if home else AMBER, dim))


def _log(canvas: Canvas, r: Rect, logs: list[str], dim: float) -> None:
    canvas.box(r, "LOG", _fade(DIM, dim), _fade(AMBER, dim))
    lines = logs[-(r.h - 2):] if r.h > 2 else []
    for i, line in enumerate(lines):
        canvas.put(r.x + 2, r.y + 1 + i, line[:r.w - 4], _fade(_log_color(line), dim))


def _status_line(canvas: Canvas, r: Rect, scene: Scene, logs: list[str], now: datetime, dim: float) -> None:
    color = RED if scene.alert == "red" else AMBER
    where = scene.address.label.upper() if scene.address else ("UNKNOWN ORIGIN" if scene.incoming else "")
    last = logs[-1].split("  ", 1)[-1] if logs else scene.status
    text = f" {now:%H:%M:%S}  {where}  {last}"
    canvas.fill(r, " ", color)
    canvas.put(r.x, r.y, text[:r.w], _fade(color, dim), bold=True)

