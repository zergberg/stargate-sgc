"""Arcs: canon story threads with ordered stages. The definitions are here; their scenarios are TOML files with
kind = "arc", naming the arc and the stage they play at.

An arc sleeps until content starts it ("arc <id> start"). A stage may name step_hours: an arc_step event then
plays that stage's arc scenario that long after the stage begins. An arc with an endgame jumps to it when its
Goa'uld's attention reaches 80, or when content advances into it; the endgame's step fires at a deadline the
ARCS tab shows. Arcs only run in Campaign mode."""
from __future__ import annotations

from dataclasses import dataclass

from . import factions
from .clock import HOUR, short
from .state import ARC_IDS, Campaign
from .world import World, faction_name, place_id

ENDGAME_ATTENTION = 80


@dataclass(frozen=True)
class Stage:
    text: str                          # the ARCS tab's line while the arc is at this stage
    log: str                           # the log's short phrase on entering it (the log box clips past 66)
    step_hours: int | None = None      # an arc_step plays this stage's scenario this long after it begins


@dataclass(frozen=True)
class Arc:
    id: str
    title: str
    major: bool                        # victory needs at least one major arc resolved
    faction: str                       # the faction at its heart
    world: str                         # the canon or arc world its scenarios bind as {world}
    stages: tuple[Stage, ...]
    endgame: int | None = None         # the stage reached when the faction's attention hits 80
    countdown: int = 0                 # game hours from the endgame's start to its arc_step
    catastrophe: str | None = None     # failing the arc ends the campaign with this


ARCS: dict[str, Arc] = {a.id: a for a in (
    Arc("apophis", "Apophis and Chulak", True, "apophis", "Chulak", (
        Stage("Chulak is Apophis's garrison world. A raid could reach the prisoners.",
              "a raid could reach the prisoners"),
        Stage("Teal'c has turned on Apophis. Apophis will want revenge.", "Teal'c has turned on Apophis",
              step_hours=48),
        Stage("Apophis is gathering his fleet. Raids on Chulak slow him; allies can warn us.",
              "Apophis is gathering his fleet"),
        Stage("Two ha'taks are on course for Earth. Strike at Chulak before they arrive.",
              "two ha'taks on course for Earth"),
    ), endgame=4, countdown=120,
        catastrophe="Apophis's ha'taks reached Earth orbit, and the SGC had no answer."),
    Arc("thor", "Cimmeria and Thor's Hammer", False, "asgard", "Cimmeria", (
        Stage("The Cimmerians speak of Thor, whose Hammer guards them. The ruins beneath it want studying.",
              "Thor's Hammer guards Cimmeria"),
        Stage("A hologram of Thor spoke to our team. The Asgard have noticed us.", "the Asgard have noticed us",
              step_hours=72),
        Stage("Heru'ur has come to Cimmeria. Find Thor's chariot before the world falls.",
              "Heru'ur has come to Cimmeria", step_hours=168),
    )),
    Arc("tokra", "The Tok'ra", False, "tokra", "Vorash", (
        Stage("A prisoner whispered of the Tok'ra, and gave an address. Someone should go and talk.",
              "a prisoner gave us an address"),
        Stage("The Tok'ra are wary. They will ask something of us.", "the Tok'ra are wary", step_hours=48),
        Stage("The Tok'ra are weighing an alliance. Keep our promises.", "an alliance is in reach",
              step_hours=336),
    )),
)}
assert set(ARCS) == set(ARC_IDS), "state.ARC_IDS and arcs.ARCS must name the same arcs"


def _cancel_steps(c: Campaign, aid: str) -> None:
    c.events.cancel(lambda e: e.kind == "arc_step" and e.data.get("arc") == aid)


