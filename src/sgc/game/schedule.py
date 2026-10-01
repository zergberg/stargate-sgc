"""The SGC's schedule as the player has been told it: the Database's QUEUE tab, and cancelling or reordering the
dial-outs still waiting for the gate. Pure functions over the campaign; the engine logs and saves."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import rules
from .clock import DAY, HOUR, Event, day, short
from .state import Campaign, Mission, team_names


@dataclass(frozen=True)
class QueueItem:
    id: str                          # "dial:<op>:<world>", "dial:depart:<mission>", "dial:search:<mission>:<by>",
                                     # "uplink:<world>", "mission:<id>", "team:<name>" or "deal:<id>"
    kind: str                        # "dial_out" | "uplink" | "mission" | "team" | "delivery"
    when: str
    what: str
    status: str
    sort: tuple[float, ...]          # dial-outs first, in gate order; then everything else by time (ties by id)
    cancellable: bool = False
    movable: bool = False
    reason: str = ""                 # why x can't cancel it
    brief: str = ""                  # the GATE QUEUE box's row: '1 SG-2 STAGING ABYDOS', 'SG-1 CHECK-IN 14:00'

    @property
    def cells(self) -> tuple[str, str, str]:
        return self.when, self.what, self.status


WAITING = "WAITING FOR THE GATE"
THROUGH = "ALREADY THROUGH THE GATE"
GONE = "NO LONGER SCHEDULED"
NOT_MOVABLE = "ONLY DIAL-OUTS WAITING FOR THE GATE CAN BE MOVED"
COLLECTING = "THE DRONE IS ALREADY COLLECTING"
CHECKIN_REASON = "A SCHEDULED CHECK-IN"      # why a drone's own check-in can't be cancelled
WAIT_HOURS = 12                              # a withdrawn search leaves the team to the 12-hour wait
TEAM_WORDS = {"base": ("STOOD DOWN", "BACK"), "injured": ("INJURED", "BACK"),
              "captured": ("CAPTURED", "PRESUMED LOST"), "lost": ("RE-FORMING", "READY"),
              "forming": ("FORMING", "READY"), "training": ("TRAINING", "BACK")}


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
    frees up is re-queued behind the ones already waiting for it (engine._fire), in (due, seq) order. An older
    save's paid recall still dials, but is never listed."""
    free = c.gate_until
    return sorted(c.events.find(lambda e: e.kind == "dial_out" and e.data["op"] != "recall"),
                  key=lambda e: (max(e.due, free), e.due < free, e.due, e.seq))


def dial_id(ev: Event) -> str:
    """A queued dial-out's id, from what it is, so it survives being re-queued or moved."""
    d = ev.data
    if d["op"] in ("malp", "uav", "uplink"):
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
        if d.get("extended"):
            return f"{op.upper()} → {name} (EXTENDED)", f"{op.upper()} EXTENDED REPORT TO {name}"
        return f"{op.upper()} → {name}", f"{op.upper()} TO {name}"
    if op == "uplink":
        name, drone = _name(c, d["world"]), d["drone"].upper()
        return f"{drone} UPLINK · {name}", f"{drone} UPLINK FROM {name}"
    m = c.mission(d["mission"])
    name = _name(c, m.world)
    if op == "depart":
        return f"{m.team} → {name} ({m.type.upper()})", f"{m.team} DEPARTURE FOR {name}"
    by = "MALP" if d["by"] == "malp" else d["by"]
    return f"{by} → {name} FOR {m.team}", f"{by} SEARCH FOR {m.team}"


def _brief(c: Campaign, ev: Event) -> str:
    """A queued dial-out as the GATE QUEUE box says it: a departure is a team staging."""
    d = ev.data
    if d["op"] == "depart":
        m = c.mission(d["mission"])
        return f"{m.team} STAGING {_name(c, m.world)}"
    return _words(c, ev)[0]


def _mission(c: Campaign, m: Mission) -> QueueItem:
    def mine(kind: str, op: str | None = None) -> list[Event]:
        return c.events.find(lambda e: e.kind == kind and e.data.get("mission") == m.id
                             and (op is None or e.data.get("op") == op))
    home = f"DUE HOME {short(m.end)}"
    checkin, overdue = mine("checkin"), mine("overdue")
    if m.state == "aborted":
        status, nxt, brief = "RECALLED · COMING HOME", c.now, "COMING HOME"
    elif checkin:
        status, nxt = f"CHECK-IN {at(checkin[0].due, c.now)} · {home}", checkin[0].due
        brief = f"CHECK-IN {at(nxt, c.now)}"
    elif overdue:
        status, nxt = f"NO CONTACT · WAITING UNTIL {short(overdue[0].due)}", overdue[0].due
        brief = "NO CONTACT"
    elif mine("search_report") or mine("dial_out", "search"):
        status, nxt, brief = "NO CONTACT · SEARCH UNDER WAY", c.now, "SEARCH UNDER WAY"
    elif mine("team_return"):
        status, nxt, brief = home, m.end, f"HOME {at(m.end, c.now)}"
    else:                                            # a missed check-in waiting for orders
        status, nxt, brief = "NO CONTACT", c.now, "NO CONTACT"
    return QueueItem(f"mission:{m.id}", "mission", at(nxt, c.now),
                     f"{m.team} {m.type.upper()} OF {_name(c, m.world)}", status, (1, nxt), reason=THROUGH,
                     brief=f"{m.team} {brief}")


