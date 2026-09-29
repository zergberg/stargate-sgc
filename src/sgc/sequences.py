"""Building blocks for gate timelines: dialing, incoming, kawoosh, shutdown, idle, exit."""
from __future__ import annotations

import math

from .addresses import Address
from .model import Scene, Step

LOCK_ORDER = {7: [1, 2, 3, 6, 7, 8, 0], 8: [1, 2, 3, 6, 7, 8, 4, 0], 9: [1, 2, 3, 6, 7, 8, 4, 5, 0]}
SPIN, LOCK = 1.8, 0.6


def angle_for_glyph(g: int) -> float:
    """Ring angle that puts glyph g under the master (top) chevron."""
    return -(g - 1) * 360 / 39


def _smooth(p: float) -> float:
    return p * p * (3 - 2 * p)


def _lock_update(pos: int, k: int, spin_ring: bool = True):
    def update(scene: Scene, p: float) -> None:
        scene.spinning = False
        scene.clamp = math.sin(math.pi * p) if spin_ring and p < 1 else 0.0
        if p >= 0.5:
            scene.lit.add(pos)
            scene.locked = max(scene.locked, k + 1)
    return update


def spin(angle: float, g: int, k: int, speed: float = 1.0) -> tuple[Step, float]:
    """Spin step bringing glyph g under the master chevron; direction alternates with k."""
    target = angle_for_glyph(g)
    direction = 1 if k % 2 == 0 else -1
    delta = (target - angle) % 360 if direction > 0 else -((angle - target) % 360)
    if abs(delta) < 40:
        delta += 360 * direction
    a0, a1 = angle, angle + delta

    def update(scene: Scene, p: float) -> None:
        scene.ring_angle = a0 + (a1 - a0) * _smooth(p)
        scene.spinning = p < 1
        scene.status = f"ENCODING CHEVRON {k + 1}"
    return Step(SPIN / speed, update, cues=("loop:ring_spin",)), a1


def dial(addr: Address, start_angle: float, speed: float = 1.0, glyphs: int | None = None) -> tuple[list[Step], float]:
    """Spin-and-lock steps for the first `glyphs` glyphs of the address (default: all)."""
    steps: list[Step] = []
    angle = start_angle
    order = LOCK_ORDER[addr.chevrons]
    full = addr.full[:glyphs] if glyphs else addr.full
    for k, g in enumerate(full):
        step, angle = spin(angle, g, k, speed)
        steps.append(step)
        last = k == addr.chevrons - 1
        log = f"CHEVRON {k + 1} LOCKED" if last else f"CHEVRON {k + 1} ENCODED"
        steps.append(Step(LOCK / speed, _lock_update(order[k], k), log, ("stop:ring_spin", "chevron_lock")))
    return steps, angle


def incoming(n: int, speed: float = 1.0) -> list[Step]:
    def setup(scene: Scene, p: float) -> None:
        scene.alert, scene.incoming, scene.identified = "incoming", True, False
        scene.address, scene.locked, scene.status = None, 0, "INCOMING WORMHOLE"
    steps = [Step(0, setup, "UNSCHEDULED OFFWORLD ACTIVATION", ("loop:klaxon",))]
    for k, pos in enumerate(LOCK_ORDER[n]):
        steps.append(Step(0.45 / speed, _lock_update(pos, k, spin_ring=False), cues=("chevron_lock",)))
    return steps


def kawoosh(speed: float = 1.0) -> list[Step]:
    def burst(scene: Scene, p: float) -> None:
        scene.horizon, scene.horizon_p = "kawoosh", p
        scene.status = "WORMHOLE ESTABLISHING"

    def settle(scene: Scene, p: float) -> None:
        if scene.horizon != "open":
            scene.horizon, scene.open_elapsed = "open", 0.0
        scene.horizon_p = 0.0
        scene.status = "WORMHOLE ESTABLISHED"
    return [Step(0.84 / speed, burst, cues=("kawoosh",)),
            Step(0.36 / speed, settle, "WORMHOLE ESTABLISHED", ("loop:wormhole_hum",))]


def hold(seconds: float, update=None, log: str | None = None, cues: tuple[str, ...] = ()) -> Step:
    return Step(seconds, update, log, cues)


def _darken(order: list[int], p: float, scene: Scene) -> None:
    keep = math.ceil(len(order) * (1 - p))
    scene.lit &= set(order[:keep])


def shutdown(speed: float = 1.0) -> list[Step]:
    def update(scene: Scene, p: float) -> None:
        order = LOCK_ORDER[scene.address.chevrons if scene.address else 7]
        if p < 0.5:
            if scene.horizon != "off":
                scene.horizon, scene.horizon_p = "collapse", p / 0.5
        else:
            scene.horizon, scene.horizon_p = "off", 0.0
            _darken(order, (p - 0.5) / 0.5, scene)
        scene.status = "WORMHOLE DISENGAGING"
        if p >= 1:
            scene.lit, scene.locked, scene.address = set(), 0, None
            scene.open_elapsed, scene.status = 0.0, "STANDING BY"
    return [Step(1.6 / speed, update, "WORMHOLE DISENGAGED", ("stop:wormhole_hum", "shutdown"))]


def idle(seconds: float) -> Step:
    def update(scene: Scene, p: float) -> None:
        scene.status = "STANDING BY"
    return Step(seconds, update)


def reset_scene(scene: Scene) -> None:
    """Return the gate to rest (keeps team statuses)."""
    scene.spinning, scene.clamp, scene.lit, scene.locked = False, 0.0, set(), 0
    scene.horizon, scene.horizon_p, scene.open_elapsed, scene.iris = "off", 0.0, 0.0, 0.0
    scene.figures, scene.splashes, scene.impacts, scene.vaporize = [], [], [], 0.0
    scene.alert, scene.address, scene.incoming, scene.identified = "normal", None, False, True
    scene.panel_title, scene.panel_rows, scene.panel_trace = "SENSORS", [], []
    scene.status = "STANDING BY"


def exit_sequence(scene: Scene, duration: float) -> list[Step]:
    pre: list[Step] = []
    if scene.horizon in ("open", "kawoosh", "collapse"):
        pre += shutdown(1.0)
    elif scene.spinning or scene.locked:
        lit_order = sorted(scene.lit)

        def abort(s: Scene, p: float) -> None:
            s.spinning, s.clamp = False, 0.0
            _darken(lit_order, p, s)
            if p >= 1:
                s.locked = 0
        pre.append(Step(1.0, abort, "DIAL ABORTED", ("stop:ring_spin", "dial_fail")))
    budget = sum(s.duration for s in pre)
    if budget > duration * 0.5 and budget > 0:
        for s in pre:
            s.duration *= duration * 0.5 / budget
        budget = duration * 0.5
    rest = duration - budget

    def blank(s: Scene, p: float) -> None:
        if p >= 1:
            reset_scene(s)
        s.status = "SYSTEMS OFFLINE"
        s.blank_panels = math.ceil(4 * p)

    def collapse(s: Scene, p: float) -> None:
        s.collapse_line, s.dim = p, 0.7 * p

    def fade(s: Scene, p: float) -> None:
        s.collapse_line, s.dim = 1.0, 0.7 + 0.3 * p
    return pre + [
        Step(rest * 0.45, blank, "SGC SYSTEMS SHUTTING DOWN", ("stopall",)),
        Step(rest * 0.40, collapse),
        Step(rest * 0.15, fade),
    ]
