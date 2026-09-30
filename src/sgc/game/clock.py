"""The SGC clock and the scheduler: game minutes, pace, the gate as one resource, and a saveable event queue."""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Callable, Iterator

PACES = {"relaxed": 120, "standard": 60, "busy": 10}      # real seconds per game hour
PACE_NAMES = tuple(PACES)
START = 8 * 60                                            # a campaign begins at 08:00 on day 1
HOUR = 60
DAY = 24 * HOUR
WINDOW = 3 * HOUR                                         # an alarm's decision window, in game minutes
WINDOW_REAL = 60.0                                        # ...and never less than this many real seconds
GATE_MINUTES = {"probe": 10, "uav": 10, "depart": 15, "recall": 30, "search": 10, "checkin": 10,
                "team_return": 15, "incoming": 30, "malp_return": 20,
                "trade_delivery": 20, "faction_action": 30}


def seconds_per_hour(pace: str, override: int | None = None) -> int:
    """Real seconds per game hour: the config override (5-600) if set, else the campaign's pace."""
    return override if override is not None else PACES[pace]


def to_minutes(real_seconds: float, sph: float) -> float:
    return real_seconds * HOUR / sph


def to_seconds(minutes: float, sph: float) -> float:
    return minutes * sph / HOUR


def window(sph: float) -> int:
    """Game minutes an alarm waits for an order: 3 game hours, stretched to at least 60 real seconds."""
    return max(WINDOW, round(to_minutes(WINDOW_REAL, sph)))


def day(minutes: float) -> int:
    return int(minutes // DAY) + 1


def stamp(minutes: float) -> str:
    """'DAY 12 · 14:30'."""
    m = int(minutes) % DAY
    return f"DAY {day(minutes)} · {m // HOUR:02d}:{m % HOUR:02d}"


def short(minutes: float) -> str:
    """'D12 14:30', for tables."""
    m = int(minutes) % DAY
    return f"D{day(minutes)} {m // HOUR:02d}:{m % HOUR:02d}"


@dataclass(order=True)
class Event:
    due: int
    seq: int
    kind: str = field(compare=False)
    data: dict = field(compare=False, default_factory=dict)


class Scheduler:
    """A priority queue of events: earliest due first, first pushed first among equals."""

    def __init__(self, events: list[Event] | None = None, seq: int = 0):
        self._heap = list(events or [])
        heapq.heapify(self._heap)
        self.seq = max([seq, *(e.seq + 1 for e in self._heap)])

    def push(self, due: int, kind: str, data: dict | None = None) -> Event:
        ev = Event(int(due), self.seq, kind, dict(data or {}))
        self.seq += 1
        heapq.heappush(self._heap, ev)
        return ev

    def peek(self) -> Event | None:
        return self._heap[0] if self._heap else None

    def pop(self) -> Event:
        return heapq.heappop(self._heap)

    def cancel(self, match: Callable[[Event], bool]) -> int:
        keep = [e for e in self._heap if not match(e)]
        removed = len(self._heap) - len(keep)
        self._heap = keep
        heapq.heapify(self._heap)
        return removed

    def find(self, match: Callable[[Event], bool]) -> list[Event]:
        return sorted(e for e in self._heap if match(e))

    def remove(self, match: Callable[[Event], bool]) -> list[Event]:
        """Take out the matching events and return them, soonest first."""
        gone = sorted(e for e in self._heap if match(e))
        self._heap = [e for e in self._heap if not match(e)]
        heapq.heapify(self._heap)
        return gone

    def swap(self, a: Event, b: Event) -> None:
        """Re-key two queued events in place: each takes the other's (due, seq), so they trade places in the
        queue and nothing else moves."""
        if not any(e is a for e in self._heap) or not any(e is b for e in self._heap):
            raise ValueError("can only swap events that are queued")
        a.due, b.due = b.due, a.due
        a.seq, b.seq = b.seq, a.seq
        heapq.heapify(self._heap)

    def __len__(self) -> int:
        return len(self._heap)

    def __iter__(self) -> Iterator[Event]:
        return iter(sorted(self._heap))

    def __eq__(self, other) -> bool:
        return isinstance(other, Scheduler) and self.to_list() == other.to_list() and self.seq == other.seq

    def to_list(self) -> list[dict]:
        return [{"due": e.due, "seq": e.seq, "kind": e.kind, "data": dict(e.data)} for e in sorted(self._heap)]

    @classmethod
    def from_list(cls, items: list, seq: int) -> "Scheduler":
        """Rebuild from to_list(); raises ValueError on malformed items."""
        events = []
        for d in items:
            if not isinstance(d, dict) or not isinstance(d.get("kind"), str) or not isinstance(d.get("data"), dict):
                raise ValueError(f"bad event {d!r}")
            for k in ("due", "seq"):
                if not isinstance(d.get(k), int) or isinstance(d.get(k), bool):
                    raise ValueError(f"event {k} must be a whole number")
            events.append(Event(d["due"], d["seq"], d["kind"], dict(d["data"])))
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise ValueError("scheduler seq must be a whole number")
        return cls(events, seq)
