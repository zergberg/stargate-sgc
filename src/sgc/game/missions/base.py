"""The MissionType dataclass: everything the engine special-cases per mission type, gathered into one
registry entry instead of scattered across engine.py, state.py, room.py and planner.py."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..engine import Engine
    from ..state import Mission


def NO_TARGET(engine: "Engine", wid: str) -> str | None:
    """The default `target`: this mission type never has one (not a rescue or a recovery)."""
    return None


def _no_bind(mission: "Mission", team: str) -> dict:
    return {}


def _no_label(target: str) -> str | None:
    return None


@dataclass(frozen=True)
class MissionType:
    name: str
    hours: int
    needs: str | None = None
    ends_as: str = "surveyed"                 # "contact" or "surveyed"
    exact_scenarios: bool = False
    planner_score: int = 0
    target: Callable[["Engine", str], "str | None"] = NO_TARGET
    bind: Callable[["Mission", str], dict] = _no_bind
    assign_label: Callable[[str], "str | None"] = _no_label
