"""The SGC's schedule as the player has been told it: the Database's QUEUE tab, and cancelling or reordering the
dial-outs still waiting for the gate. Pure functions over the campaign; the engine logs and saves."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QueueItem:
    id: str                          # "dial:<op>:<world>", "dial:depart:<mission>", "dial:search:<mission>:<by>",
                                     # "drone:<world>", "mission:<id>" or "team:<name>"
    kind: str                        # "dial_out" | "drone" | "mission" | "team"
    when: str
    what: str
    status: str
    sort: tuple[int, float]          # dial-outs first, in gate order; then everything else by time
    cancellable: bool = False
    movable: bool = False
    reason: str = ""                 # why x can't cancel it

    @property
    def cells(self) -> tuple[str, str, str]:
        return self.when, self.what, self.status
