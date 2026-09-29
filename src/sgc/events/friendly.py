"""Friendly incoming: an IDC is received, the iris opens and travellers arrive."""
from __future__ import annotations

from .. import sequences as sq
from ..addresses import Address, load_canon
from ..model import Figure, Step
from . import EventContext, EventDef
from .code_red import iris_move
from .common import Splashes, cleanup, timed

ALLIES = {"TOK'RA": "Revanna", "TOLLAN": "Tollana", "JAFFA REBELLION": "Dakara"}
WALK, STAGGER = 5.0, 1.0


def build(ctx: EventContext) -> list:
    rng, scene0 = ctx.rng, ctx.scene
    away = [t for t, v in scene0.teams.items() if v.startswith("OFFWORLD")]
    canon = {a.name: a for a in load_canon()}
    if away:
        who = rng.choice(away)
        origin = scene0.team_locations.get(who) or Address("UNKNOWN", "", (), False)
        lanes = (-0.45, -0.15, 0.15, 0.45)
    else:
        who = rng.choice(sorted(ALLIES))
        origin = canon.get(ALLIES[who]) or Address("UNKNOWN", "", (), False)
        lanes = (-0.15, 0.15)
    n = origin.chevrons if origin.glyphs else 7
    splashes = Splashes()
    out: set[int] = set()

    def opened(scene, p):
        scene.horizon, scene.open_elapsed = "open", 0.0

    def idc(scene, p):
        scene.identified, scene.alert = True, "normal"
        scene.address = origin if origin.glyphs else None
        scene.status = f"IDC: {who}"

    def arrive(scene, t):
        figs = []
        for i, lane in enumerate(lanes):
            start = i * STAGGER
            if t < start:
                continue
            if i not in out:
                out.add(i)
                splashes.add(lane * 0.35, 0.75, t)
            pos = 1 - (t - start) / WALK
            if pos > 0:
                figs.append(Figure("person", pos, lane))
        scene.figures = figs
        splashes.apply(scene, t)
        if t >= WALK + STAGGER * (len(lanes) - 1) and who in scene.teams:
            scene.teams[who] = "AT BASE"
            scene.team_locations.pop(who, None)

    walk_time = WALK + STAGGER * (len(lanes) - 1) + 0.5
    return [*sq.incoming(n, ctx.speed),
            iris_move(True, "IRIS CLOSING", "iris_close"),
            Step(0, opened, cues=("loop:wormhole_hum",)),
            Step(3.0, None),
            Step(0, idc, f"IDC RECEIVED — {who}", ("idc_accept", "stop:klaxon")),
            iris_move(False, "OPENING THE IRIS", "iris_open"),
            *timed(walk_time, arrive, [(0, f"{who} ARRIVING", ())]),
            Step(8 * ctx.open_scale, None),
            *sq.shutdown(ctx.speed), cleanup()]


EVENT = EventDef("friendly", "incoming", build)
