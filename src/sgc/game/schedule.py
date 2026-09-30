"""The SGC's schedule as the player has been told it: the Database's QUEUE tab, and cancelling or reordering the
dial-outs still waiting for the gate. Pure functions over the campaign; the engine logs and saves."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import rules
from .clock import DAY, HOUR, Event, day, short
from .state import TEAMS, Campaign, Mission


@dataclass(frozen=True)
class QueueItem:
    id: str                          # "dial:<op>:<world>", "dial:depart:<mission>", "dial:search:<mission>:<by>",
                                     # "drone:<world>", "mission:<id>" or "team:<name>"
    kind: str                        # "dial_out" | "drone" | "mission" | "team"
    when: str
    what: str
    status: str
    sort: tuple[float, ...]          # dial-outs first, in gate order; then everything else by time (ties by id)
    cancellable: bool = False
    movable: bool = False
    reason: str = ""                 # why x can't cancel it

    @property
    def cells(self) -> tuple[str, str, str]:
        return self.when, self.what, self.status


Travel = dict[str, tuple[int, int]]          # engine.TRAVEL: game minutes until a drone's report, (lo, hi)

WAITING = "WAITING FOR THE GATE"
THROUGH = "ALREADY THROUGH THE GATE"
GONE = "NO LONGER SCHEDULED"
NOT_MOVABLE = "ONLY DIAL-OUTS WAITING FOR THE GATE CAN BE MOVED"
SOON = "REPORT EXPECTED ANY MINUTE"
WAIT_HOURS = 12                              # a withdrawn search leaves the team to the 12-hour wait
TEAM_WORDS = {"base": ("STOOD DOWN", "BACK"), "injured": ("INJURED", "BACK"),
              "captured": ("CAPTURED", "PRESUMED LOST"), "lost": ("RE-FORMING", "READY")}


def at(minute: float, now: float) -> str:
    """'14:00' on the same SGC day as now, else 'D3 14:00'."""
    if day(minute) != day(now):
        return short(minute)
    m = int(minute) % DAY
    return f"{m // HOUR:02d}:{m % HOUR:02d}"


def _name(c: Campaign, wid: str) -> str:
    w = c.worlds.get(wid)
    return (w.name if w is not None else wid).upper()


def gate_order(c: Campaign) -> list[Event]:
    """Queued dial-outs in the order the gate will take them. While the gate is busy, each one due before it
    frees up is re-queued behind the ones already waiting for it (engine._fire), in (due, seq) order."""
    free = c.gate_until
    return sorted(c.events.find(lambda e: e.kind == "dial_out"),
                  key=lambda e: (max(e.due, free), e.due < free, e.due, e.seq))


def dial_id(ev: Event) -> str:
    """A queued dial-out's id, from what it is, so it survives being re-queued or moved."""
    d = ev.data
    if d["op"] in ("malp", "uav", "recall"):
        return f"dial:{d['op']}:{d['world']}"
    if d["op"] == "search":
        return f"dial:search:{d['mission']}:{d['by']}"
    return f"dial:{d['op']}:{d['mission']}"


def _words(c: Campaign, ev: Event) -> tuple[str, str]:
    """A queued dial-out as (the row's WHAT, the same thing in a sentence)."""
    d = ev.data
    op = d["op"]
    if op in ("malp", "uav"):
        name = _name(c, d["world"])
        return f"{op.upper()} → {name}", f"{op.upper()} TO {name}"
    if op == "recall":
        w = c.worlds[d["world"]]
        drone, name = (w.drone or "drone").upper(), _name(c, w.id)
        return f"RECALL {drone} FROM {name}", f"RECALL OF THE {drone} ON {name}"
    m = c.mission(d["mission"])
    name = _name(c, m.world)
    if op == "depart":
        return f"{m.team} → {name} ({m.type.upper()})", f"{m.team} DEPARTURE FOR {name}"
    by = "MALP" if d["by"] == "malp" else d["by"]
    return f"{by} → {name} FOR {m.team}", f"{by} SEARCH FOR {m.team}"


def _drone(c: Campaign, ev: Event, travel: Travel) -> QueueItem:
    """A drone through the gate: only its report window, never the exact rolled time."""
    d = ev.data
    lo, hi = travel[d["drone"]]
    if "sent" in d:
        start, end = max(c.now, d["sent"] + lo), d["sent"] + hi
    else:                                            # an older save didn't note when it went
        start, end = c.now, c.now + hi
    status = f"REPORT EXPECTED {at(start, c.now)}–{at(end, start)}" if end > c.now else SOON
    what = f"{d['drone'].upper()} AT {_name(c, d['world'])}"
    return QueueItem(f"drone:{d['world']}", "drone", at(start, c.now), what, status, (1, start, end),
                     reason=f"{THROUGH}: RECALL FROM THE BRIEFING ROOM")


