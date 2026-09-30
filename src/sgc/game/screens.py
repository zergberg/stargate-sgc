"""Game screens: the start menu, decisions, the campaign status, the SGC clock and the controls legend."""
from __future__ import annotations

import math
import textwrap

from ..layout import Layout, Rect
from ..model import Prompt, Scene
from ..panels import AMBER, CYAN, DIM, GREEN, RED, WHITE
from ..term.canvas import Canvas
from .clock import stamp
from .database import COLUMNS, TAB_TITLES, TABS, Database
from .menu import Menu
from .room import Room
from .state import Campaign

HILITE = (70, 48, 12)
HEADER_BG = (38, 26, 8)
ALARM_BG = ((120, 10, 5), (50, 5, 5))
METER_W = 10
STATUS_H = 6
LEGENDS = ("bar", "full", "off")
WIDTHS = {"addresses": (0, 18, 10, 10, 5, 5), "missions": (5, 16, 8, 10, 9, 4, 0),
          "teams": (5, 10, 9, 17, 16, 0), "intel": (5, 22, 8, 0)}
KEYS_HELP = (("b", "briefing room"), ("d", "SGC Database"), ("1-9", "give an order"), ("↑↓ ⏎", "choose"),
             ("←→", "Database tabs"), ("/", "search"), ("s f", "sort, filter"), ("?", "legend"),
             ("m + -", "mute, volume"), ("p", "pause (Recruit)"), ("^C Esc", "cancel typing"),
             ("q", "back / quit"))


def _wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        lines += textwrap.wrap(para, max(1, width)) or [""]
    return lines


def _clip(text: str, width: int) -> str:
    """Truncate to width, ending with an ellipsis when something was cut, as draw_prompt does."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[:max(0, width - 1)] + "…"


def _elastic_widths(widths: tuple[int, ...], cols: int) -> list[int]:
    """Fill in the one 0 (elastic) width with whatever's left of the terminal."""
    out = list(widths)
    i = out.index(0) if 0 in out else len(out) - 1
    fixed = sum(w for j, w in enumerate(out) if j != i)
    out[i] = max(4, cols - 2 - fixed - len(out))
    return out


def _db_keys(tab: str) -> list[tuple[str, str]]:
    """Only the keys that do something on the current Database tab."""
    keys = [("←→", "TABS"), ("↑↓", "SCROLL" if tab == "world" else "SELECT")]
    if tab in ("addresses", "missions", "intel"):
        keys.append(("⏎", "OPEN"))
    if tab == "addresses":
        keys += [("/", "SEARCH"), ("s", "SORT"), ("f", "FILTER")]
    keys.append(("q", "BACK"))
    return keys


DB_HELP = (("←→", "switch tabs"), ("↑↓", "select, or scroll a world file"),
           ("⏎", "open a world's file"), ("/", "search names, ids and glyphs"), ("s", "sort by status or name"),
           ("f", "filter by status"), ("?", "legend: bar, full, off"), ("q", "close the Database"))


def _db_help_rows(cols: int) -> list[str]:
    """The full Database legend: every key with what it does, in two columns when there's room."""
    entries = [f"{k:<3} {label}" for k, label in DB_HELP]
    colw = max(len(e) for e in entries) + 3
    per = 2 if 2 * colw + 1 <= cols else 1
    rows = [entries[i:i + per] for i in range(0, len(entries), per)]
    return [" " + "".join(e.ljust(colw) for e in row) for row in rows]


def _db_hint(tab: str) -> str:
    """A short, tab-aware one-liner for the compact Database, where the full key list won't fit."""
    keys = [("←→", "TAB"), ("↑↓", "SCROLL" if tab == "world" else "MOVE")]
    if tab in ("addresses", "missions", "intel"):
        keys.append(("⏎", "OPEN"))
    keys.append(("q", "BACK"))
    return " " + "  ".join(f"{k} {label}" for k, label in keys) + " "


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
            line = (f"{str(rec.get('result', '')).upper():<7} {rec['surveyed']}W {rec['days']}D "
                    f"{str(rec.get('mode', ''))[:4].upper()} {str(rec.get('difficulty', ''))[:3].upper()}")
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


