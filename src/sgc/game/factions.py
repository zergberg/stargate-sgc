"""Goa'uld attention and ally trust: stages, words, and when a Goa'uld next acts against Earth.

Attention is hidden: nothing here gives the player a number, and a faction is named in log lines and texts only
once the SGC knows it (know(), from a "reveal faction" effect)."""
from __future__ import annotations

import random

from .clock import DAY, HOUR
from .state import Campaign, Faction
from .world import ALLY_NAMES, FACTION_IDS, FACTION_KIND, faction_id, faction_name

STAGES = (("unaware", 0), ("curious", 20), ("hostile", 50), ("seeking", 80))
STAGE_NAMES = tuple(name for name, _ in STAGES)
GAIN = {"recruit": 0.75, "officer": 1.0, "commander": 1.25}       # attention gains, by difficulty
DECAY = {"recruit": 2, "officer": 1, "commander": 1}              # attention lost per quiet game day
EVERY = {"curious": (72, 120), "hostile": (36, 72), "seeking": (18, 36)}   # game hours between actions
TEMPO = {"recruit": 1.25, "officer": 1.0, "commander": 0.8}       # scales those intervals
WORDS = {"unaware": "shows no sign of knowing about Earth", "curious": "knows our gate address",
         "hostile": "has put a price on the SG teams", "seeking": "means to destroy Earth"}
TRUST_WORDS = ((75, "allied"), (50, "friendly"), (25, "cautious"), (0, "wary"))
DEFCON = {"unaware": 5, "curious": 4, "hostile": 3, "seeking": 2}
UNKNOWN = {"goauld": "a Goa'uld", "ally": "strangers"}
GOAULD = tuple(f for f in FACTION_IDS if FACTION_KIND[f] == "goauld")
ALLIES = tuple(ALLY_NAMES)


def stage(f: Faction) -> str:
    return next(name for name, low in reversed(STAGES) if f.attention >= low)


def stage_of(c: Campaign, fid: str) -> str:
    return stage(c.factions[fid])


def trust_word(f: Faction) -> str:
    return next(word for low, word in TRUST_WORDS if f.trust >= low)


def display(c: Campaign, fid: str) -> str:
    """How texts name a faction: its name once the SGC knows it, otherwise 'a Goa'uld' (or 'strangers')."""
    f = c.factions[fid]
    return faction_name(fid) if f.known else UNKNOWN[f.kind]


def bind(c: Campaign, fid: str) -> dict[str, str]:
    """Scenario bindings for a faction: {faction} for texts, faction_id for effects and conditions."""
    return {"faction": display(c, fid), "faction_id": fid}


def words(c: Campaign, fid: str) -> str:
    """The Database's words for a known faction: a Goa'uld's stage, or an ally's trust."""
    f = c.factions[fid]
    return WORDS[stage(f)] if f.kind == "goauld" else trust_word(f)


def owner_of(c: Campaign, wid: str) -> str | None:
    """The faction id of the Goa'uld holding this world (a hidden trait), or None."""
    w = c.worlds.get(wid) or c.unlisted.get(wid)
    return faction_id(w.owner) if w is not None else None


def _headline(fid: str, text: str) -> str:
    return f"INTEL: {faction_name(fid).upper()} {text.upper()}"


def know(c: Campaign, fid: str, source: str) -> list[str]:
    """The SGC learns of a faction; returns the log line the first time."""
    f = c.factions[fid]
    if f.known:
        return []
    f.known, f.source = True, source
    return [f"NEW FACTION ON FILE: {faction_name(fid).upper()} — FROM {source.upper()}"]


def adjust_attention(c: Campaign, fid: str, delta: int) -> list[str]:
    """Raise or lower a Goa'uld's attention (gains scaled by difficulty, clamped to 0-100). A new stage makes
    sure an action is pending, and is logged only if the SGC knows this Goa'uld."""
    f = c.factions[fid]
    if f.kind != "goauld" or delta == 0:
        return []
    before = stage(f)
    if delta > 0:
        delta = max(1, int(delta * GAIN[c.difficulty] + 0.5))
        f.quiet_since = c.now
    f.attention = max(0, min(100, f.attention + delta))
    after = stage(f)
    if after == before:
        return []
    if STAGE_NAMES.index(after) > STAGE_NAMES.index(before):
        # A pending action drawn at the old, slower stage may be due later than the new stage allows: redraw it.
        latest = c.now + round(EVERY[after][1] * HOUR * TEMPO[c.difficulty])
        c.events.cancel(lambda e: e.kind == "faction_action" and e.data.get("faction") == fid and e.due > latest)
    ensure_action(c, fid)
    return [_headline(fid, WORDS[after])] if f.known else []


def adjust_trust(c: Campaign, fid: str, delta: int) -> list[str]:
    """Raise or lower an ally's trust (0-100); a new word is logged once the ally is known."""
    f = c.factions[fid]
    if f.kind != "ally" or delta == 0:
        return []
    before = trust_word(f)
    f.trust = max(0, min(100, f.trust + delta))
    after = trust_word(f)
    if after == before or not f.known:
        return []
    return [f"{faction_name(fid).upper()}: NOW {after.upper()}"]


def _pending(c: Campaign, fid: str) -> bool:
    return bool(c.events.find(lambda e: e.kind == "faction_action" and e.data.get("faction") == fid))


def _interval(c: Campaign, fid: str, rng: random.Random) -> int:
    lo, hi = EVERY[stage_of(c, fid)]
    return max(HOUR, round(rng.randint(lo, hi) * HOUR * TEMPO[c.difficulty]))


def ensure_action(c: Campaign, fid: str) -> None:
    """A Goa'uld that is Curious or worse always has one action pending. Seeded from the campaign, so rules
    (which have no rng) stay deterministic."""
    if stage_of(c, fid) == "unaware" or _pending(c, fid):
        return
    rng = random.Random(f"{c.seed}/{fid}/{c.now}")
    c.events.push(c.now + _interval(c, fid, rng), "faction_action", {"faction": fid})


def schedule_next(c: Campaign, fid: str, rng: random.Random) -> None:
    """After an action: the next one, from the engine's rng, unless they've lost interest."""
    c.events.cancel(lambda e: e.kind == "faction_action" and e.data.get("faction") == fid)
    if stage_of(c, fid) != "unaware":
        c.events.push(c.now + _interval(c, fid, rng), "faction_action", {"faction": fid})


def decay(c: Campaign) -> list[str]:
    """Midnight: every Goa'uld whose attention hasn't risen for a whole game day loses some."""
    out = []
    for fid in GOAULD:
        f = c.factions[fid]
        if f.attention <= 0 or c.now - f.quiet_since < DAY:
            continue
        before = stage(f)
        f.attention = max(0, f.attention - DECAY[c.difficulty])
        if stage(f) != before and f.known:
            out.append(_headline(fid, WORDS[stage(f)]))
    return out


def threat(c: Campaign) -> int:
    """DEFCON from what the SGC knows: 5 calm, 4 a known Goa'uld is curious, 3 hostile, 2 seeking or an
    arc's endgame deadline running. (An alarm is shown on top of this by the screens.)"""
    level = min([5, *(DEFCON[stage(c.factions[fid])] for fid in GOAULD if c.factions[fid].known)])
    if any(a.state == "active" and a.deadline is not None for a in c.arcs.values()):
        level = 2
    return level
