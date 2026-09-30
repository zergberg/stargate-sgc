"""Campaign state, save version 2: everything a saved game needs, as plain data."""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field

from . import orders
from .clock import HOUR, START, Scheduler
from .world import DRONES, ENVIRONMENTS, FEATURES, INHABITANTS, STATUSES as WORLD_STATUSES, World, cartouche

VERSION = 2
TEAMS = ("SG-1", "SG-2", "SG-3", "SG-4")
ROSTER = {"SG-1": "elite", "SG-2": "recon", "SG-3": "combat", "SG-4": "science"}
SPECIALTIES = ("recon", "combat", "science", "diplomatic", "medical")
STATUSES = ("base", "offworld", "injured", "captured", "lost")
IDC_STATES = ("valid", "compromised", "revoked")
METERS = ("security", "personnel")
MODES = ("campaign", "sandbox")
DIFFICULTIES = ("recruit", "officer", "commander")
PACES = ("relaxed", "standard", "busy")
RANKS = (("green", 0), ("seasoned", 3), ("veteran", 8), ("elite", 15))
RANK_NAMES = tuple(name for name, _ in RANKS)
MISSION_TYPES = ("survey", "contact")
MISSION_STATES = ("active", "complete", "aborted", "captured", "lost")
STOCK = {"malp": (4, 6), "uav": (2, 3)}             # drone: (starting stock, cap)
FIRST_INCOMING = (36, 96)                             # game hours until the first random incoming wormhole


@dataclass
class Team:
    specialty: str                  # a SPECIALTY, or "elite" (SG-1: every specialty at half strength)
    status: str = "base"
    idc: str = "valid"
    xp: int = 0                     # missions completed; sets the rank
    until: int = 0                  # game minute a stood-down, injured, captured or lost team's timer runs out
    where: str = ""                 # world id while offworld or captured
    mission: int | None = None      # the active mission's id


@dataclass
class Mission:
    id: int
    team: str
    world: str
    type: str
    start: int
    end: int                        # game minute the team is due home
    state: str = "active"
    casualties: int = 0
    findings: list[str] = field(default_factory=list)


def _meters() -> dict[str, int]:
    return {"security": 70, "personnel": 80}


def _record() -> dict[str, int]:
    return {"missions": 0, "personnel_lost": 0, "teams_lost": 0, "probes": 0, "surveyed": 0}


@dataclass
class Campaign:
    mode: str
    difficulty: str
    seed: int
    pace: str = "standard"
    minutes: float = START
    events: Scheduler = field(default_factory=Scheduler)
    gate_until: int = 0
    worlds: dict[str, World] = field(default_factory=dict)
    teams: dict[str, Team] = field(default_factory=lambda: {t: Team(ROSTER[t]) for t in TEAMS})
    missions: list[Mission] = field(default_factory=list)
    stock: dict[str, int] = field(default_factory=lambda: {d: s for d, (s, _) in STOCK.items()})
    orders: dict[str, str] = field(default_factory=orders.defaults)
    meters: dict[str, int] = field(default_factory=_meters)
    inventory: set[str] = field(default_factory=set)
    used: set[str] = field(default_factory=set)           # single-use ally powers already spent
    alarms: list[dict] = field(default_factory=list)      # decisions waiting for an order, oldest first
    record: dict[str, int] = field(default_factory=_record)
    rng_state: tuple | None = None
    over: str | None = None                                 # set when the base falls

    @property
    def now(self) -> int:
        return int(self.minutes)

    def mission(self, mid: int | None) -> Mission | None:
        return next((m for m in self.missions if m.id == mid), None)

    def active_missions(self) -> list[Mission]:
        return [m for m in self.missions if m.state == "active"]


def rank_index(team: Team) -> int:
    return max(i for i, (_, need) in enumerate(RANKS) if team.xp >= need)


def rank(team: Team) -> str:
    return RANKS[rank_index(team)][0]


def demote(team: Team) -> None:
    """Heavy casualties: down one rank, to the bottom of it."""
    team.xp = RANKS[max(0, rank_index(team) - 1)][1]


def has_specialty(team: Team, specialty: str) -> bool:
    return team.specialty == specialty or team.specialty == "elite"


def available_teams(c: Campaign) -> list[str]:
    return [t for t in TEAMS if c.teams[t].status == "base" and c.teams[t].until <= c.now]