def _mission(c: Campaign, m: Mission) -> QueueItem:
    def mine(kind: str, op: str | None = None) -> list[Event]:
        return c.events.find(lambda e: e.kind == kind and e.data.get("mission") == m.id
                             and (op is None or e.data.get("op") == op))
    home = f"DUE HOME {short(m.end)}"
    checkin, overdue = mine("checkin"), mine("overdue")
    if m.state == "aborted":
        status, nxt = "RECALLED · COMING HOME", c.now
    elif checkin:
        status, nxt = f"CHECK-IN {at(checkin[0].due, c.now)} · {home}", checkin[0].due
    elif overdue:
        status, nxt = f"NO CONTACT · WAITING UNTIL {short(overdue[0].due)}", overdue[0].due
    elif mine("search_report") or mine("dial_out", "search"):
        status, nxt = "NO CONTACT · SEARCH UNDER WAY", c.now
    elif mine("team_return"):
        status, nxt = home, m.end
    else:                                            # a missed check-in waiting for orders
        status, nxt = "NO CONTACT", c.now
    return QueueItem(f"mission:{m.id}", "mission", at(nxt, c.now),
                     f"{m.team} {m.type.upper()} OF {_name(c, m.world)}", status, (1, nxt), reason=THROUGH)


def view(c: Campaign, travel: Travel) -> list[QueueItem]:
    """What the SGC has scheduled that the player has been told about, soonest first. Incoming wormholes,
    the recovery tick and rolled times never appear."""
    dials = gate_order(c)
    departing = {e.data["mission"] for e in dials if e.data["op"] == "depart"}
    items = [QueueItem(dial_id(ev), "dial_out", str(i + 1), _words(c, ev)[0], WAITING, (0, i),
                       cancellable=True, movable=True) for i, ev in enumerate(dials)]
    items += [_drone(c, ev, travel) for ev in c.events.find(lambda e: e.kind == "malp_return")]
    for m in c.missions:
        tm = c.teams[m.team]
        if m.state in ("active", "aborted") and tm.status == "offworld" and tm.mission == m.id \
                and m.id not in departing:
            items.append(_mission(c, m))
    for name in TEAMS:
        t = c.teams[name]
        if t.status in TEAM_WORDS and t.until > c.now:
            label, word = TEAM_WORDS[t.status]
            items.append(QueueItem(f"team:{name}", "team", at(t.until, c.now), f"{name} {label}",
                                   f"{word} {short(t.until)}", (1, t.until),
                                   reason=f"NOTHING TO CANCEL: {name} IS {label}"))
    # every key is public: the event heap's order (the rolled times) must never decide a tie
    return sorted(items, key=lambda i: (i.sort, i.id))


def _find(c: Campaign, item_id: str) -> Event | None:
    return next((e for e in gate_order(c) if dial_id(e) == item_id), None)


def _refusal(c: Campaign, item_id: str, travel: Travel, why: Callable[[QueueItem], str]) -> str:
    item = next((i for i in view(c, travel) if i.id == item_id), None)
    return why(item) if item is not None else GONE


def _undo(c: Campaign, d: dict) -> list[str]:
    """Take back what queuing the dial-out did (engine._launch, assign, recall_drone, _missed_order)."""
    op = d["op"]
    if op in ("malp", "uav"):
        return rules.stow(c, op)                     # back into stores
    if op == "recall":
        return []                                    # the drone stays where it is
    m = c.mission(d["mission"])
    if op == "depart":                               # it never left: no mission at all
        tm = c.teams[m.team]
        tm.status, tm.where, tm.mission = "base", "", None
        m.state = "cancelled"
        c.record["missions"] -= 1
        return [f"{m.team} STANDING BY AT BASE"]
    lines = rules.withdraw_search(c, d)
    if m.state == "active":                          # the missing team is left to the 12-hour wait
        c.events.push(c.now + WAIT_HOURS * HOUR, "overdue", {"mission": m.id})
        lines.append(f"WAITING {WAIT_HOURS} HOURS FOR {m.team}")
    return lines


def cancel(c: Campaign, item_id: str, confirm: bool, travel: Travel) -> tuple[str, list[str], bool]:
    """Cancel a dial-out waiting for the gate: (message, log lines from undoing it, done). Without confirm it
    only asks; a row that can't be cancelled answers with why."""
    ev = _find(c, item_id)
    if ev is None:
        return _refusal(c, item_id, travel, lambda i: i.reason), [], False
    said = _words(c, ev)[1]
    if not confirm:
        return f"CANCEL THE {said}?  x AGAIN TO CONFIRM", [], False
    c.events.remove(lambda e: e is ev)
    return f"CANCELLED: {said}", _undo(c, ev.data), True


def move(c: Campaign, item_id: str, delta: int, travel: Travel) -> tuple[str, bool]:
    """Move a waiting dial-out one place up (delta < 0) or down the gate queue: (message, done)."""
    dials = gate_order(c)
    ids = [dial_id(e) for e in dials]
    if item_id not in ids:
        return _refusal(c, item_id, travel, lambda i: NOT_MOVABLE), False
    i = ids.index(item_id)
    j = i + (1 if delta > 0 else -1)
    if j < 0:
        return "ALREADY FIRST IN THE GATE QUEUE", False
    if j >= len(dials):
        return "ALREADY LAST IN THE GATE QUEUE", False
    c.events.swap(dials[i], dials[j])
    return f"{_words(c, dials[i])[1]}: NOW {j + 1} IN THE GATE QUEUE", True
