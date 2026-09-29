"""Failed dial: the final chevron will not lock and the dial is aborted."""
from __future__ import annotations

import math

from .. import sequences as sq
from ..model import Step
from . import EventContext, EventDef
from .common import cleanup, start_outgoing


def build(ctx: EventContext) -> list:
    addr = ctx.addr
    n = addr.chevrons
    steps, angle = sq.dial(addr, ctx.ring_angle, ctx.speed, glyphs=n - 1)
    last_spin, _ = sq.spin(angle, addr.full[-1], n - 1, ctx.speed)
    final_pos = sq.LOCK_ORDER[n][-1]
    locked_order = sq.LOCK_ORDER[n][:n - 1]

    def flicker(scene, p):
        scene.spinning = False
        on = int(p * 12) % 2 == 0 and p < 0.9
        scene.clamp = 0.5 * abs(math.sin(p * math.pi * 3))
        if on:
            scene.lit.add(final_pos)
        else:
            scene.lit.discard(final_pos)
        scene.status = f"CHEVRON {n} WILL NOT LOCK"

    def dark(scene, p):
        scene.clamp = 0.0
        keep = math.ceil(len(locked_order) * (1 - p))
        scene.lit = set(locked_order[:keep])
        if p >= 1:
            scene.locked = 0
    return [start_outgoing(addr), *steps, last_spin,
            Step(1.6, flicker, f"CHEVRON {n} WILL NOT LOCK", ("stop:ring_spin", "chevron_lock")),
            Step(1.0, dark, "DIAL ABORTED", ("dial_fail",)),
            cleanup()]


EVENT = EventDef("failed_dial", "outgoing", build)