def new_campaign(mode: str, difficulty: str, seed: int, pace: str = "standard") -> Campaign:
    c = Campaign(mode, difficulty, seed, pace)
    c.worlds = cartouche(mode, seed, START)
    rng = random.Random(seed + 1)
    c.events.push(START + HOUR, "recovery_tick")
    c.events.push(START + rng.randint(*FIRST_INCOMING) * HOUR, "incoming")
    return c


# ------------------------------------------------------------------ saving

def world_to_dict(w: World) -> dict:
    d = asdict(w)
    d["glyphs"], d["features"] = list(w.glyphs), list(w.features)
    d["names"] = [list(n) for n in w.names]
    d["reports"], d["notes"] = [list(r) for r in w.reports], [list(n) for n in w.notes]
    return d


def to_dict(c: Campaign) -> dict:
    d = {
        "version": VERSION, "mode": c.mode, "difficulty": c.difficulty, "seed": c.seed, "pace": c.pace,
        "minutes": c.minutes, "events": c.events.to_list(), "event_seq": c.events.seq, "gate_until": c.gate_until,
        "worlds": [world_to_dict(w) for w in c.worlds.values()],
        "teams": {n: asdict(t) for n, t in c.teams.items()},
        "missions": [asdict(m) for m in c.missions],
        "stock": dict(c.stock), "orders": dict(c.orders), "meters": dict(c.meters),
        "inventory": sorted(c.inventory), "used": sorted(c.used), "alarms": [dict(a) for a in c.alarms],
        "record": dict(c.record), "rng_state": None, "over": c.over,
    }
    if c.rng_state is not None:
        version, internal, gauss = c.rng_state
        d["rng_state"] = [version, list(internal), gauss]
    return d


def _int(v, what: str) -> int:
    if not isinstance(v, int) or isinstance(v, bool):
        raise ValueError(f"{what} must be a whole number, got {v!r}")
    return v


def _str(v, what: str) -> str:
    if not isinstance(v, str):
        raise ValueError(f"{what} must be text, got {v!r}")
    return v


def _bool(v, what: str) -> bool:
    if not isinstance(v, bool):
        raise ValueError(f"{what} must be a bool, got {v!r}")
    return v


def _one_of(v, allowed, what: str):
    if v not in allowed:
        raise ValueError(f"unknown {what} {v!r}")
    return v


def _pairs(items, what: str) -> list[tuple[int, str]]:
    return [(_int(m, what), _str(t, what)) for m, t in items]


def _str_list(v, what: str) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ValueError(f"{what} must be a list of text, got {v!r}")
    return v


def _nonneg_int(v, what: str) -> int:
    n = _int(v, what)
    if n < 0:
        raise ValueError(f"{what} must not be negative, got {n!r}")
    return n


def _meter_int(v, what: str) -> int:
    n = _int(v, what)
    if not 0 <= n <= 100:
        raise ValueError(f"{what} must be between 0 and 100, got {n!r}")
    return n


def _alarm(a: dict) -> dict:
    """An alarm waiting for an order: type, title and text are shown; deadline schedules the timeout."""
    if not isinstance(a, dict):
        raise ValueError(f"alarm must be a table, got {a!r}")
    for key in ("type", "title", "text"):
        if not isinstance(a.get(key), str):
            raise ValueError(f"alarm {key} must be text, got {a.get(key)!r}")
    deadline = a.get("deadline")
    if deadline is not None and (not isinstance(deadline, int) or isinstance(deadline, bool)):
        raise ValueError(f"alarm deadline must be a whole number or null, got {deadline!r}")
    if a["type"] == "node":
        if not isinstance(a.get("bind"), dict):
            raise ValueError(f"a node alarm's bind must be a table, got {a.get('bind')!r}")
        if not isinstance(a.get("scenario"), str):
            raise ValueError(f"a node alarm's scenario must be text, got {a.get('scenario')!r}")
        if not isinstance(a.get("node"), str):
            raise ValueError(f"a node alarm's node must be text, got {a.get('node')!r}")
    if a["type"] == "missed_checkin":
        _int(a.get("mission"), "a missed check-in alarm's mission")
    return dict(a)


