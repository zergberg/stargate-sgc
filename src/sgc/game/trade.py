"""Trade deals: naquadah through the gate every few days, until the deal runs out or the route is cut."""
from __future__ import annotations

from .clock import HOUR
from .state import Campaign, Deal
from .world import faction_id

EVERY = 72 * HOUR               # between deliveries
DELIVERIES = 6
MAX_MISSES = 2                  # disruptions in a row that cut the route
HOSTILE = 50                    # factions.STAGES' Hostile threshold (a rules test checks they agree)


def new_deal(c: Campaign, wid: str, goods: str, amount: int) -> list[str]:
    """A deal (or a renewal of the one already running with this world)."""
    w = c.worlds[wid]
    for d in c.deals:
        if d.world == wid and d.state == "active":
            d.left, d.amount, d.misses = DELIVERIES, max(d.amount, amount), 0
            return [f"TRADE WITH {w.name.upper()} RENEWED"]
    did = max((d.id for d in c.deals), default=0) + 1
    d = Deal(did, wid, goods, amount, c.now + EVERY, DELIVERIES)
    c.deals.append(d)
    c.events.push(d.next, "trade_delivery", {"deal": did})
    return [f"TRADE DEAL: {amount} {goods.upper()} FROM {w.name.upper()} EVERY {EVERY // HOUR} HOURS"]


def disruption(c: Campaign, d: Deal) -> int:
    """The % chance a delivery is lost. Uses hidden traits: never show it."""
    w = c.worlds[d.world]
    p = 5 + (10 if w.danger >= 2 else 0) + (10 if w.status == "hostile" else 0)
    fid = faction_id(w.owner)
    if fid is not None and c.factions[fid].attention >= HOSTILE:
        p += 15
    return p


def deliver(c: Campaign, did: int, roll: float) -> tuple[list[str], bool]:
    """A delivery falls due; roll is the engine's 0-100 draw. Returns (log lines, whether it arrived)."""
    d = c.deal(did)
    if d is None or d.state != "active":
        return [], False
    w = c.worlds[d.world]
    if w.status == "lost":
        d.state = "cut"
        return [f"{w.name.upper()} IS LOST — THE TRADE ROUTE IS CUT"], False
    d.left -= 1
    if roll < disruption(c, d):
        d.misses += 1
        lines, arrived = [f"DELIVERY FROM {w.name.upper()} DID NOT ARRIVE"], False
        if d.misses >= MAX_MISSES:
            d.state = "cut"
            lines.append(f"THE TRADE ROUTE TO {w.name.upper()} IS CUT")
    else:
        d.misses = 0
        c.naquadah += d.amount
        lines, arrived = [f"DELIVERY FROM {w.name.upper()}: {d.amount} NAQUADAH"], True
    if d.state == "active":
        if d.left <= 0:
            d.state = "ended"
            lines.append(f"THE DEAL WITH {w.name.upper()} HAS RUN ITS COURSE")
        else:
            d.next = c.now + EVERY
            c.events.push(d.next, "trade_delivery", {"deal": did})
    return lines, arrived


def risk_words(c: Campaign, d: Deal) -> str:
    """The TRADE tab's risk, from what the SGC knows only: the world's status (hostile or lost), a known owner's stage (once the
    owner is on file), and recent disruptions."""
    if d.state != "active":
        return "—"
    w = c.worlds[d.world]
    fid = faction_id(w.seen.get("owner"))
    known_hostile = fid is not None and c.factions[fid].known and c.factions[fid].attention >= HOSTILE
    if w.status in ("hostile", "lost") or known_hostile:
        return "HIGH"
    return "RAISED" if d.misses else "LOW"
