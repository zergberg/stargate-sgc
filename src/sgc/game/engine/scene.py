"""Queueing visuals onto the director, and letting an idle wormhole close itself."""
from __future__ import annotations

from ... import sequences as sq
from ...model import Step


class SceneMixin:
    def _close_idle_gate(self) -> None:
        """A wormhole left up once its scene is over, with no order pending, disengages on its own."""
        if self.d is None or not self.d.idle or self.c.alarms or self.prompt is not None:
            return
        s = self.d.scene
        if s.horizon != "off" or s.locked:
            self._queue([*sq.shutdown(), Step(0, lambda sc, p: sq.reset_scene(sc))])

    def _show(self, steps: list[Step], urgent: bool = False) -> None:
        """Queue a visual. Routine traffic is skipped while other traffic is on screen, and waits behind a walk
        between the rooms."""
        if self.d is None or not steps:
            return
        if not urgent and self.showing:
            return
        self._queue(steps)

    def _queue(self, steps: list[Step]) -> None:
        """Put traffic on the director, counted as showing until its last step has played."""
        def played(s, p):
            self._traffic = max(0, self._traffic - 1)
        self._traffic += 1
        self.d.run_steps([*steps, Step(0, played)])
