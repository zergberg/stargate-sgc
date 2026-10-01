"""The SGC Database: a pure view-model over the campaign. Tabs, rows, sort, filter, search and the world file."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from . import arcs, factions, trade
from .arcs import ARCS
from .clock import DAY, HOUR, short
from .rules import FLAG_NAMES
from .schedule import QueueItem
from .state import Campaign, rank, team_names
from .world import FACTION_IDS, World, faction_name

if TYPE_CHECKING:          # the engine imports this module, so it's only imported here for type hints
    from .engine import Engine
    from .room import Room

TABS = ("addresses", "world", "missions", "teams", "intel", "factions", "trade", "arcs", "queue")
TAB_TITLES = {"addresses": "ADDRESSES", "world": "WORLD FILE", "missions": "MISSIONS", "teams": "TEAMS",
              "intel": "INTEL", "factions": "FACTIONS", "trade": "TRADE", "arcs": "ARCS", "queue": "QUEUE"}
COLUMNS = {
    "addresses": ("NAME", "GLYPHS", "STATUS", "LAST VISIT", "FLAGS", "DRONE"),
    "missions": ("TEAM", "WORLD", "TYPE", "STARTED", "OUTCOME", "CAS", "FINDINGS"),
    "teams": ("TEAM", "SPECIALTY", "RANK", "STATUS", "LOCATION", "HISTORY"),
    "intel": ("KIND", "WHAT", "WORLD", "SOURCE"),
    "factions": ("KIND", "NAME", "STANDING", "ON FILE FROM"),
    "trade": ("WORLD", "GOODS", "NEXT", "LEFT", "RISK"),
    "arcs": ("ARC", "STATE", "WHERE IT STANDS", "SINCE"),
    "queue": ("WHEN", "WHAT", "STATUS"),
    "world": (),
}
SEARCHABLE = ("addresses", "queue")
OPENS = ("addresses", "missions", "intel", "trade", "arcs")      # tabs where Enter opens a world's file
ORDERS_TABS = ("addresses", "world")        # Part 9: tabs where o opens the ORDERS panel (world: Enter too)
HINT = "EVERY ADDRESS VISITED · RE-SURVEY, STUDY RUINS OR ASK ALLIES FOR MORE"
ALLY_FLAGS = {"tokra": "ally.tokra", "asgard": "ally.asgard", "tollan": "ally.tollan", "nox": "ally.nox",
              "jaffa": "ally.jaffa"}
SORTS = ("status", "name")
FILTERS = (None, "unexplored", "probed", "surveyed", "contact", "hostile", "lost")
_STATUS_ORDER = {s: i for i, s in enumerate(("contact", "surveyed", "probed", "unexplored", "hostile", "lost"))}


def time_left(minutes: float) -> str:
    """Game time still to run, rounded up to the hour: '11H', '1D 20H', '4D'."""
    hours = max(1, -(-int(minutes) // HOUR))
    d, h = divmod(hours, DAY // HOUR)
    return " ".join(part for part in (f"{d}D" if d else "", f"{h}H" if h else "") if part)


def team_status(c: Campaign, name: str) -> str:
    """A team's status with its timer: STOOD DOWN 11H, INJURED 1D 20H, CAPTURED 4D, RE-FORMING 2D 5H,
    FORMING 1D 4H, TRAINING 20H.

    An offworld team reads OFFWORLD, and one waiting for the gate STAGING; where it's going belongs elsewhere.
    """
    t = c.teams[name]
    left = t.until - c.now
    if t.status == "staging":
        return "STAGING"
    if t.status == "base":
        return f"STOOD DOWN {time_left(left)}" if left > 0 else "BASE"
    if t.status == "injured":
        return f"INJURED {time_left(left)}"
    if t.status == "captured":
        return f"CAPTURED {time_left(left)}"
    if t.status == "lost":
        return f"RE-FORMING {time_left(left)}" if t.until else "LOST"
    if t.status == "forming":
        return f"FORMING {time_left(left)}"
    if t.status == "training":
        return f"TRAINING {time_left(left)}"
    return t.status.upper()


def _lead(w: World) -> bool:
    """An address that came from intel rather than the cartouche."""
    return w.found.startswith("intel")


@dataclass(frozen=True)
class Row:
    key: str                    # world id, mission id or team name
    cells: tuple[str, ...]


class Database:
    def __init__(self, c: Campaign, schedule: Callable[[], list[QueueItem]] | None = None,
                 engine: "Engine | None" = None):
        self.c = c
        self.schedule = schedule or (lambda: [])   # the engine's schedule_view: the QUEUE tab's only source
        self.engine = engine            # Part 9: hosts a Room for the ORDERS panel; None in view-only tests
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
        self.orders: "Room | None" = None   # Part 9: the hosted Room, open on an address's action screen
        self.compact = False            # the app's layout.mode == "compact": the ORDERS panel needs full size

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
                                      (w.drone or ("wreck" if w.wreck else "")).upper())))
            return out
        if self.tab == "missions":
            ms = sorted(c.missions, key=lambda m: (m.team, -m.start))
            return [Row(str(m.id), (m.team, self._world_name(m.world), m.type.upper(), short(m.start),
                                    m.state.upper(), str(m.casualties), "; ".join(m.findings) or "—"))
                    for m in ms]
        if self.tab == "teams":
            out = []
            for name in team_names(c):
                t = c.teams[name]
                done = [m for m in c.missions if m.team == name and m.state not in ("active", "cancelled")]
                where = self._world_name(t.where) if t.where and t.status != "staging" \
                    else "—" if t.status == "lost" else "SGC"
                history = f"{len(done)} missions" + (f", last {self._world_name(done[-1].world)}" if done else "")
                specialty = t.specialty.upper() + (f"/{t.secondary.upper()}" if t.secondary else "")
                out.append(Row(name, (name, specialty, rank(t).upper(), team_status(c, name), where,
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
            for flag in sorted(f for f in c.inventory if f.startswith("tech.")):
                out.append(Row("", ("TECH", FLAG_NAMES[flag], "—", "on file")))
            for w in c.worlds.values():
                if w.status in ("surveyed", "contact") and "ruins" in w.seen.get("features", ""):
                    out.append(Row(w.id, ("SOURCE", "ruins: study or re-survey for addresses", w.id, "Dr. Jackson")))
            for fid in factions.ALLIES:
                f = c.factions[fid]
                if f.known and f.trust >= 50:
                    out.append(Row("", ("SOURCE", f"{faction_name(fid)}: allied intelligence", "—",
                                        "shares addresses at reviews")))
            return out
        if self.tab == "factions":
            out = []
            for fid in FACTION_IDS:                    # the Goa'uld come first in FACTION_IDS
                f = c.factions[fid]
                if not f.known:
                    continue
                standing = "ALLIANCE" if ALLY_FLAGS.get(fid) in c.inventory else factions.words(c, fid)
                out.append(Row(fid, ("GOA'ULD" if f.kind == "goauld" else "ALLY", faction_name(fid), standing,
                                     f.source)))
            return out
        if self.tab == "trade":
            return [Row(d.world, (self._world_name(d.world), f"{d.amount} {d.goods.upper()}",
                                  short(d.next) if d.state == "active" else d.state.upper(), str(d.left),
                                  trade.risk_words(c, d))) for d in c.deals]
        if self.tab == "arcs":
            out = []
            for aid, st in c.arcs.items():
                if st.state == "dormant":
                    continue
                w = arcs.arc_world(c, aid)
                key = w.id if w is not None and w.id in c.worlds else ""
                out.append(Row(key, (ARCS[aid].title, st.state.upper(), arcs.stage_text(c, aid),
                                     short(st.started) if st.started is not None else "—")))
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

    def hint(self) -> str | None:
        """A one-line nudge for the Addresses tab once every address has been visited (never a spoiler)."""
        return HINT if self.tab == "addresses" and "explored" in self.c.hints else None

    def disarm(self) -> None:
        """Clear the armed cancel and any showing reply. The app calls this before ?, m, + and -."""
        self.armed, self.message = None, ""

    # ------------------------------------------------------------------ the ORDERS panel (Part 9)
    def _order_target(self) -> str | None:
        """The address ORDERS opens for: the selected row on Addresses, or the World file's own
        address."""
        if self.tab == "world":
            return self.world_id if self.world_id in self.c.worlds else None
        row = self.selected()
        return row.key if row is not None and row.key in self.c.worlds else None

    def _open_orders(self, wid: str) -> None:
        """Host the briefing room's Room on this address, positioned on its first step -- one source of
        truth for the actions, their greyed reasons and the engine calls they make."""
        if self.engine is None:
            return
        from .room import Room          # deferred: engine.py imports this module, so Room can't be a
        self.orders = Room(self.engine)  # top-level import here without a circular import
        self.orders.open_on_world(wid)

    def _orders_key(self, k: str) -> None:
        """Keys while the ORDERS panel is open: the briefing room's own choosing keys reach the hosted
        Room, Esc/Ctrl+C step back a screen and close the panel from its first step, note typing works as
        in the briefing room, and every other Database key is ignored."""
        from .room import CANCEL_KEYS
        room = self.orders
        if room.text_mode:
            if k in CANCEL_KEYS or k == "enter" or k.startswith("ch:") or k == "backspace":
                room.key(k)
            return
        if k in CANCEL_KEYS:
            if room.back() == ("close",):
                self.orders = None
            return
        if k in ("up", "down", "enter") or (len(k) == 1 and k.isdigit()):
            room.key(k)

    def close_orders(self) -> None:
        """Drop a half-open ORDERS panel: an alarm closes the Database outright, and reopening it brings
        back the tab, search and scroll but not the panel."""
        self.orders = None

    def set_compact(self, compact: bool) -> None:
        """The app calls this whenever it relays out or opens the Database, reporting whether it's drawn
        compact. The ORDERS panel needs a full-size pane, so a resize into compact while it's open closes
        it, same as an alarm would."""
        self.compact = compact
        if compact:
            self.orders = None

    def _try_open_orders(self, wid: str | None) -> None:
        """Open ORDERS on this address, unless the Database is compact: there's no room for the panel, so
        it stays closed and the tab gets a reply saying why instead."""
        if wid is None:
            return
        if self.compact:
            self.message = "ORDERS NEED A LARGER PANE"
            return
        self._open_orders(wid)

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
        if w.wreck:
            lines.append("  UAV WRECK ON SITE")
        held = [n for n in team_names(self.c)
                if self.c.teams[n].status == "captured" and self.c.teams[n].where == w.id]
        drones = [d.drone for d in self.c.captured_drones if d.world == w.id and d.located]
        if held or drones:
            lines += ["", "HELD HERE"] + [f"  {n} (CAPTURED)" for n in held] + [f"  OUR {d.upper()}" for d in drones]
        deals = [d for d in self.c.deals if d.world == w.id]
        if deals:
            lines += ["", "TRADE"] + [
                f"  {d.amount} {d.goods.upper()} EVERY {trade.EVERY // HOUR} HOURS · "
                + (f"NEXT {short(d.next)}" if d.state == "active" else d.state.upper()) for d in deals]
        lines += ["", "MISSION OPTIONS: " + ", ".join(o.upper() for o in w.options), "", "REPORTS"]
        lines += [f"  {short(m)}  {text}" for m, text in w.reports] or ["  none"]
        lines += ["", "NOTES"]
        lines += [f"  {short(m)}  {text}" for m, text in w.notes] or ["  none"]
        return lines

    # ------------------------------------------------------------------ keys
    @property
    def text_mode(self) -> bool:
        return self.searching or (self.orders is not None and self.orders.text_mode)

    def key(self, k: str) -> tuple | None:
        """Handle a key; returns ("close",), ("cancel", id, confirm) or ("move", id, delta)."""
        if self.orders is not None:
            self._orders_key(k)
            return None
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
                self.message = "NO MATCHES" if self.query.strip() else "NOTHING SCHEDULED"
                return None
            if k == "x":
                confirm = self.armed == row.key
                self.armed = None if confirm else row.key
                return ("cancel", row.key, confirm)
            self.armed = None
            return ("move", row.key, -1 if k == "[" else 1)
        self.disarm()
        if k == "q":
            return ("close",)
        if k in ("right", "tab", "left"):
            step = -1 if k == "left" else 1
            self.tab, self.sel = TABS[(TABS.index(self.tab) + step) % len(TABS)], 0
            if self.tab in SEARCHABLE:
                self.query, self.searching = "", False   # arriving at a search tab starts it fresh, not the
            if self.tab == "world":                      # other search tab's leftover query
                self.scroll = 0
        elif k in ("up", "down") and self.tab == "world":
            self.scroll = max(0, self.scroll + (-1 if k == "up" else 1))
        elif k in ("up", "down"):
            n = len(self.rows())
            if n:
                self.sel = (self.sel + (-1 if k == "up" else 1)) % n
        elif k == "enter" and self.tab == "world":        # the world tab's only Enter: it has no world
            self._try_open_orders(self._order_target())    # file of its own to open
        elif k == "o" and self.tab in ORDERS_TABS:
            self._try_open_orders(self._order_target())
        elif k == "enter" and self.tab in OPENS:
            row = self.selected()
            if row is not None and self.tab == "missions":
                self.world_id, self.tab, self.scroll = self.c.mission(int(row.key)).world, "world", 0
            elif row is not None and row.key in self.c.worlds:    # a TECH or ally SOURCE row is not a world
                self.world_id, self.tab, self.scroll = row.key, "world", 0
        elif k == "escape" and self.tab in SEARCHABLE:   # a search kept with Enter: Esc clears it
            self.query, self.sel = "", 0
        elif k == "/" and self.tab in SEARCHABLE:
            self.searching, self.query, self.sel = True, "", 0
        elif k == "s" and self.tab == "addresses":
            self.sort = SORTS[(SORTS.index(self.sort) + 1) % len(SORTS)]
        elif k == "f" and self.tab == "addresses":
            self.filter, self.sel = FILTERS[(FILTERS.index(self.filter) + 1) % len(FILTERS)], 0
        return None