def world_from_dict(d: dict) -> World:
    glyphs = tuple(_int(g, "glyph") for g in d["glyphs"])
    if not 6 <= len(glyphs) <= 8 or not all(1 <= g <= 39 for g in glyphs):
        raise ValueError(f"bad glyphs {glyphs!r}")
    hidden = d["hidden_names"]
    if not isinstance(hidden, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in hidden.items()):
        raise ValueError("hidden_names must map text to text")
    seen = d["seen"]
    if not isinstance(seen, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in seen.items()):
        raise ValueError("seen must map text to text")
    last = d["last_visit"]
    wid = _str(d["id"], "world id")
    if not wid:
        raise ValueError("world id must not be empty")
    canon = d["canon"]
    if not isinstance(canon, bool):
        raise ValueError(f"canon must be a bool, got {canon!r}")
    return World(
        id=wid, glyphs=glyphs,
        env=_one_of(d["env"], ENVIRONMENTS, "environment"),
        inhabitants=_one_of(d["inhabitants"], INHABITANTS, "inhabitants"),
        features=tuple(_one_of(f, FEATURES, "feature") for f in d["features"]),
        owner=None if d["owner"] is None else _str(d["owner"], "owner"),
        hidden_names=dict(hidden), canon=canon,
        status=_one_of(d["status"], WORLD_STATUSES, "world status"), surveyed=_bool(d["surveyed"], "surveyed"),
        names=[(_str(n, "name"), _str(s, "name source"), _int(m, "name minute")) for n, s, m in d["names"]],
        seen=dict(seen), telemetry=_str_list(d["telemetry"], "telemetry"),
        reports=_pairs(d["reports"], "report"), notes=_pairs(d["notes"], "note"),
        drone=None if d["drone"] is None else _one_of(d["drone"], DRONES, "drone"),
        options=[_one_of(o, MISSION_TYPES, "mission type") for o in d["options"]],
        found=_str(d["found"], "found"), last_visit=None if last is None else _int(last, "last_visit"))


def _team(d: dict) -> Team:
    mission = d["mission"]
    return Team(specialty=_one_of(d["specialty"], (*SPECIALTIES, "elite"), "specialty"),
                status=_one_of(d["status"], STATUSES, "team status"), idc=_one_of(d["idc"], IDC_STATES, "idc state"),
                xp=_nonneg_int(d["xp"], "xp"), until=_nonneg_int(d["until"], "until"),
                where=_str(d["where"], "where"),
                mission=None if mission is None else _int(mission, "team mission"))


def _mission(d: dict) -> Mission:
    return Mission(id=_int(d["id"], "mission id"), team=_one_of(d["team"], TEAMS, "team"),
                   world=_str(d["world"], "mission world"), type=_one_of(d["type"], MISSION_TYPES, "mission type"),
                   start=_int(d["start"], "start"), end=_int(d["end"], "end"),
                   state=_one_of(d["state"], MISSION_STATES, "mission state"),
                   casualties=_int(d["casualties"], "casualties"),
                   findings=[_str(f, "finding") for f in d["findings"]])


DIAL_OPS = ("malp", "uav", "recall", "depart", "search")
SEARCHERS = ("malp", *TEAMS)                   # a MALP, or the team sent to look


def _event(e, world_ids: set[str], mission_ids: set[int]) -> None:
    """Check one saved event's payload against what its handler in the engine reads; raises ValueError."""
    d, kind = e.data, e.kind

    def need(key: str):
        if key not in d:
            raise ValueError(f"event {kind!r} needs {key!r}, got {d!r}")
        return d[key]

    def world() -> None:
        _one_of(_str(need("world"), f"{kind} world"), world_ids, f"{kind} world")

    def mission() -> None:
        mid = _int(need("mission"), f"{kind} mission")
        if mid not in mission_ids:
            raise ValueError(f"event {kind!r} references unknown mission {mid!r}")

    if kind == "dial_out":
        op = _one_of(need("op"), DIAL_OPS, "dial-out op")
        if op in ("malp", "uav", "recall"):
            world()
        else:
            mission()
            if op == "search":
                _one_of(need("by"), SEARCHERS, "searcher")
    elif kind == "malp_return":
        world()
        _one_of(need("drone"), DRONES, "drone")
    elif kind == "search_report":
        mission()
        _one_of(need("by"), SEARCHERS, "searcher")
    elif kind == "team_return" and "mission" not in d:       # a reinforcement heading home, not a mission
        _one_of(need("team"), TEAMS, "returning team")
    elif kind in ("checkin", "team_return", "overdue"):
        mission()
        if "since" in d:
            _int(d["since"], "check-in since")
    elif "mission" in d:
        mission()


