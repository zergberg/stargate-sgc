"""Gate addresses: the curated canon list plus random P3X-style worlds."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from importlib import resources


@dataclass(frozen=True)
class Address:
    name: str
    designation: str
    glyphs: tuple[int, ...]          # destination glyphs, without the point of origin
    canon: bool
    events: tuple[str, ...] = ()     # preferred events
    origin: int = 1                  # Earth's point of origin

    @property
    def chevrons(self) -> int:
        return len(self.glyphs) + 1

    @property
    def full(self) -> tuple[int, ...]:
        return self.glyphs + (self.origin,)

    @property
    def label(self) -> str:
        return self.name


def load_canon() -> list[Address]:
    raw = json.loads(resources.files("sgc").joinpath("data/addresses.json").read_text())
    return [Address(e["name"], e["designation"], tuple(e["glyphs"]), True, tuple(e["events"])) for e in raw]


def random_address(rng: random.Random) -> Address:
    glyphs = tuple(rng.sample(range(2, 40), 6))
    designation = f"P{rng.randint(2, 9)}{rng.choice('XCJMRWY')}-{rng.randint(100, 999)}"
    return Address(designation, designation, glyphs, False)


class AddressPicker:
    """Mixes canon and random addresses, never repeating the previous one or dialing Earth."""

    def __init__(self, canon: list[Address], canon_ratio: float, rng: random.Random):
        self._canon = [a for a in canon if a.name != "Earth"]
        self._ratio = canon_ratio
        self._rng = rng
        self._prev: str | None = None

    def next(self) -> Address:
        while True:
            if self._canon and self._rng.random() < self._ratio:
                a = self._rng.choice(self._canon)
            else:
                a = random_address(self._rng)
            if a.label != self._prev:
                self._prev = a.label
                return a