def next_legend(state: str) -> str:
    return LEGENDS[(LEGENDS.index(state) + 1) % len(LEGENDS)] if state in LEGENDS else "bar"


def draw_header(canvas: Canvas, layout: Layout, c: Campaign, alarm: str | None, t: float) -> None:
    """The game's header: the SGC clock and DEFCON on the right; an alarm takes over the whole bar."""
    if layout.mode != "full":
        return
    r = layout.header
    right = f"{stamp(c.minutes)} SGC · DEFCON {2 if alarm else 5} "
    if alarm:
        bg = ALARM_BG[int(t * 2) % 2]
        canvas.fill(r, " ", WHITE, bg)
        canvas.put(r.x, r.y, f" !! ALARM · {alarm.upper()}"[:max(0, r.w - len(right) - 1)], WHITE, bg, bold=True)
    else:
        bg = HEADER_BG
    canvas.put(max(r.x, r.x + r.w - len(right)), r.y, right, WHITE if alarm else AMBER, bg, bold=True)


def draw_status(canvas: Canvas, r: Rect, c: Campaign) -> None:
    canvas.fill(r, " ")
    canvas.box(r, "SGC STATUS", DIM, AMBER)
    x, y, w = r.x + 2, r.y + 1, r.w - 4
    bottom = r.y + r.h - 1
    for name in ("security", "personnel"):
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
        canvas.put(x, y, f"MALP {c.stock['malp']}  UAV {c.stock['uav']}"[:w], CYAN)
        y += 1
    if y < bottom:
        active = len(c.active_missions())
        canvas.put(x, y, (f"{active} MISSION{'S' if active != 1 else ''} ACTIVE" if active else "NO TEAMS OUT")[:w],
                   AMBER if active else DIM)


def draw_game(canvas: Canvas, layout: Layout, scene: Scene, c: Campaign, t: float) -> None:
    """The gate-room side panel in a campaign: the alarm prompt (if any) above the SGC status."""
    if layout.mode == "compact":
        p = scene.prompt
        canvas.fill(layout.status, " ", AMBER)
        if p is not None:
            opts = " ".join(f"[{i + 1}] {label.upper()[:14].rstrip()}" for i, (label, ok) in enumerate(p.options)
                             if ok)
            timer = f"{max(0, math.ceil(p.remaining))}s " if p.total else ""
            canvas.put(layout.status.x, layout.status.y, (" " + timer + opts)[:layout.status.w],
                       RED if p.total else AMBER, bold=True)
        else:
            canvas.put(layout.status.x, layout.status.y, f" {stamp(c.minutes)} SGC"[:layout.status.w], AMBER,
                       bold=True)
        return
    if layout.mode != "full":
        return
    r = layout.side
    status_h = max(1, min(STATUS_H, r.h))
    if scene.prompt is not None:
        need = _prompt_height(scene.prompt, r.w)
        status_h = max(1, min(status_h, r.h - min(need, r.h - 3)))
    draw_status(canvas, Rect(r.x, r.y + r.h - status_h, r.w, status_h), c)
    if scene.prompt is not None:
        draw_prompt(canvas, Rect(r.x, r.y, r.w, r.h - status_h), scene.prompt, t)


def draw_legend(canvas: Canvas, layout: Layout, state: str, keys: list[tuple[str, str]],
                 busy: bool = False) -> None:
    """The controls legend: a one-line bar on the log's bottom edge, or the full help over the side panel.

    `busy` means the side panel already holds something that must stay visible (an open alarm, the
    briefing room's list): the full help falls back to the one-line bar instead of covering it.
    """
    if layout.mode != "full" or state == "off":
        return
    if state == "bar" or busy:
        r = layout.log
        text = " " + "  ".join(f"{k} {label}" for k, label in keys) + " "
        canvas.put(r.x + 2, r.y + r.h - 1, text[:max(0, r.w - 4)], AMBER, HEADER_BG, bold=True)
        return
    r = layout.side
    canvas.fill(r, " ")
    canvas.box(r, "CONTROLS", AMBER, AMBER)
    x, y, w = r.x + 2, r.y + 1, r.w - 4
    kw = max(len(k) for k, _ in KEYS_HELP) + 1
    for k, label in KEYS_HELP:
        for i, line in enumerate(_wrap(label, max(1, w - kw))):
            if y >= r.y + r.h - 1:
                return
            canvas.put(x, y, (k if i == 0 else "").ljust(kw), WHITE, bold=True)
            canvas.put(x + kw, y, line, AMBER)
            y += 1