def _enter(c: Campaign, arc: Arc, n: int) -> None:
    st = c.arcs[arc.id]
    st.stage = n
    _cancel_steps(c, arc.id)
    if arc.endgame == n:
        st.deadline = c.now + arc.countdown * HOUR
        c.events.push(st.deadline, "arc_step", {"arc": arc.id, "stage": n})
    elif (hours := arc.stages[n - 1].step_hours) is not None:
        c.events.push(c.now + hours * HOUR, "arc_step", {"arc": arc.id, "stage": n})


def _line(arc: Arc, n: int) -> str:
    """The log's line for a stage: the title and a short phrase; the ARCS tab has the full text (stage_text)."""
    return f"{arc.title.upper()}: {arc.stages[n - 1].log.upper()}"


def names_faction(arc: Arc) -> bool:
    """The arc's title names its faction, so the SGC knows the faction the moment the arc starts."""
    name = faction_name(arc.faction).lower().removeprefix("the ")
    return name in arc.title.lower()


def start(c: Campaign, aid: str) -> list[str]:
    """Wake a dormant arc at stage 1 (its first clue has been found)."""
    st = c.arcs.get(aid)
    if st is None or st.state != "dormant":
        return []
    arc = ARCS[aid]
    st.state, st.started = "active", c.now
    _enter(c, arc, 1)
    known = factions.know(c, arc.faction, "a new lead") if names_faction(arc) and arc.faction in c.factions else []
    return [f"NEW LEAD: {arc.title.upper()}", *known, *on_attention(c, arc.faction)]


def advance(c: Campaign, aid: str) -> list[str]:
    st = c.arcs.get(aid)
    if st is None or st.state != "active" or st.stage >= len(ARCS[aid].stages):
        return []
    arc = ARCS[aid]
    _enter(c, arc, st.stage + 1)
    return [_line(arc, st.stage)]


def _end(c: Campaign, aid: str, state: str) -> bool:
    st = c.arcs.get(aid)
    if st is None or st.state != "active":
        return False
    st.state, st.ended, st.deadline = state, c.now, None
    _cancel_steps(c, aid)
    return True


def resolve(c: Campaign, aid: str) -> list[str]:
    if not _end(c, aid, "resolved"):
        return []
    c.ledger["arcs"] += 1
    return [f"RESOLVED: {ARCS[aid].title.upper()}"]


def fail(c: Campaign, aid: str) -> list[str]:
    """The arc is lost. A catastrophe ends the campaign (the engine sees c.over)."""
    if not _end(c, aid, "failed"):
        return []
    arc = ARCS[aid]
    if arc.catastrophe:
        c.over, c.ending = arc.catastrophe, "fallen"
    return [f"FAILED: {arc.title.upper()}"]


def on_attention(c: Campaign, fid: str) -> list[str]:
    """A Goa'uld's attention has changed: an active arc of theirs at 80 or more jumps to its endgame."""
    out = []
    if c.factions[fid].attention < ENDGAME_ATTENTION:
        return out
    for arc in ARCS.values():
        st = c.arcs.get(arc.id)
        if st is not None and st.state == "active" and arc.faction == fid and arc.endgame \
                and st.stage < arc.endgame:
            _enter(c, arc, arc.endgame)
            out.append(_line(arc, arc.endgame))
    return out


def stage_text(c: Campaign, aid: str) -> str:
    """The ARCS tab's words for where an arc stands."""
    st, arc = c.arcs.get(aid), ARCS[aid]
    if st is None:                     # Sandbox has no arcs
        return ""
    if st.state in ("resolved", "failed"):
        return f"{st.state.capitalize()}."
    if st.state == "dormant":
        return ""
    text = arc.stages[st.stage - 1].text
    return f"{text} Due {short(st.deadline)}." if st.deadline is not None else text


def arc_world(c: Campaign, aid: str) -> World | None:
    """The world an arc's scenarios bind as {world}: on the dialing list, or still unlisted."""
    wid = place_id(ARCS[aid].world)
    return c.worlds.get(wid) or c.unlisted.get(wid)