def _way_home(events: Scheduler, name: str) -> bool:
    """An offworld team on no mission is reinforcing or searching: an event brings it home."""
    return bool(events.find(lambda e: (e.kind == "team_return" and e.data.get("team") == name)
                            or (e.kind in ("dial_out", "search_report") and e.data.get("by") == name)))


def from_dict(d: dict) -> Campaign:
    """Rebuild a campaign from to_dict() output; raises ValueError on bad or malformed data."""
    version = d.get("version") if isinstance(d, dict) else None
    if not isinstance(d, dict) or not isinstance(version, int) or isinstance(version, bool) or version != VERSION:
        raise ValueError(f"unsupported save version {version!r}")
    try:
        minutes = d["minutes"]
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
            raise ValueError(f"minutes must be a number, got {minutes!r}")
        try:
            finite = math.isfinite(minutes)
        except OverflowError:
            finite = False
        if not finite or minutes < 0:
            raise ValueError(f"minutes must be a number, got {minutes!r}")
        worlds = [world_from_dict(w) for w in d["worlds"]]
        world_ids = {w.id for w in worlds}
        teams = {_one_of(n, TEAMS, "team"): _team(t) for n, t in d["teams"].items()}
        missions = [_mission(m) for m in d["missions"]]
        mission_ids = [m.id for m in missions]
        if len(set(mission_ids)) != len(mission_ids):
            raise ValueError(f"mission ids must be unique, got {mission_ids!r}")
        mission_id_set = set(mission_ids)
        for m in missions:
            if m.world not in world_ids:
                raise ValueError(f"mission {m.id} references unknown world {m.world!r}")
        for name, tm in teams.items():
            if tm.mission is not None and tm.mission not in mission_id_set:
                raise ValueError(f"{name} references unknown mission {tm.mission!r}")
        events = Scheduler.from_list(d["events"], d["event_seq"])
        for e in events:
            _event(e, world_ids, mission_id_set)
        for name, tm in teams.items():
            if tm.status == "offworld" and tm.mission is None and not _way_home(events, name):
                raise ValueError(f"{name} is offworld with no mission and nothing bringing it home")
        stock = {k: _nonneg_int(v, "stock") for k, v in d["stock"].items()}
        meters = {k: _meter_int(v, "meters") for k, v in d["meters"].items()}
        if set(d["record"]) != set(_record()):
            raise ValueError(f"record keys must be {sorted(_record())}, got {sorted(d['record'])}")
        record = {k: _int(v, "record") for k, v in d["record"].items()}
        alarms = d["alarms"]
        if not isinstance(alarms, list):
            raise ValueError("alarms must be a list of tables")
        alarms = [_alarm(a) for a in alarms]
        over = d["over"]
        if over is not None and not isinstance(over, str):
            raise ValueError(f"over must be null or a string, got {over!r}")
        rs = d["rng_state"]
        rng_state = None
        if rs is not None:
            rng_state = (rs[0], tuple(rs[1]), rs[2])
            try:
                random.Random().setstate(rng_state)
            except Exception as e:
                raise ValueError(f"invalid rng_state: {e}") from e
        inventory = _str_list(d["inventory"], "inventory")
        used = _str_list(d["used"], "used")
        c = Campaign(
            mode=_one_of(d["mode"], MODES, "mode"), difficulty=_one_of(d["difficulty"], DIFFICULTIES, "difficulty"),
            seed=_int(d["seed"], "seed"), pace=_one_of(d["pace"], PACES, "pace"), minutes=minutes,
            events=events, gate_until=_int(d["gate_until"], "gate_until"),
            worlds={w.id: w for w in worlds}, teams=teams, missions=missions,
            stock=stock, orders=orders.validate(d["orders"]), meters=meters,
            inventory=set(inventory), used=set(used), alarms=alarms,
            record=record, rng_state=rng_state, over=over)
    except (KeyError, TypeError, AttributeError, IndexError) as e:
        raise ValueError(str(e)) from e
    if set(c.teams) != set(TEAMS) or set(c.meters) != set(METERS) or set(c.stock) != set(STOCK) \
            or len(c.worlds) != len(worlds) or not c.worlds:
        raise ValueError("save file does not describe a valid campaign")
    return c
