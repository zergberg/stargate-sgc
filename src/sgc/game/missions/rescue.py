"""Rescuing a captured team: the planner's top priority, and the one mission type whose target is a team
name, not a drone."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import MissionType

if TYPE_CHECKING:
    from ..engine import Engine
    from ..state import Mission


def target(engine: "Engine", wid: str) -> str | None:
    """Who a rescue is for: the first captured team on this world not already targeted by an active
    rescue."""
    from ..state import team_names               # function-local: avoids a state <-> missions import cycle
    c = engine.c
    taken = [m.target for m in c.active_missions() if m.world == wid and m.type == "rescue"]
    return next((n for n in team_names(c) if c.teams[n].status == "captured" and c.teams[n].where == wid
                 and n not in taken), None)


def bind(mission: "Mission", team: str) -> dict:
    return {"captive": mission.target} if mission.target else {}


def assign_label(target: str) -> str | None:
    return f"RESCUE {target}"


TYPE = MissionType(name="rescue", hours=20, exact_scenarios=True, planner_score=50, target=target,
                    bind=bind, assign_label=assign_label)
