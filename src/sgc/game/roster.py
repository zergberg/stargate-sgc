"""The roster: specialty strength, commissioning SG-5 to SG-12, and training a second specialty."""
from __future__ import annotations

from .clock import DAY, HOUR
from .state import ALL_TEAMS, SPECIALTIES, Campaign, Team

COMMISSION = 200
TRAINING = 100
FORMING = 2 * DAY               # a new team forms this long before it's on duty
TRAIN_TIME = DAY


def strength(t: Team, specialty: str) -> float:
    """1 for the team's own specialty, ½ for a trained secondary or for SG-1 (elite), else 0."""
    if t.specialty == specialty:
        return 1.0
    if t.specialty == "elite" or t.secondary == specialty:
        return 0.5
    return 0.0


def next_number(c: Campaign) -> str | None:
    return next((n for n in ALL_TEAMS if n not in c.teams), None)


def commission_reason(c: Campaign) -> str | None:
    if next_number(c) is None:
        return f"THE ROSTER IS FULL ({ALL_TEAMS[-1]})"
    if c.funding < COMMISSION:
        return f"NOT ENOUGH FUNDING ({COMMISSION})"
    return None


def commission(c: Campaign, specialty: str) -> str:
    """Form the next free SG team, Green, with this specialty."""
    if specialty not in SPECIALTIES:
        raise ValueError(f"unknown specialty {specialty!r}")
    why = commission_reason(c)
    if why:
        return why
    name = next_number(c)
    c.funding -= COMMISSION
    c.teams[name] = Team(specialty, status="forming", until=c.now + FORMING)
    return f"{name} COMMISSIONED: {specialty.upper()}, FORMING FOR {FORMING // DAY} DAYS"


def train_reason(c: Campaign, team: str, specialty: str | None = None) -> str | None:
    t = c.teams[team]
    if t.specialty == "elite":
        return f"{team} ALREADY TRAINS IN EVERY SPECIALTY"
    if t.secondary:
        return f"{team} ALREADY HAS A SECOND SPECIALTY"
    if specialty == t.specialty:
        return f"{team} IS ALREADY {specialty.upper()}"
    if t.status != "base" or t.until > c.now:
        return f"{team} MUST BE AT BASE AND ON DUTY"
    if c.funding < TRAINING:
        return f"NOT ENOUGH FUNDING ({TRAINING})"
    return None


def train(c: Campaign, team: str, specialty: str) -> str:
    """Train a second specialty (half strength); the team is at base but off duty meanwhile."""
    if specialty not in SPECIALTIES:
        raise ValueError(f"unknown specialty {specialty!r}")
    why = train_reason(c, team, specialty)
    if why:
        return why
    t = c.teams[team]
    c.funding -= TRAINING
    t.secondary, t.status, t.until = specialty, "training", c.now + TRAIN_TIME
    return f"{team} TRAINING: {specialty.upper()}, BACK ON DUTY IN {TRAIN_TIME // HOUR} HOURS"
