"""Game screens drawn over the side panel: the start menu, decisions, the campaign status."""
from __future__ import annotations

import math
import textwrap

from ..layout import Layout, Rect
from ..model import Prompt, Scene
from ..panels import AMBER, CYAN, DIM, GREEN, RED, WHITE
from ..term.canvas import Canvas
from .menu import Menu
from .state import Campaign

HILITE = (70, 48, 12)
HEADER_BG = (38, 26, 8)
METER_W = 10
FLAG_SHORT = {"ally.tokra": "TOK'RA", "ally.asgard": "ASGARD", "ally.tollan": "TOLLAN", "ally.nox": "NOX",
              "ally.jaffa": "JAFFA", "tech.zat": "ZAT", "tech.naquadah_generator": "NAQ-GEN", "tech.lrs": "LRS"}


def _wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        lines += textwrap.wrap(para, max(1, width)) or [""]
    return lines


def draw_room_label(canvas: Canvas, layout: Layout, scene: Scene) -> None:
    if layout.mode == "full" and scene.view_p < 0.5:
        canvas.put(0, 0, " SGC · BRIEFING ROOM · LEVEL 27".ljust(44), AMBER, HEADER_BG, bold=True)


def draw_menu(canvas: Canvas, layout: Layout, menu: Menu, records: list[dict]) -> None:
    items = menu.items()
    if layout.mode == "compact":
        text = " " + "  ".join(f"{i + 1} {it}" for i, it in enumerate(items))
        canvas.fill(layout.status, " ", AMBER)
        canvas.put(layout.status.x, layout.status.y, text[:layout.status.w], AMBER, bold=True)
        return
    if layout.mode != "full":
        return
    r = layout.side
    canvas.fill(r, " ")
    canvas.box(r, menu.title, DIM, AMBER)
    x, y, w = r.x + 2, r.y + 2, r.w - 4
    bottom = r.y + r.h - 1
    for i, it in enumerate(items):
        sel = i == menu.sel
        canvas.put(x, y, f" {i + 1}  {it}".ljust(w)[:w], WHITE if sel else AMBER, HILITE if sel else None, bold=sel)
        y += 1
    y += 1
    for line in _wrap(menu.notice, w) if menu.notice else []:
        canvas.put(x, y, line, RED, bold=True)
        y += 1
    canvas.put(x, y + 1, "↑↓ ENTER or 1-9 · Q BACK"[:w], DIM)
    y += 3
    if records and y < bottom - 1:
        canvas.put(x, y, "HALL OF RECORDS", AMBER, bold=True)
        y += 1
        for rec in records:
            if y >= bottom:
                break
            line = (f"{rec.get('result', '').upper():<7} {rec['goauld_defeated']}L {rec['cycles']}C "
                    f"{rec['mode'][:4].upper()} {rec['difficulty'][:3].upper()}")
            canvas.put(x, y, line[:w], DIM)
            y += 1


def _prompt_height(prompt: Prompt, panel_w: int) -> int:
    """Rows needed to show a prompt without cutting anything: borders, text, spacer, options, bar."""
    w = panel_w - 4
    text_rows = len(_wrap(prompt.text, w))
    option_rows = sum(len(_wrap(label, w - 3)) for label, _ in prompt.options)
    bar = 1 if prompt.total > 0 else 0
    return 2 + text_rows + 1 + option_rows + bar


