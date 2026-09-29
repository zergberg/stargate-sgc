"""Helpers shared by the event modules."""
from __future__ import annotations

from ..addresses import Address
from ..model import Scene, Step
from .. import sequences as sq


def start_outgoing(addr: Address) -> Step:
    def setup(scene: Scene, p: float) -> None:
        scene.address, scene.locked, scene.lit = addr, 0, set()
        scene.incoming, scene.identified, scene.alert = False, True, "normal"
        scene.status = "DIALING"
    glyphs = "-".join(f"{g:02d}" for g in addr.full)
    return Step(0, setup, f"DIALING {addr.label.upper()}  {glyphs}")


def timed(total: float, update, marks: list[tuple[float, str | None, tuple[str, ...]]] = ()) -> list[Step]:
    """One logical span of `total` seconds, split so logs/cues fire at the given times.

    `update(scene, elapsed)` receives seconds since the span started.
    """
    marks = sorted(m for m in marks if 0 <= m[0] < total)
    bounds = sorted({0.0, *(m[0] for m in marks), total})
    steps = []
    for a, b in zip(bounds, bounds[1:]):
        here = [m for m in marks if m[0] == a]
        log = next((m[1] for m in here if m[1]), None)
        cues = tuple(c for m in here for c in m[2])

        def upd(scene: Scene, p: float, a=a, b=b) -> None:
            if update:
                update(scene, a + p * (b - a))
        steps.append(Step(b - a, upd, log, cues))
    return steps


def cleanup() -> Step:
    return Step(0, lambda scene, p: sq.reset_scene(scene), cues=("stop:klaxon",))


class Splashes:
    """Tracks ripples where things cross the event horizon; age runs 0..1 over `life` seconds."""

    def __init__(self, life: float = 0.9):
        self.life = life
        self._born: list[tuple[float, float, float]] = []

    def add(self, x: float, y: float, t: float) -> None:
        self._born.append((x, y, t))

    def apply(self, scene: Scene, t: float) -> None:
        scene.splashes = [[x, y, (t - b) / self.life] for x, y, b in self._born if 0 <= t - b < self.life]


class Trace:
    """Scrolling signal trace for the side screen, sampled every 0.1 s."""

    def __init__(self, fn, length: int = 60):
        self.fn, self.length, self._last = fn, length, -1

    def apply(self, scene: Scene, t: float) -> None:
        idx = int(t * 10)
        while self._last < idx:
            self._last += 1
            scene.panel_trace.append(max(0.0, min(1.0, self.fn(self._last / 10))))
        del scene.panel_trace[:-self.length]
