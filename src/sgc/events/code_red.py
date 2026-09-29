"""Code Red: unscheduled offworld activation, no IDC; the iris closes and takes impacts."""
from __future__ import annotations

from .. import sequences as sq
from ..model import Step
from . import EventContext, EventDef
from .common import cleanup, timed

IMPACT_LIFE = 0.4


def iris_move(close: bool, log: str, cue: str) -> Step:
    def update(scene, p):
        scene.iris = p if close else 1 - p
    return Step(1.2, update, log, (cue,))


def build(ctx: EventContext) -> list:
    rng = ctx.rng
    hold = 20 * ctx.open_scale
    hits = sorted(rng.uniform(2, hold - 2) for _ in range(rng.randint(4, 7)))
    spots = [(rng.uniform(-0.6, 0.6), rng.uniform(-0.6, 0.6)) for _ in hits]

    def red(scene, p):
        scene.alert, scene.status = "red", "CODE RED"

    def opened(scene, p):
        scene.horizon, scene.open_elapsed = "open", 0.0

    def impacts(scene, t):
        scene.impacts = [[x, y, 1 - (t - h) / IMPACT_LIFE] for (x, y), h in zip(spots, hits) if 0 <= t - h < IMPACT_LIFE]
        scene.status = "IRIS HOLDING"
        if t >= hold:
            scene.impacts = []

    marks = [(h, "IMPACTS ON THE IRIS" if i == 0 else None, ("iris_impact",)) for i, h in enumerate(hits)]

    def calm(scene, p):
        scene.alert = "normal"
    return [*sq.incoming(7, ctx.speed),
            Step(0, red, "CODE RED — NO IDC RECEIVED"),
            iris_move(True, "IRIS CLOSING", "iris_close"),
            Step(0, opened, "WORMHOLE ESTABLISHED — IRIS HOLDING", ("loop:wormhole_hum",)),
            *timed(hold, impacts, marks),
            *sq.shutdown(ctx.speed),
            Step(0, calm, cues=("stop:klaxon",)),
            iris_move(False, "OPENING THE IRIS", "iris_open"),
            cleanup()]


EVENT = EventDef("code_red", "incoming", build)
