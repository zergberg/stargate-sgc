"""The director: plays a queue of timed steps against the scene, building new cycles as needed."""
from __future__ import annotations

import random
from collections import deque

from . import sequences as sq
from .addresses import Address, AddressPicker
from .config import Config
from .events import EventContext, EventDef
from .model import Scene, Step


class Director:
    def __init__(self, cfg: Config, rng: random.Random, picker: AddressPicker, registry: dict[str, EventDef]):
        self.cfg, self.rng, self.picker, self.registry = cfg, rng, picker, registry
        self.scene = Scene()
        self.time = 0.0
        self._queue: deque[Step] = deque()
        self._cur: Step | None = None
        self._cur_t = 0.0
        self._pending: list[tuple[str, Address | None]] = []
        self._last_kind: str | None = None
        self._exiting = False
        self._finished = False
        self.auto = True                  # False: only play what run_steps() queues (game mode)

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def exiting(self) -> bool:
        return self._exiting

    @property
    def idle(self) -> bool:
        """Nothing playing and nothing queued (with auto off, the gate just rests)."""
        return self._cur is None and not self._queue

    def run_steps(self, steps: list[Step]) -> None:
        self._queue.extend(steps)

    def cut_to(self, steps: list[Step], auto: bool) -> None:
        """Abandon whatever is playing (shutting the gate quickly), then play `steps`."""
        if self._exiting:
            return
        self.skip(log=None)
        self._queue.extend(steps)
        self.auto = auto

    def queue_event(self, name: str, addr: Address | None = None) -> None:
        if name not in self.registry:
            raise KeyError(f"unknown event '{name}'")
        self._pending.append((name, addr))

    def _pick(self) -> tuple[str, Address]:
        addr = self.picker.next()
        weights = {k: v * (2 if k in addr.events else 1) for k, v in self.cfg.event_weights.items()
                   if k in self.registry}
        if self._last_kind == "incoming":
            weights = {k: v for k, v in weights.items() if self.registry[k].kind != "incoming"}
        names = [k for k, v in weights.items() if v > 0]
        if not names:
            return "science", addr
        return self.rng.choices(names, [weights[k] for k in names])[0], addr

    def _build_cycle(self) -> list[Step]:
        if self._pending:
            name, addr = self._pending.pop(0)
            if addr is None and self.registry[name].kind == "outgoing":
                addr = self.picker.next()
        else:
            name, addr = self._pick()
        ev = self.registry[name]
        self._last_kind = ev.kind
        ctx = EventContext(addr if ev.kind == "outgoing" else None, self.rng, self.scene, 1.0,
                           self.cfg.open_scale, self.scene.ring_angle)
        return ev.build(ctx) + [sq.idle(4.0)]

    def advance(self, dt: float) -> tuple[list[str], list[str]]:
        """Move time forward by dt; returns (log lines, sound cues) emitted along the way."""
        logs: list[str] = []
        cues: list[str] = []
        if self._finished:
            return logs, cues
        self.time += dt
        if self.scene.horizon == "open":
            self.scene.open_elapsed += dt
        remaining = dt
        while True:
            if self._cur is None:
                if not self._queue:
                    if self._exiting:
                        self._finished = True
                        break
                    if not self.auto:
                        break
                    self._queue.extend(self._build_cycle())
                self._cur, self._cur_t = self._queue.popleft(), 0.0
                if self._cur.log:
                    logs.append(self._cur.log)
                cues.extend(self._cur.cues)
                if self._cur.duration <= 0:
                    if self._cur.update:
                        self._cur.update(self.scene, 1.0)
                    self._cur = None
                    continue
            if remaining <= 1e-12:
                break
            take = min(remaining, self._cur.duration - self._cur_t)
            self._cur_t += take
            remaining -= take
            if self._cur.update:
                self._cur.update(self.scene, min(1.0, self._cur_t / self._cur.duration))
            if self._cur_t >= self._cur.duration - 1e-9:
                self._cur = None
        return logs, cues

    def skip(self, log: str | None = "SEQUENCE OVERRIDDEN") -> None:
        """Abandon the current cycle: shut the gate quickly and move on."""
        if self._exiting:
            return

        def abort(scene: Scene, p: float) -> None:
            scene.spinning, scene.clamp = False, 0.0
        steps = [Step(0, abort, log, ("stop:ring_spin", "stop:klaxon"))]
        if self.scene.horizon != "off" or self.scene.locked:
            steps += sq.shutdown(2.0)
        steps += [Step(0, lambda s, p: sq.reset_scene(s), cues=("stopall",)), sq.idle(1.0)]
        self._cur = None
        self._queue = deque(steps)

    def begin_exit(self) -> None:
        if self._exiting:
            return
        self._exiting = True
        self._cur = None
        self._queue = deque(sq.exit_sequence(self.scene, self.cfg.exit_duration))
