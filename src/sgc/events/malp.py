"""MALP recon: the probe rolls through first and streams telemetry back."""
from __future__ import annotations

import math

from .. import sequences as sq
from ..model import Figure
from . import EventContext, EventDef
from .common import Splashes, Trace, cleanup, start_outgoing, timed

ROLL = 6.0


def build(ctx: EventContext) -> list:
    addr, rng = ctx.addr, ctx.rng
    toxic = rng.random() < 0.3
    o2 = rng.uniform(4, 12) if toxic else rng.uniform(18, 23)
    temp, rad = rng.uniform(-40, 55), rng.uniform(0.05, 3.5 if toxic else 0.8)
    splashes = Splashes()
    trace = Trace(lambda t: 0.5 + 0.35 * math.sin(t * 5) * math.sin(t * 0.7) + 0.1 * rng.uniform(-1, 1))
    state = {"tick": -1, "in": False}

    def roll(scene, t):
        pos = t / ROLL
        if pos < 1:
            scene.figures = [Figure("malp", pos, 0.0)]
        elif not state["in"]:
            state["in"] = True
            scene.figures = []
            splashes.add(0.0, 0.75, t)
        splashes.apply(scene, t)
        scene.panel_title = f"MALP TELEMETRY · {addr.label.upper()}"
        scene.panel_rows = [("STATUS", "IN TRANSIT" if pos < 1 else "SIGNAL ACQUIRED")]

    def telemetry(scene, t):
        tick = int(t * 2)
        if tick != state["tick"]:
            state["tick"] = tick
            scene.panel_rows = [
                ("ATMOS", f"O2 {o2 + rng.uniform(-0.2, 0.2):4.1f} %"),
                ("TEMP", f"{temp + rng.uniform(-0.5, 0.5):5.1f} C"),
                ("RAD", f"{rad + rng.uniform(-0.02, 0.02):4.2f} mSv"),
                ("SIGNAL", f"{rng.randint(82, 99)} %"),
                ("CAMERA", "PANNING"),
            ]
        trace.apply(scene, t)

    verdict = "TOXIC ATMOSPHERE — NO-GO" if toxic else "ATMOSPHERE NOMINAL — CLEARED FOR RECON"
    hold = 25 * ctx.open_scale
    dial, _ = sq.dial(addr, ctx.ring_angle, ctx.speed)
    return [start_outgoing(addr), *dial, *sq.kawoosh(ctx.speed),
            *timed(ROLL + 1.0, roll, [(0.3, "MALP IN TRANSIT", ())]),
            *timed(hold, telemetry, [(0, "MALP TELEMETRY RECEIVED", ()), (hold * 0.7, verdict, ())]),
            *sq.shutdown(ctx.speed), cleanup()]


EVENT = EventDef("malp", "outgoing", build)