def draw_room(canvas: Canvas, layout: Layout, room: Room) -> None:
    """The briefing room's list on the side panel (or the status line on a small terminal)."""
    items = room.items()
    if layout.mode == "compact":
        text = " " + "  ".join(f"{i + 1} {label.replace(chr(10), ' · ')}" for i, (label, _) in enumerate(items))
        canvas.fill(layout.status, " ", AMBER)
        canvas.put(layout.status.x, layout.status.y, text[:layout.status.w], AMBER, bold=True)
        return
    if layout.mode != "full":
        return
    r = layout.side
    canvas.fill(r, " ")
    canvas.box(r, room.title, DIM, AMBER)
    x, y, w = r.x + 2, r.y + 1, r.w - 4
    bottom = r.y + r.h - 1
    detail = [part for line in room.detail() for part in _wrap(line, w)]
    for line in detail[:max(0, bottom - y - 3)]:
        canvas.put(x, y, line, CYAN)
        y += 1
    if detail:
        y += 1
    if room.text_mode:
        canvas.put(x, y, "NOTE (ENTER TO SAVE):"[:w], DIM)
        canvas.put(x, y + 1, ("> " + room.note + "_")[-w:], WHITE, bold=True)
        return
    space = max(1, bottom - y - (2 if room.notice else 0))
    blocks = [_item_rows(label, w) for label, _ in items]
    # scroll so the selection is on screen: show from the top if it fits, else end the view on it
    first = 0
    while first < room.sel and sum(len(b) for b in blocks[first:room.sel + 1]) > space:
        first += 1
    top_y = y
    for i in range(first, len(items)):
        _, ok = items[i]
        sel = i == room.sel
        num = f"{i + 1}" if i < 9 else " "
        for j, row in enumerate(blocks[i]):
            if y - top_y >= space:
                break
            text = f" {num}  {row}" if j == 0 else f"    {row}"
            canvas.put(x, y, _clip(text, w).ljust(w)[:w], (WHITE if sel else AMBER) if ok else DIM,
                       HILITE if sel else None, bold=sel and j == 0)
            y += 1
    if room.notice and y < bottom:
        for line in _wrap(room.notice, w)[:max(0, bottom - y - 1)]:
            y += 1
            canvas.put(x, y, line, GREEN, bold=True)


def _item_rows(label: str, w: int) -> list[str]:
    """A room item's rows: each line of the label wrapped to the panel, after the 4-column number gutter."""
    return [part for line in label.split("\n") for part in _wrap(line, max(1, w - 4))]


def _world_lines(db: Database, width: int) -> list[str]:
    return [ln for text in db.detail() for ln in _wrap(text, width)]


def _draw_world_file(canvas: Canvas, x0: int, top: int, bottom: int, width: int, db: Database) -> None:
    """The world file's lines, wrapped to width and scrolled; ↑/↓ MORE markers when content is hidden."""
    lines = _world_lines(db, width)
    height = max(0, bottom - top)
    n = len(lines)
    max_scroll = max(0, n - height)
    db.scroll = max(0, min(db.scroll, max_scroll))
    view = list(lines[db.scroll:db.scroll + height])
    if db.scroll > 0 and view:
        view[0] = "↑ MORE"
    if db.scroll + height < n and view:
        view[-1] = "↓ MORE"
    for i, text in enumerate(view):
        idx = db.scroll + i
        canvas.put(x0, top + i, text[:width], WHITE if idx == 0 else AMBER, bold=idx == 0)


