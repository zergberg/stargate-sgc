"""Team traffic: an SG team walks up the ramp and steps through the event horizon."""
from __future__ import annotations

from .. import sequences as sq
from ..model import Figure
from . import EventContext, EventDef
from .common import Splashes, cleanup, start_outgoing, timed

LANES = (-0.45, -0.15, 0.15, 0.45)
WALK, STAGGER = 5.5, 1.2


def build(ctx: EventContext) -> list:
    at_base = [t for t, v in ctx.scene.teams.items() if v == "AT BASE"]
    if not at_base:
        from . import malp
        return malp.build(ctx)
    addr, team = ctx.addr, ctx.rng.choice(at_base)
    splashes = Splashes()
    entered: set[int] = set()

    def walk(scene, t):
        figs = []
        for i, lane in enumerate(LANES):
            pos = max(0.0, (t - i * STAGGER) / WALK)
            if pos < 1:
                figs.append(Figure("person", pos, lane))
            elif i not in entered:
                entered.add(i)
                splashes.add(lane * 0.35, 0.75, t)
        scene.figures = figs
        splashes.apply(scene, t)
        if t >= WALK + STAGGER * 3:
            scene.teams[team] = f"OFFWORLD {addr.label.upper()}"
            scene.team_locations[team] = addr

    dial, _ = sq.dial(addr, ctx.ring_angle, ctx.speed)
    walk_time = WALK + STAGGER * 3 + 1.0
    hold = max(0.0, 30 * ctx.open_scale - walk_time)
    return [start_outgoing(addr), *dial, *sq.kawoosh(ctx.speed),
            *timed(walk_time, walk, [(0.5, f"{team} DEPARTING FOR {addr.label.upper()}", ())]),
            *timed(hold, None, [(0, f"{team} TRANSIT COMPLETE", ())]),
            *sq.shutdown(ctx.speed), cleanup()]


EVENT = EventDef("traffic", "outgoing", build)
