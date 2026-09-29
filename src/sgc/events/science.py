"""Science readout: the gate stays open while instruments stream data from the planet."""
from __future__ import annotations

import math

from .. import sequences as sq
from . import EventContext, EventDef
from .common import Trace, cleanup, start_outgoing, timed

FINDINGS = ["SPECTROMETRY: TRACE NAQUADAH DETECTED", "SPECTROMETRY: HIGH TRINIUM SIGNATURE",
            "SPECTROMETRY: ANCIENT POWER SIGNATURE", "SPECTROMETRY: NO ANOMALIES"]
SAMPLES = ["ATMOSPHERIC SAMPLE: BREATHABLE", "ATMOSPHERIC SAMPLE: HIGH CO2 — MASKS ADVISED",
           "SOIL SAMPLE: ORGANIC COMPOUNDS PRESENT", "SEISMIC: MINOR TREMOR ACTIVITY"]


def build(ctx: EventContext) -> list:
    addr, rng = ctx.addr, ctx.rng
    base = {"grav": rng.uniform(0.7, 1.5), "em": rng.uniform(20, 70), "naq": rng.uniform(0, 900),
            "age": rng.randint(2, 90) * 1000, "freq": rng.uniform(0.6, 1.8)}
    trace = Trace(lambda t: 0.5 + 0.3 * math.sin(t * base["freq"] * 3) + 0.15 * rng.uniform(-1, 1))
    state = {"tick": -1}

    def update(scene, t):
        scene.panel_title = f"SCIENCE · {addr.label.upper()}"
        tick = int(t * 2)
        if tick != state["tick"]:
            state["tick"] = tick
            scene.panel_rows = [
                ("GRAVITY", f"{base['grav'] + rng.uniform(-0.01, 0.01):.2f} G"),
                ("EM FIELD", f"{base['em'] + rng.uniform(-2, 2):5.1f} uT"),
                ("NAQUADAH", f"{base['naq'] + rng.uniform(-5, 5):5.0f} ppm"),
                ("SURFACE AGE", f"~{base['age']:,} YRS"),
                ("UPLINK", f"{min(100, int(t / 40 * 100 / ctx.open_scale)):3d} %"),
            ]
        trace.apply(scene, t)

    dial, _ = sq.dial(addr, ctx.ring_angle, ctx.speed)
    hold = 40 * ctx.open_scale
    marks = [(0, "SCIENCE TEAM UPLINK ACTIVE", ()), (5 * ctx.open_scale, rng.choice(FINDINGS), ()),
             (18 * ctx.open_scale, rng.choice(SAMPLES), ()), (30 * ctx.open_scale, "DATA UPLINK COMPLETE", ())]
    return [start_outgoing(addr), *dial, *sq.kawoosh(ctx.speed), *timed(hold, update, marks),
            *sq.shutdown(ctx.speed), cleanup()]


EVENT = EventDef("science", "outgoing", build)