def draw_prompt(canvas: Canvas, r: Rect, prompt: Prompt, t: float) -> None:
    urgent = prompt.total > 0
    canvas.fill(r, " ")
    canvas.box(r, prompt.title, RED if urgent else DIM, AMBER)
    x, y, w = r.x + 2, r.y + 1, r.w - 4
    bottom = r.y + r.h - 1
    limit = bottom - (1 if urgent else 0)
    avail = max(0, limit - y)

    full_option_lines = [_wrap(label, w - 3) for label, _ in prompt.options]
    full_option_rows = sum(len(lines) for lines in full_option_lines)
    want = min(len(_wrap(prompt.text, w)), 3)          # keep at least this many text lines if we can
    opt_w = max(1, w - 3)
    if full_option_rows > max(0, avail - want - 1):
        # wrapping the options would crowd out the question: one truncated line per option instead
        option_lines = [[label if len(label) <= opt_w else label[:max(0, opt_w - 1)] + "…"]
                         for label, _ in prompt.options]
    else:
        option_lines = full_option_lines
    option_rows = sum(len(lines) for lines in option_lines)

    budget = max(0, avail - option_rows)
    if budget >= 2:
        spacer, text_rows = 1, budget - 1
    else:
        spacer, text_rows = 0, max(1, budget)

    lines = _wrap(prompt.text, w)
    if len(lines) > text_rows:
        lines = lines[:text_rows]
        if lines:
            lines[-1] = lines[-1][:max(0, w - 1)] + "…"
    for line in lines:
        if y >= limit:
            break
        canvas.put(x, y, line, WHITE)
        y += 1
    y += spacer
    for i, ((_, ok), lopts) in enumerate(zip(prompt.options, option_lines)):
        for j, line in enumerate(lopts):
            if y >= limit:
                break
            canvas.put(x, y, (f"{i + 1}  " if j == 0 else "   ") + line, AMBER if ok else DIM, bold=ok and j == 0)
            y += 1
    if urgent:
        n = max(0, w - 5)
        frac = max(0.0, min(1.0, prompt.remaining / prompt.total))
        filled = max(0, min(n, round(n * frac)))
        secs = max(0, math.ceil(prompt.remaining))
        low = prompt.remaining < 4
        flash = low and int(t * 4) % 2 == 0
        canvas.put(x, bottom - 1, "█" * filled + "░" * (n - filled), RED if flash else AMBER)
        canvas.put(x + n + 1, bottom - 1, f"{secs:>2}s", RED if low else WHITE, bold=True)


def draw_status(canvas: Canvas, r: Rect, c: Campaign) -> None:
    canvas.fill(r, " ")
    canvas.box(r, "SGC STATUS", DIM, AMBER)
    x, y, w = r.x + 2, r.y + 1, r.w - 4
    bottom = r.y + r.h - 1
    for name in ("security", "personnel", "intel"):
        if y >= bottom:
            return
        v = c.meters[name]
        n = round(METER_W * v / 100)
        color = RED if v < 25 else AMBER if v < 50 else GREEN
        canvas.put(x, y, f"{name.upper():<10}", DIM)
        canvas.put(x + 10, y, "█" * n + "░" * (METER_W - n), color)
        canvas.put(x + 11 + METER_W, y, f"{v:>3}", WHITE)
        y += 1
    if y < bottom:
        flags = [FLAG_SHORT.get(f, f.split(".")[-1].upper()) for f in sorted(c.inventory) if f not in c.used]
        canvas.put(x, y, (" ".join(flags) or "NO ALLIES YET")[:w], CYAN)
        y += 1
    lords = c.active_lords()
    avail_rows = max(0, bottom - y)
    if avail_rows <= 0:
        shown, rest = [], []
    elif len(lords) > avail_rows:
        shown, rest = lords[:avail_rows - 1], lords[avail_rows - 1:]
    else:
        shown, rest = lords, []
    for lord in shown:
        pips = "■" * lord.strength + "□" * max(0, 5 - lord.strength)
        canvas.put(x, y, f"{lord.name.upper()[:10]:<10} {pips}", AMBER)
        avail_w = max(0, w - 17)
        num = f"{lord.aggression:>3}"
        agr = f" AGR {num}"
        canvas.put(x + 17, y, (agr if len(agr) <= avail_w else num)[:avail_w],
                   RED if lord.aggression >= 60 else DIM)
        y += 1
    if rest:
        canvas.put(x, y, f"+{len(rest)} MORE"[:w], AMBER)
        y += 1


def draw_game(canvas: Canvas, layout: Layout, scene: Scene, c: Campaign, t: float) -> None:
    if layout.mode == "compact":
        p = scene.prompt
        if p is not None:
            opts = " ".join(f"[{i + 1}] {label.upper()[:14].rstrip()}" for i, (label, ok) in enumerate(p.options)
                             if ok)
            timer = f"{max(0, math.ceil(p.remaining))}s " if p.total else ""
            canvas.fill(layout.status, " ", AMBER)
            canvas.put(layout.status.x, layout.status.y, (" " + timer + opts)[:layout.status.w],
                       RED if p.total else AMBER, bold=True)
        return
    if layout.mode != "full":
        return
    r = layout.side
    lords_h = 6 + len(c.active_lords())
    if scene.prompt is not None:
        needed = _prompt_height(scene.prompt, r.w)
        status_h = max(5, min(lords_h, r.h - needed))
    else:
        status_h = min(r.h // 2, lords_h)
    status_h = max(1, min(status_h, r.h))
    draw_status(canvas, Rect(r.x, r.y + r.h - status_h, r.w, status_h), c)
    if scene.prompt is not None:
        draw_prompt(canvas, Rect(r.x, r.y, r.w, r.h - status_h), scene.prompt, t)
