"""Standing orders: the situations an alarm can belong to, their choices, and what runs when time is up."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Situation:
    id: str
    label: str
    choices: tuple[tuple[str, str], ...]          # (key, label); the first is the cautious default

    @property
    def default(self) -> str:
        return self.choices[0][0]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(k for k, _ in self.choices)


SITUATIONS: dict[str, Situation] = {s.id: s for s in (
    Situation("unknown_idc", "Unknown IDC or no IDC",
              (("closed", "Keep the iris closed"), ("open_guarded", "Open for 30 seconds under guard"))),
    Situation("hostiles_following", "Our IDC, team reports hostiles following",
              (("open_briefly", "Open briefly, then close"), ("keep_closed", "Keep closed until clear"))),
    Situation("bad_idc", "A compromised or revoked IDC",
              (("closed_alert", "Keep closed, revoke the code"), ("closed_silent", "Keep closed, say nothing"))),
    Situation("object", "Object through the gate",
              (("seal", "Seal the level"), ("push_back", "Dial out, push it back through"))),
    Situation("missed_checkin", "Missed check-in",
              (("malp", "Send a MALP to search"), ("team", "Send the nearest available team"),
               ("wait", "Wait 12 hours"))),
    Situation("under_fire", "Team under fire at a check-in",
              (("recall", "Recall them"), ("hold", "Hold position"), ("reinforce", "Reinforce if a team is available"))),
    Situation("contact_offer", "Contact or trade offer during a check-in",
              (("defer", "Defer to the debrief"), ("accept", "Accept if the team is diplomatic"))),
)}


def defaults() -> dict[str, str]:
    return {s.id: s.default for s in SITUATIONS.values()}


def validate(orders: dict) -> dict[str, str]:
    """A complete set of orders from saved data; raises ValueError on anything unknown."""
    if not isinstance(orders, dict) or set(orders) != set(SITUATIONS):
        raise ValueError(f"standing orders must cover exactly {sorted(SITUATIONS)}")
    for sid, key in orders.items():
        if key not in SITUATIONS[sid].keys:
            raise ValueError(f"unknown standing order {key!r} for {sid}")
    return dict(orders)


def cycle(orders: dict[str, str], sid: str) -> str:
    """Switch a situation to its next choice; returns the new key."""
    keys = SITUATIONS[sid].keys
    orders[sid] = keys[(keys.index(orders[sid]) + 1) % len(keys)]
    return orders[sid]


def label(sid: str, key: str) -> str:
    return dict(SITUATIONS[sid].choices)[key]


def on_timeout(sid: str | None, orders: dict[str, str], options: list[tuple[str, bool]], default: str) -> str:
    """The choice key that runs when nobody answers: the standing order if it's there and allowed, else the
    default if that's allowed, else the first allowed choice (the default if nothing is).

    `options` are the alarm's (choice key, enabled) pairs.
    """
    ordered = orders.get(sid) if sid else None
    for key in (ordered, default):
        if key is not None and (key, True) in options:
            return key
    return next((k for k, ok in options if ok), default)
