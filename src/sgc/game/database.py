"""The SGC Database: a pure view-model over the campaign. Tabs, rows, sort, filter, search and the world file."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .clock import DAY, HOUR, short
from .schedule import QueueItem
from .state import TEAMS, Campaign, rank
from .world import World

TABS = ("addresses", "world", "missions", "teams", "intel", "queue")
TAB_TITLES = {"addresses": "ADDRESSES", "world": "WORLD FILE", "missions": "MISSIONS", "teams": "TEAMS",
              "intel": "INTEL", "queue": "QUEUE"}
COLUMNS = {
    "addresses": ("NAME", "GLYPHS", "STATUS", "LAST VISIT", "FLAGS", "DRONE"),
    "missions": ("TEAM", "WORLD", "TYPE", "STARTED", "OUTCOME", "CAS", "FINDINGS"),
    "teams": ("TEAM", "SPECIALTY", "RANK", "STATUS", "LOCATION", "HISTORY"),
    "intel": ("KIND", "WHAT", "WORLD", "SOURCE"),
    "queue": ("WHEN", "WHAT", "STATUS"),
    "world": (),
}
SEARCHABLE = ("addresses", "queue")
SORTS = ("status", "name")
FILTERS = (None, "unexplored", "probed", "surveyed", "contact", "hostile", "lost")
_STATUS_ORDER = {s: i for i, s in enumerate(("contact", "surveyed", "probed", "unexplored", "hostile", "lost"))}


def time_left(minutes: float) -> str:
    """Game time still to run, rounded up to the hour: '11H', '1D 20H', '4D'."""
    hours = max(1, -(-int(minutes) // HOUR))
    d, h = divmod(hours, DAY // HOUR)
    return " ".join(part for part in (f"{d}D" if d else "", f"{h}H" if h else "") if part)


def team_status(c: Campaign, name: str) -> str:
    """A team's status with its timer: STOOD DOWN 11H, INJURED 1D 20H, CAPTURED 4D, RE-FORMING 2D 5H.

    An offworld team reads OFFWORLD; where it is belongs in another column.
    """
    t = c.teams[name]
    left = t.until - c.now
    if t.status == "base":
        return f"STOOD DOWN {time_left(left)}" if left > 0 else "BASE"
    if t.status == "injured":
        return f"INJURED {time_left(left)}"
    if t.status == "captured":
        return f"CAPTURED {time_left(left)}"
    if t.status == "lost":
        return f"RE-FORMING {time_left(left)}" if t.until else "LOST"
    return t.status.upper()


def _lead(w: World) -> bool:
    """An address that came from intel rather than the cartouche."""
    return w.found.startswith("intel")


@dataclass(frozen=True)
class Row:
    key: str                    # world id, mission id or team name
    cells: tuple[str, ...]


class Database:
    def __init__(self, c: Campaign, schedule: Callable[[], list[QueueItem]] | None = None):
        self.c = c
        self.schedule = schedule or (lambda: [])   # the engine's schedule_view: the QUEUE tab's only source
        self.tab = "addresses"
        self.sel = 0
        self.sort = "status"
        self.filter: str | None = None
        self.query = ""
        self.searching = False
        self.world_id = next(iter(c.worlds))
        self.scroll = 0                 # the world tab's line offset; the draw clamps it to the content
        self.armed: str | None = None   # the QUEUE row x was pressed on once: x again there confirms
        self.message = ""               # the engine's reply to the last x, [ or ]

    # ------------------------------------------------------------------ rows
    def _team_on(self, w: World) -> bool:
        return any(t.where == w.id and t.status == "offworld" for t in self.c.teams.values())

    def _match(self, w: World) -> bool:
        if self.filter and w.status != self.filter:
            return False
        q = self.query.strip().lower()
        if not q:
            return True
        return q in w.id.lower() or q in w.glyph_text or any(q in n.lower() for n, _, _ in w.names)

    def worlds(self) -> list[World]:
        ws = [w for w in self.c.worlds.values() if self._match(w)]
        if self.sort == "name":
            return sorted(ws, key=lambda w: (w.name.lower(), w.id))
        return sorted(ws, key=lambda w: (_STATUS_ORDER[w.status], w.name.lower(), w.id))

    def rows(self) -> list[Row]:
        c = self.c
        if self.tab == "addresses":
            out = []
            for w in self.worlds():
                name = w.name if w.name == w.id else f"{w.name} ({w.id})"
                flags = ("T" if self._team_on(w) else "") + ("N" if w.notes else "") + \
                        ("L" if _lead(w) else "")
                out.append(Row(w.id, (name, w.glyph_text, w.status.upper(),
                                      short(w.last_visit) if w.last_visit is not None else "—", flags,
                                      (w.drone or "").upper())))
            return out
        if self.tab == "missions":
            ms = sorted(c.missions, key=lambda m: (m.team, -m.start))
            return [Row(str(m.id), (m.team, self._world_name(m.world), m.type.upper(), short(m.start),
                                    m.state.upper(), str(m.casualties), "; ".join(m.findings) or "—"))
                    for m in ms]
        if self.tab == "teams":
            out = []
            for name in TEAMS:
                t = c.teams[name]
                done = [m for m in c.missions if m.team == name and m.state not in ("active", "cancelled")]
                where = self._world_name(t.where) if t.where else "—" if t.status == "lost" else "SGC"
                history = f"{len(done)} missions" + (f", last {self._world_name(done[-1].world)}" if done else "")
                out.append(Row(name, (name, t.specialty.upper(), rank(t).upper(), team_status(c, name), where,
                                     history)))
            return out
        if self.tab == "intel":
            out = []
            for w in c.worlds.values():
                for n, source, minute in w.names:
                    out.append(Row(w.id, ("NAME", n, w.id, f"{source}, {short(minute)}")))
            for w in c.worlds.values():
                if _lead(w) and w.status == "unexplored":
                    out.append(Row(w.id, ("LEAD", "address not yet visited", w.id, w.found)))
            return out
        if self.tab == "queue":
            q = self.query.strip().lower()
            return [Row(i.id, i.cells) for i in self.schedule() if not q or q in "  ".join(i.cells).lower()]
        return []

    def _world_name(self, wid: str) -> str:
        w = self.c.worlds.get(wid)
        return w.name if w is not None else wid

    def selected(self) -> Row | None:
        rows = self.rows()
        if not rows:
            return None
        self.sel = max(0, min(self.sel, len(rows) - 1))
        return rows[self.sel]

    def select(self, key: str) -> None:
        """Put the selection on the row with this key, if it's still listed: it follows a moved row."""
        for i, row in enumerate(self.rows()):
            if row.key == key:
                self.sel = i
                return

    # ------------------------------------------------------------------ the world file
    def detail(self) -> list[str]:
        w = self.c.worlds.get(self.world_id)
        if w is None:
            return ["NO WORLD SELECTED"]
        named = f"{w.name.upper()} · " if w.name != w.id else ""        # an unnamed world: the designation once
        lines = [f"{named}{w.id} · {w.status.upper()}",f"GLYPHS {w.glyph_text}",
                 f"ADDRESS FROM {w.found.upper()}", "", "NAMES"]
        lines += [f"  {n} — {s}, {short(m)}" for n, s, m in w.names] or ["  none known"]
        lines += ["", "TELEMETRY"]
        lines += [f"  {k.upper()}: {v}" for k, v in w.seen.items()] or ["  no readings"]
        if w.drone:
            lines.append(f"  {w.drone.upper()} ON SITE")
        lines += ["", "MISSION OPTIONS: " + ", ".join(o.upper() for o in w.options), "", "REPORTS"]
        lines += [f"  {short(m)}  {text}" for m, text in w.reports] or ["  none"]
        lines += ["", "NOTES"]
        lines += [f"  {short(m)}  {text}" for m, text in w.notes] or ["  none"]
        return lines

    # ------------------------------------------------------------------ keys
    @property
    def text_mode(self) -> bool:
        return self.searching

    def key(self, k: str) -> tuple | None:
        """Handle a key; returns ("close",), ("cancel", id, confirm) or ("move", id, delta)."""
        if self.searching:
            if k in ("ctrl-c", "escape"):                 # cancel: clear the search and close it
                self.searching, self.query, self.sel = False, "", 0
            elif k.startswith("ch:"):
                self.query += k[3:]
                self.sel = 0
            elif k == "backspace":
                self.query = self.query[:-1]
            elif k == "enter":
                self.searching = False
            return None
        if self.tab == "queue" and k in ("x", "[", "]"):
            row = self.selected()
            if row is None:
                self.message = "NOTHING SCHEDULED"
                return None
            if k == "x":
                confirm = self.armed == row.key
                self.armed = None if confirm else row.key
                return ("cancel", row.key, confirm)
            self.armed = None
            return ("move", row.key, -1 if k == "[" else 1)
        self.armed, self.message = None, ""
        if k == "q":
            return ("close",)
        if k in ("right", "tab", "left"):
            step = -1 if k == "left" else 1
            self.tab, self.sel = TABS[(TABS.index(self.tab) + step) % len(TABS)], 0
            if self.tab == "world":
                self.scroll = 0
        elif k in ("up", "down") and self.tab == "world":
            self.scroll = max(0, self.scroll + (-1 if k == "up" else 1))
        elif k in ("up", "down"):
            n = len(self.rows())
            if n:
                self.sel = (self.sel + (-1 if k == "up" else 1)) % n
        elif k == "enter" and self.tab in ("addresses", "missions", "intel"):
            row = self.selected()
            if row is not None:
                self.world_id = self.c.mission(int(row.key)).world if self.tab == "missions" else row.key
                self.tab, self.scroll = "world", 0
        elif k == "/" and self.tab in SEARCHABLE:
            self.searching, self.query, self.sel = True, "", 0
        elif k == "s" and self.tab == "addresses":
            self.sort = SORTS[(SORTS.index(self.sort) + 1) % len(SORTS)]
        elif k == "f" and self.tab == "addresses":
            self.filter, self.sel = FILTERS[(FILTERS.index(self.filter) + 1) % len(FILTERS)], 0
        return None
