"""Campaign state: everything a saved game needs, as plain data."""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field

VERSION = 1
TEAMS = ("SG-1", "SG-2", "SG-3", "SG-4")
STATUSES = ("base", "offworld", "injured", "captured", "lost")
IDC_STATES = ("valid", "compromised", "revoked")
METERS = ("security", "personnel", "intel")
MODES = ("campaign", "endless")
DIFFICULTIES = ("recruit", "officer", "commander")
POOL = ("Apophis", "Heru'ur", "Sokar", "Cronus", "Ba'al", "Yu", "Nirrti", "Svarog", "Olokun", "Bastet")
ROSTER = 5


@dataclass
class Team:
    status: str = "base"
    idc: str = "valid"
    out_cycles: int = 0          # cycles until an injured, re-forming or stood-down team is available
    captured_at: str = ""


@dataclass
class Lord:
    name: str
    strength: int
    aggression: int
    defeated: bool = False


def _meters() -> dict[str, int]:
    return {"security": 70, "personnel": 80, "intel": 10}


def _record() -> dict[str, int]:
    return {"goauld_defeated": 0, "missions": 0, "personnel_lost": 0, "teams_lost": 0}


@dataclass
class Campaign:
    mode: str
    difficulty: str
    seed: int
    meters: dict[str, int] = field(default_factory=_meters)
    teams: dict[str, Team] = field(default_factory=lambda: {t: Team() for t in TEAMS})
    lords: list[Lord] = field(default_factory=list)
    inventory: set[str] = field(default_factory=set)
    used: set[str] = field(default_factory=set)           # single-use ally powers already spent
    cycles: int = 0
    since_briefing: int = 0
    record: dict[str, int] = field(default_factory=_record)
    rng_state: tuple | None = None
    over: str | None = None                                 # set when the base falls
    won: bool = False

    def lord(self, name: str) -> Lord | None:
        return next((l for l in self.lords if l.name == name), None)

    def active_lords(self) -> list[Lord]:
        return [l for l in self.lords if not l.defeated]


def new_campaign(mode: str, difficulty: str, seed: int) -> Campaign:
    rng = random.Random(seed)
    lords = [Lord(name, rng.randint(3, 5), rng.randint(10, 30)) for name in rng.sample(POOL, ROSTER)]
    return Campaign(mode, difficulty, seed, lords=lords)


def replace_lord(c: Campaign, fallen: str) -> Lord | None:
    """Endless mode: a new System Lord rises when one falls."""
    taken = {l.name for l in c.lords}
    for name in POOL:
        if name not in taken:
            new = Lord(name, 4, 20)
            c.lords.append(new)
            return new
    for l in c.lords:                  # every name used: an old enemy returns
        if l.defeated and l.name != fallen:
            l.defeated, l.strength, l.aggression = False, 4, 20
            return l
    return None


def to_dict(c: Campaign) -> dict:
    d = asdict(c)
    d["inventory"], d["used"] = sorted(c.inventory), sorted(c.used)
    if c.rng_state is not None:
        version, internal, gauss = c.rng_state
        d["rng_state"] = [version, list(internal), gauss]
    d["version"] = VERSION
    return d


def _strict_int(v, what: str) -> int:
    if not isinstance(v, int) or isinstance(v, bool):
        raise ValueError(f"{what} must be a whole number, got {v!r}")
    return v


def from_dict(d: dict) -> Campaign:
    """Rebuild a campaign from to_dict() output; raises ValueError on bad or malformed data."""
    if d.get("version") != VERSION:
        raise ValueError(f"unsupported save version {d.get('version')!r}")
    try:
        teams = {}
        for name, v in d["teams"].items():
            if v["status"] not in STATUSES:
                raise ValueError(f"unknown team status {v['status']!r}")
            if v["idc"] not in IDC_STATES:
                raise ValueError(f"unknown idc state {v['idc']!r}")
            teams[name] = Team(status=v["status"], idc=v["idc"],
                                out_cycles=_strict_int(v["out_cycles"], "out_cycles"),
                                captured_at=v["captured_at"])
        lords = []
        for l in d["lords"]:
            if l["name"] not in POOL:
                raise ValueError(f"unknown Goa'uld {l['name']!r}")
            lords.append(Lord(name=l["name"],
                               strength=_strict_int(l["strength"], "strength"),
                               aggression=_strict_int(l["aggression"], "aggression"),
                               defeated=bool(l["defeated"])))
        meters = {k: _strict_int(v, "meters") for k, v in d["meters"].items()}
        if set(d["record"]) != set(_record()):
            raise ValueError(f"record keys must be {sorted(_record())}, got {sorted(d['record'])}")
        record = {k: _strict_int(v, "record") for k, v in d["record"].items()}
        won = d["won"]
        if not isinstance(won, bool):
            raise ValueError(f"won must be a bool, got {won!r}")
        over = d["over"]
        if over is not None and not isinstance(over, str):
            raise ValueError(f"over must be null or a string, got {over!r}")
        rs = d.get("rng_state")
        rng_state = None
        if rs is not None:
            rng_state = (rs[0], tuple(rs[1]), rs[2])
            try:
                random.Random().setstate(rng_state)
            except Exception as e:
                raise ValueError(f"invalid rng_state: {e}") from e
        c = Campaign(
            mode=d["mode"], difficulty=d["difficulty"],
            seed=_strict_int(d["seed"], "seed"),
            meters=meters, teams=teams, lords=lords,
            inventory=set(d["inventory"]), used=set(d["used"]),
            cycles=_strict_int(d["cycles"], "cycles"),
            since_briefing=_strict_int(d["since_briefing"], "since_briefing"),
            record=record, rng_state=rng_state, over=over, won=won,
        )
    except (KeyError, TypeError, AttributeError, IndexError) as e:
        raise ValueError(str(e)) from e
    if c.mode not in MODES or c.difficulty not in DIFFICULTIES or set(c.teams) != set(TEAMS) \
            or set(c.meters) != set(METERS):
        raise ValueError("save file does not describe a valid campaign")
    return c
