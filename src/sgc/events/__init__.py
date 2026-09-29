"""Event registry: each event builds the timeline for one gate cycle."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from ..addresses import Address
from ..model import Scene, Step


@dataclass
class EventContext:
    addr: Address | None
    rng: random.Random
    scene: Scene
    speed: float = 1.0
    open_scale: float = 1.0
    ring_angle: float = 0.0


@dataclass
class EventDef:
    name: str
    kind: str                                  # "outgoing" | "incoming"
    build: Callable[[EventContext], list[Step]]


REGISTRY: dict[str, EventDef] = {}


def _register() -> None:
    from . import code_red, failed_dial, friendly, kawoosh_hazard, malp, science, traffic
    for mod in (science, traffic, malp, failed_dial, code_red, friendly, kawoosh_hazard):
        REGISTRY[mod.EVENT.name] = mod.EVENT


_register()