def view(c: Campaign) -> list[QueueItem]:
    """What the SGC has scheduled that the player has been told about, soonest first. Incoming wormholes,
    the recovery tick, a probe's report and rolled times never appear."""
    dials = gate_order(c)
    items = [QueueItem(dial_id(ev), "dial_out", str(i + 1), _words(c, ev)[0], WAITING, (0, i),
                       cancellable=ev.data["op"] != "uplink", movable=True,
                       reason=COLLECTING if ev.data["op"] == "uplink" else "", brief=f"{i + 1} {_brief(c, ev)}")
             for i, ev in enumerate(dials)]
    for ev in c.events.find(lambda e: e.kind == "uplink"):       # its window, never the rolled time
        d = ev.data
        start, end = max(c.now, d["from"]), d["to"]
        window = f"{at(start, c.now)}–{at(end, start)}"
        items.append(QueueItem(f"uplink:{d['world']}", "uplink", at(start, c.now),
                               f"{d['drone'].upper()} UPLINK · {_name(c, d['world'])}", f"EXPECTED {window}",
                               (1, start, end), reason=COLLECTING, brief=f"{d['drone'].upper()} UPLINK {window}"))
    for ev in c.events.find(lambda e: e.kind == "drone_checkin"):    # its exact time: it isn't a hidden roll
        d = ev.data
        name = _name(c, d["world"])
        items.append(QueueItem(f"checkin:{d['world']}", "drone_checkin", at(ev.due, c.now),
                               f"{d['drone'].upper()} CHECK-IN · {name}", f"DUE {at(ev.due, c.now)}",
                               (1, ev.due), reason=CHECKIN_REASON,
                               brief=f"{d['drone'].upper()} CHECK-IN {name} {at(ev.due, c.now)}"))
    for m in c.missions:
        if m.state not in ("active", "aborted"):
            continue                                 # history, perhaps of a team since disbanded
        tm = c.teams.get(m.team)
        if tm is not None and tm.status == "offworld" and tm.mission == m.id:    # a staging team isn't out yet
            items.append(_mission(c, m))
    for name in team_names(c):
        t = c.teams[name]
        if t.status in TEAM_WORDS and t.until > c.now:
            label, word = TEAM_WORDS[t.status]
            items.append(QueueItem(f"team:{name}", "team", at(t.until, c.now), f"{name} {label}",
                                   f"{word} {short(t.until)}", (1, t.until),
                                   reason=f"NOTHING TO CANCEL: {name} IS {label}",
                                   brief=f"{name} {label} {at(t.until, c.now)}"))
    for d in c.deals:                                # announced when made; the disruption odds never show
        if d.state == "active":
            items.append(QueueItem(f"deal:{d.id}", "delivery", at(d.next, c.now), f"DELIVERY FROM {_name(c, d.world)}",
                                   f"{d.amount} {d.goods.upper()} · {d.left} TO COME", (1, d.next),
                                   reason="NOTHING TO CANCEL: A TRADE PARTNER'S DELIVERY",
                                   brief=f"DELIVERY {_name(c, d.world)} {at(d.next, c.now)}"))
    # every key is public: the event heap's order (the rolled times) must never decide a tie
    return sorted(items, key=lambda i: (i.sort, i.id))


def _find(c: Campaign, item_id: str) -> Event | None:
    return next((e for e in gate_order(c) if dial_id(e) == item_id), None)


def _refusal(c: Campaign, item_id: str, why: Callable[[QueueItem], str]) -> str:
    item = next((i for i in view(c) if i.id == item_id), None)
    return why(item) if item is not None else GONE


def _undo(c: Campaign, d: dict) -> list[str]:
    """Take back what queuing the dial-out did (engine._launch, assign, _missed_order)."""
    op = d["op"]
    if op in ("malp", "uav"):
        return rules.stow(c, op)                     # back into stores
    m = c.mission(d["mission"])
    if op == "depart":                               # it never left: no mission at all
        tm = c.teams.get(m.team)
        if tm is not None:
            tm.status, tm.where, tm.mission = "base", "", None
        m.state = "cancelled"
        c.record["missions"] -= 1
        return [f"{m.team} STANDING BY AT BASE"]
    lines = rules.withdraw_search(c, d)
    if m.state == "active":                          # the missing team is left to the 12-hour wait
        c.events.push(c.now + WAIT_HOURS * HOUR, "overdue", {"mission": m.id})
        lines.append(f"WAITING {WAIT_HOURS} HOURS FOR {m.team}")
    return lines


def cancel(c: Campaign, item_id: str, confirm: bool) -> tuple[str, list[str], bool]:
    """Cancel a dial-out waiting for the gate: (message, log lines from undoing it, done). Without confirm it
    only asks; a row that can't be cancelled answers with why."""
    ev = _find(c, item_id)
    if ev is None:
        return _refusal(c, item_id, lambda i: i.reason), [], False
    if ev.data["op"] == "uplink":
        return COLLECTING, [], False
    said = _words(c, ev)[1]
    if not confirm:
        return f"CANCEL THE {said}?  x AGAIN TO CONFIRM", [], False
    c.events.remove(lambda e: e is ev)
    return f"CANCELLED: {said}", _undo(c, ev.data), True


def move(c: Campaign, item_id: str, delta: int) -> tuple[str, bool]:
    """Move a waiting dial-out one place up (delta < 0) or down the gate queue: (message, done)."""
    dials = gate_order(c)
    ids = [dial_id(e) for e in dials]
    if item_id not in ids:
        return _refusal(c, item_id, lambda i: NOT_MOVABLE), False
    i = ids.index(item_id)
    j = i + (1 if delta > 0 else -1)
    if j < 0:
        return "ALREADY FIRST IN THE GATE QUEUE", False
    if j >= len(dials):
        return "ALREADY LAST IN THE GATE QUEUE", False
    c.events.swap(dials[i], dials[j])
    return f"{_words(c, dials[i])[1]}: NOW {j + 1} IN THE GATE QUEUE", True