def _draw_database_compact(canvas: Canvas, layout: Layout, db: Database) -> None:
    """A single-column list: the tab (indicated even though the others aren't shown), rows, a key hint."""
    cols, rows = layout.cols, layout.rows
    canvas.fill(Rect(0, 0, cols, rows), " ")
    idx = TABS.index(db.tab) + 1
    canvas.put(0, 0, _clip(f"TAB {idx}/{len(TABS)} {TAB_TITLES[db.tab]}", cols), AMBER, bold=True)
    y, bottom = 1, rows - 1
    if db.tab == "world":
        _draw_world_file(canvas, 0, y, bottom, max(1, cols), db)
    else:
        table = db.rows()
        height = max(0, bottom - y)
        first = max(0, db.sel - height + 1) if table else 0
        for i, row in enumerate(table[first:first + height], start=first):
            sel = i == db.sel
            cell = row.cells[0] if row.cells else ""
            canvas.put(0, y + i - first, _clip(cell, cols), WHITE if sel else AMBER, HILITE if sel else None,
                       bold=sel)
        if not table:
            canvas.put(0, y, "NOTHING ON FILE"[:cols], DIM)
    canvas.put(0, bottom, _db_hint(db.tab)[:cols], AMBER, HEADER_BG, bold=True)


def draw_database(canvas: Canvas, layout: Layout, db: Database, legend: str) -> None:
    """The SGC Database, full screen: tabs, a sortable table or the world file, and its keys."""
    cols, rows = layout.cols, layout.rows
    if layout.mode == "tiny":
        canvas.fill(Rect(0, 0, cols, rows), " ")
        msg = "ENLARGE PANE"
        canvas.put(max(0, (cols - len(msg)) // 2), rows // 2, msg[:cols], AMBER, bold=True)
        return
    if layout.mode == "compact":
        _draw_database_compact(canvas, layout, db)
        return
    full = Rect(0, 0, cols, rows)
    canvas.fill(full, " ")
    canvas.fill(Rect(0, 0, cols, 1), " ", AMBER, HEADER_BG)
    x = 1
    canvas.put(x, 0, "SGC DATABASE", AMBER, HEADER_BG, bold=True)
    x += 14
    for tab in TABS:
        label = f" {TAB_TITLES[tab]} "
        on = tab == db.tab
        canvas.put(x, 0, label, WHITE if on else DIM, HILITE if on else HEADER_BG, bold=on)
        x += len(label) + 1
    clock_text = f"{stamp(db.c.minutes)} SGC "
    canvas.put(max(0, cols - len(clock_text)), 1, clock_text, AMBER, bold=True)
    help_rows = _db_help_rows(cols) if legend == "full" else []
    top, bottom = 2, rows - (2 if legend != "off" else 1) - len(help_rows)
    if db.tab == "addresses":
        line = f"SORT {db.sort.upper()} · FILTER {(db.filter or 'all').upper()} · SEARCH: {db.query}"
        canvas.put(1, 1, (line + ("_" if db.searching else ""))[:max(0, cols - len(clock_text) - 2)], CYAN)
    if db.tab == "world":
        _draw_world_file(canvas, 2, top, bottom, max(1, cols - 4), db)
    else:
        widths = _elastic_widths(WIDTHS[db.tab], cols)
        x = 1
        for head, wd in zip(COLUMNS[db.tab], widths):
            canvas.put(x, top, head[:wd], DIM, bold=True)
            x += wd + 1
        table = db.rows()
        space = max(1, bottom - top - 1)
        first = max(0, db.sel - space + 1)
        for i, row in enumerate(table[first:first + space], start=first):
            sel = i == db.sel
            y = top + 1 + i - first
            if sel:
                canvas.fill(Rect(0, y, cols, 1), " ", WHITE, HILITE)
            x = 1
            for cell, wd in zip(row.cells, widths):
                canvas.put(x, y, _clip(cell, wd), WHITE if sel else AMBER, HILITE if sel else None, bold=sel)
                x += wd + 1
        if not table:
            canvas.put(2, top + 1, "NOTHING ON FILE", DIM)
    if legend != "off":
        text = " " + "  ".join(f"{k} {label}" for k, label in _db_keys(db.tab)) + " "
        canvas.fill(Rect(0, rows - 1, cols, 1), " ", AMBER, HEADER_BG)
        canvas.put(0, rows - 1, text[:cols], AMBER, HEADER_BG, bold=True)
        for i, line in enumerate(help_rows):
            y = rows - 1 - len(help_rows) + i
            canvas.fill(Rect(0, y, cols, 1), " ", AMBER, HEADER_BG)
            canvas.put(0, y, line[:cols], AMBER, HEADER_BG)
