"""The campaign's score (program growth), Campaign victory, and its entry in the hall of records."""
from __future__ import annotations

from .arcs import ARCS
from .clock import day
from .state import CORE_TEAMS, Campaign

SURVEYED, ALLY, TEAM, MISSION = 10, 50, 20, 2
MAJOR_ARC, MINOR_ARC = 200, 100


def score(c: Campaign) -> int:
    """10 per world surveyed, 50 per ally, 20 per team beyond the first four, 2 per completed mission, and in a
    Campaign 200 per major arc resolved and 100 per minor one."""
    allies = sum(1 for flag in c.inventory if flag.startswith("ally."))
    completed = sum(1 for m in c.missions if m.state == "complete")
    s = (SURVEYED * c.record["surveyed"] + ALLY * allies + TEAM * max(0, len(c.teams) - len(CORE_TEAMS))
         + MISSION * completed)
    return s + sum(MAJOR_ARC if ARCS[aid].major else MINOR_ARC
                   for aid, st in c.arcs.items() if st.state == "resolved")


def victory(c: Campaign) -> bool:
    """Campaign mode, a major arc resolved, and no arc still in play (a failed minor arc doesn't block it)."""
    if c.mode != "campaign" or any(st.state == "active" for st in c.arcs.values()):
        return False
    return any(st.state == "resolved" and ARCS[aid].major for aid, st in c.arcs.items())


def result(c: Campaign) -> str:
    return "victory" if c.won is not None else (c.ending or "overrun")


def record(c: Campaign) -> dict:
    """An entry for Saves.add_record (it adds the date)."""
    return {"mode": c.mode, "difficulty": c.difficulty, "result": result(c), "days": day(c.minutes),
            "surveyed": c.record["surveyed"], "score": score(c)}
