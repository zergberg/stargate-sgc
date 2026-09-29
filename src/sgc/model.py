"""Scene state shared by the director (writer) and the renderers/panels (readers)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .addresses import Address


@dataclass
class Figure:
    kind: str            # "person" | "malp" | "crate"
    pos: float           # 0 = front of the ramp (near the viewer) .. 1 = at the event horizon
    lane: float = 0.0    # -1..1 sideways offset on the ramp
    alpha: float = 1.0


def _teams() -> dict[str, str]:
    return {"SG-1": "AT BASE", "SG-2": "AT BASE", "SG-3": "AT BASE", "SG-4": "AT BASE"}


@dataclass
class Scene:
    ring_angle: float = 0.0              # degrees, clockwise
    spinning: bool = False
    lit: set[int] = field(default_factory=set)   # chevron positions 0..8, 0 = top, clockwise
    clamp: float = 0.0                   # master chevron travel 0..1
    horizon: str = "off"                 # off | kawoosh | open | collapse
    horizon_p: float = 0.0               # progress within kawoosh / collapse
    open_elapsed: float = 0.0            # seconds the wormhole has been open
    iris: float = 0.0                    # 0 open .. 1 closed
    figures: list[Figure] = field(default_factory=list)
    splashes: list[list[float]] = field(default_factory=list)   # [x, y, age 0..1], x/y in -1..1 of the horizon
    impacts: list[list[float]] = field(default_factory=list)    # [x, y, intensity 0..1]
    vaporize: float = 0.0                # kawoosh-hazard flash 0..1
    alert: str = "normal"                # normal | incoming | red
    address: Address | None = None
    locked: int = 0
    incoming: bool = False
    identified: bool = True
    status: str = "STANDING BY"
    panel_title: str = "SCIENCE"
    panel_rows: list[tuple[str, str]] = field(default_factory=list)
    panel_trace: list[float] = field(default_factory=list)       # 0..1 values, newest last
    power: float = 1.0
    teams: dict[str, str] = field(default_factory=_teams)
    team_locations: dict[str, Address] = field(default_factory=dict)   # where offworld teams went
    dim: float = 0.0                     # exit fade 0..1
    blank_panels: int = 0                # exit: side screens blanked so far
    collapse_line: float = 0.0           # exit CRT collapse 0..1


@dataclass
class Step:
    duration: float
    update: Callable[[Scene, float], None] | None = None   # called with progress p in [0, 1]
    log: str | None = None                                  # emitted when the step starts
    cues: tuple[str, ...] = ()                              # "cue", "loop:cue", "stop:cue", "stopall"
