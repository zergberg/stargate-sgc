"""Kawoosh hazard: something left on the ramp gets vaporized by the unstable vortex."""
from __future__ import annotations

from .. import sequences as sq
from ..model import Figure, Step
from . import EventContext, EventDef
from .common import cleanup, start_outgoing, timed

ITEMS = ["SUPPLY CRATE", "EQUIPMENT CART", "SPARE MALP WHEEL", "SOMEONE'S LUNCH"]


def build(ctx: EventContext) -> list:
    addr, item = ctx.addr, ctx.rng.choice(ITEMS)
    burst, settle = sq.kawoosh(ctx.speed)
    burst_update, settle_update = burst.update, settle.update

    def place_crate(scene, p):
        scene.figures = [Figure("crate", 0.93, 0.25)]

    def burst2(scene, p):
        burst_update(scene, p)
        if p >= 0.35:
            scene.figures = [f for f in scene.figures if f.kind != "crate"]
            scene.vaporize = 1.0 if p < 0.5 else 1.0 - 0.7 * (p - 0.5) / 0.5
    burst.update = burst2

    def settle2(scene, p):
        settle_update(scene, p)
        scene.vaporize = 0.3 * (1 - p)
    settle.update = settle2

    dial, _ = sq.dial(addr, ctx.ring_angle, ctx.speed)
    hold = 10 * ctx.open_scale
    return [start_outgoing(addr), Step(0, place_crate, f"{item} LEFT ON THE RAMP"), *dial, burst, settle,
            *timed(hold, None, [(1.0, f"{item} VAPORIZED — AND THAT'S WHY WE STAND BACK", ())]),
            *sq.shutdown(ctx.speed), cleanup()]


EVENT = EventDef("kawoosh_hazard", "outgoing", build)
