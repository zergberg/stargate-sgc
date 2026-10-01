"""Recovering a captured drone (MALP or UAV) the SGC knows the location of."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import MissionType

if TYPE_CHECKING:
    from ..engine import Engine


def target(engine: "Engine", wid: str) -> str | None:
    """What a recovery is after: a located, captured drone on this world not already targeted."""
    c = engine.c
    taken = [m.target for m in c.active_missions() if m.world == wid and m.type == "recover"]
    held = [d.drone for d in c.captured_drones if d.world == wid and d.located]
    for drone in taken:
        if drone in held:
            held.remove(drone)
    return held[0] if held else None


def assign_label(target: str) -> str | None:
    return f"RECOVER THE {target.upper()}"


TYPE = MissionType(name="recover", hours=12, exact_scenarios=True, planner_score=15, target=target,
                    assign_label=assign_label)
