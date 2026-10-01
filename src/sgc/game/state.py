"""Campaign state, save version 3: everything a saved game needs, as plain data."""
from __future__ import annotations

import copy
import math
import random
from dataclasses import asdict, dataclass, field

from . import orders
from .clock import CHECKIN_HOURS, DAY, HOUR, START, Scheduler
from .world import (DRONES, ENVIRONMENTS, FACTION_IDS, FACTION_KIND, FEATURES, INHABITANTS,
                    STATUSES as WORLD_STATUSES, WRECKS, World, cartouche, place)

VERSION = 3
CORE_TEAMS = ("SG-1", "SG-2", "SG-3", "SG-4")         # always on the roster; a lost one re-forms
ALL_TEAMS = tuple(f"SG-{n}" for n in range(1, 13))    # SG-5 to SG-12 are commissioned with funding
ROSTER = {"SG-1": "elite", "SG-2": "recon", "SG-3": "combat", "SG-4": "science"}
SPECIALTIES = ("recon", "combat", "science", "diplomatic", "medical")
STATUSES = ("base", "staging", "offworld", "injured", "captured", "lost", "forming", "training")
IDC_STATES = ("valid", "compromised", "revoked")
METERS = ("security", "personnel")
MODES = ("campaign", "sandbox")
DIFFICULTIES = ("recruit", "officer", "commander")
PACES = ("relaxed", "standard", "busy")
RANKS = (("green", 0), ("seasoned", 3), ("veteran", 8), ("elite", 15))
RANK_NAMES = tuple(name for name, _ in RANKS)
from .missions import MISSION_TYPES
MISSION_STATES = ("active", "complete", "aborted", "captured", "lost", "cancelled")
STOCK = {"malp": (4, 8), "uav": (0, 4)}             # drone: (starting stock, stores cap for purchases)
FIRST_INCOMING = (36, 96)                             # game hours until the first random incoming wormhole
REVIEW_EVERY = 7 * DAY                                # a funding review every 7 game days from the start
ARC_IDS = ("apophis", "thor", "tokra")                # arcs.ARCS defines exactly these
ARC_STATES = ("dormant", "active", "resolved", "failed")
ARC_UNLISTED = ("Vorash",)                            # arc worlds whose addresses the SGC doesn't have yet
DEAL_STATES = ("active", "ended", "cut")
GOODS = ("naquadah",)
UPGRADE_IDS = ("uav_program", "security_detail", "iris_reinforcement", "database_analysts", "infirmary",
               "research_lab", "naquadah_generator")
LEDGER = ("intel", "tech", "allies", "missions", "arcs", "lost", "captured", "breaches", "incidents")
ENDINGS = ("overrun", "fallen", "retired")
RESERVE_MAX = 4                                       # the most drones of a kind a requisition keeps in stores


@dataclass
class Team:
    specialty: str                  # a SPECIALTY, or "elite" (SG-1: every specialty at half strength)
    status: str = "base"
    idc: str = "valid"
    xp: int = 0                     # missions completed; sets the rank
    until: int = 0                  # game minute a timed status (stood down, injured, captured, lost,
                                    # forming, training) runs out
    where: str = ""                 # world id while offworld or captured
    mission: int | None = None      # the active mission's id
    secondary: str | None = None    # a trained second specialty, at half strength


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
    target: str | None = None       # rescue: the captive team; recover: the drone kind


@dataclass
class Faction:
    kind: str                       # "goauld" or "ally"
    attention: int = 0              # a Goa'uld's interest in Earth, 0-100; never shown as a number
    trust: int = 0                  # an ally's trust in Earth, 0-100
    known: bool = False             # the SGC has learned of them through intel
    source: str = ""                # where that intel came from
    quiet_since: int = START        # the last minute their attention rose


@dataclass
class ArcState:
    state: str = "dormant"          # an ARC_STATE
    stage: int = 0                  # 1.. once active
    started: int | None = None
    ended: int | None = None
    deadline: int | None = None     # the endgame's due minute, once its countdown runs


@dataclass
class Deal:
    id: int
    world: str
    goods: str
    amount: int
    next: int                       # game minute the next delivery is due
    left: int                       # deliveries still to come
    misses: int = 0                 # disruptions in a row
    state: str = "active"


@dataclass
class CapturedDrone:
    drone: str
    world: str                      # where it was taken, and is held
    minute: int
    located: bool = False           # intel or a UAV has found it: a recover mission is possible


def _meters() -> dict[str, int]:
    return {"security": 70, "personnel": 80}


def _record() -> dict[str, int]:
    return {"missions": 0, "personnel_lost": 0, "teams_lost": 0, "probes": 0, "surveyed": 0}


def _ledger() -> dict[str, int]:
    return dict.fromkeys(LEDGER, 0)


def _factions() -> dict[str, Faction]:
    return {fid: Faction(FACTION_KIND[fid]) for fid in FACTION_IDS}


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
    teams: dict[str, Team] = field(default_factory=lambda: {t: Team(ROSTER[t]) for t in CORE_TEAMS})
    missions: list[Mission] = field(default_factory=list)
    stock: dict[str, int] = field(default_factory=lambda: {d: s for d, (s, _) in STOCK.items()})
    orders: dict[str, str] = field(default_factory=orders.defaults)
    meters: dict[str, int] = field(default_factory=_meters)
    inventory: set[str] = field(default_factory=set)
    used: set[str] = field(default_factory=set)           # single-use ally powers already spent
    alarms: list[dict] = field(default_factory=list)      # decisions waiting for an order, oldest first
    record: dict[str, int] = field(default_factory=_record)
    rng_state: tuple | None = None
    over: str | None = None                                 # set when the campaign ends
    funding: int = 500
    naquadah: int = 0
    upgrades: set[str] = field(default_factory=set)
    reserve: dict[str, int] = field(default_factory=lambda: {"malp": 2, "uav": 0})
    factions: dict[str, Faction] = field(default_factory=_factions)
    arcs: dict[str, ArcState] = field(default_factory=dict)          # Campaign mode only
    deals: list[Deal] = field(default_factory=list)
    captured_drones: list[CapturedDrone] = field(default_factory=list)
    ledger: dict[str, int] = field(default_factory=_ledger)          # performance since the last review
    reviews: list[tuple[int, int, str]] = field(default_factory=list)   # (minute, grant, summary)
    unlisted: dict[str, World] = field(default_factory=dict)          # arc worlds not yet on the dialing list
    won: int | None = None                                  # the minute the campaign was won
    ending: str | None = None                               # why it ended: an ENDING (None reads as overrun)
    hints: set[str] = field(default_factory=set)            # one-time notices already given

    @property
    def now(self) -> int:
        return int(self.minutes)

    def mission(self, mid: int | None) -> Mission | None:
        return next((m for m in self.missions if m.id == mid), None)

    def active_missions(self) -> list[Mission]:
        return [m for m in self.missions if m.state == "active"]

    def deal(self, did: int | None) -> Deal | None:
        return next((d for d in self.deals if d.id == did), None)


def team_names(c: Campaign) -> list[str]:
    """The roster in number order: SG-1, SG-2, ..., SG-12."""
    return sorted(c.teams, key=lambda n: int(n.split("-")[1]))


def rank_index(team: Team) -> int:
    return max(i for i, (_, need) in enumerate(RANKS) if team.xp >= need)


def rank(team: Team) -> str:
    return RANKS[rank_index(team)][0]


def demote(team: Team) -> None:
    """Heavy casualties: down one rank, to the bottom of it."""
    team.xp = RANKS[max(0, rank_index(team) - 1)][1]


def has_specialty(team: Team, specialty: str) -> bool:
    return team.specialty in (specialty, "elite") or team.secondary == specialty


def available_teams(c: Campaign) -> list[str]:
    return [t for t in team_names(c) if c.teams[t].status == "base" and c.teams[t].until <= c.now]


def new_campaign(mode: str, difficulty: str, seed: int, pace: str = "standard") -> Campaign:
    c = Campaign(mode, difficulty, seed, pace)
    unlisted = [place(n) for n in ARC_UNLISTED] if mode == "campaign" else []
    c.worlds = cartouche(mode, seed, START, reserved={w.id for w in unlisted})
    c.unlisted = {w.id: w for w in unlisted}
    if mode == "campaign":
        c.arcs = {a: ArcState() for a in ARC_IDS}
    rng = random.Random(seed + 1)
    c.events.push(START + HOUR, "recovery_tick")
    c.events.push(START + rng.randint(*FIRST_INCOMING) * HOUR, "incoming")
    c.events.push(START + REVIEW_EVERY, "funding_review")
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
        "funding": c.funding, "naquadah": c.naquadah, "upgrades": sorted(c.upgrades), "reserve": dict(c.reserve),
        "factions": {fid: asdict(f) for fid, f in c.factions.items()},
        "arcs": {aid: asdict(a) for aid, a in c.arcs.items()},
        "deals": [asdict(x) for x in c.deals], "captured_drones": [asdict(x) for x in c.captured_drones],
        "ledger": dict(c.ledger), "reviews": [list(r) for r in c.reviews],
        "unlisted": [world_to_dict(w) for w in c.unlisted.values()],
        "won": c.won, "ending": c.ending, "hints": sorted(c.hints),
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
    wreck = d.get("wreck")                          # absent from a save made before wrecks: none
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
        wreck=None if wreck is None else _one_of(wreck, WRECKS, "wreck"),
        options=[_one_of(o, MISSION_TYPES, "mission type") for o in d["options"]],
        found=_str(d["found"], "found"), last_visit=None if last is None else _int(last, "last_visit"))


def _opt_int(v, what: str) -> int | None:
    return None if v is None else _int(v, what)


def _team(d: dict) -> Team:
    mission, secondary = d["mission"], d["secondary"]
    return Team(specialty=_one_of(d["specialty"], (*SPECIALTIES, "elite"), "specialty"),
                status=_one_of(d["status"], STATUSES, "team status"), idc=_one_of(d["idc"], IDC_STATES, "idc state"),
                xp=_nonneg_int(d["xp"], "xp"), until=_nonneg_int(d["until"], "until"),
                where=_str(d["where"], "where"),
                mission=None if mission is None else _int(mission, "team mission"),
                secondary=None if secondary is None else _one_of(secondary, SPECIALTIES, "secondary"))


def _mission(d: dict) -> Mission:
    target = d["target"]
    return Mission(id=_int(d["id"], "mission id"), team=_one_of(d["team"], ALL_TEAMS, "team"),
                   world=_str(d["world"], "mission world"), type=_one_of(d["type"], MISSION_TYPES, "mission type"),
                   start=_int(d["start"], "start"), end=_int(d["end"], "end"),
                   state=_one_of(d["state"], MISSION_STATES, "mission state"),
                   casualties=_int(d["casualties"], "casualties"),
                   findings=[_str(f, "finding") for f in d["findings"]],
                   target=None if target is None else _str(target, "mission target"))


def _faction(fid: str, d: dict) -> Faction:
    f = Faction(kind=_one_of(d["kind"], ("goauld", "ally"), "faction kind"),
                attention=_meter_int(d["attention"], "attention"), trust=_meter_int(d["trust"], "trust"),
                known=_bool(d["known"], "known"), source=_str(d["source"], "faction source"),
                quiet_since=_nonneg_int(d["quiet_since"], "quiet_since"))
    if f.kind != FACTION_KIND[fid]:
        raise ValueError(f"{fid} is a {FACTION_KIND[fid]}, not a {f.kind}")
    return f


def _arc(d: dict) -> ArcState:
    return ArcState(state=_one_of(d["state"], ARC_STATES, "arc state"), stage=_nonneg_int(d["stage"], "arc stage"),
                    started=_opt_int(d["started"], "arc started"), ended=_opt_int(d["ended"], "arc ended"),
                    deadline=_opt_int(d["deadline"], "arc deadline"))


def _deal(d: dict, world_ids: set[str]) -> Deal:
    return Deal(id=_int(d["id"], "deal id"), world=_one_of(d["world"], world_ids, "deal world"),
                goods=_one_of(d["goods"], GOODS, "goods"), amount=_nonneg_int(d["amount"], "deal amount"),
                next=_int(d["next"], "deal next"), left=_nonneg_int(d["left"], "deliveries left"),
                misses=_nonneg_int(d["misses"], "misses"), state=_one_of(d["state"], DEAL_STATES, "deal state"))


def _captured_drone(d: dict, world_ids: set[str]) -> CapturedDrone:
    return CapturedDrone(drone=_one_of(d["drone"], DRONES, "drone"),
                         world=_one_of(d["world"], world_ids, "captured drone world"),
                         minute=_int(d["minute"], "capture minute"), located=_bool(d["located"], "located"))


DIAL_OPS = ("malp", "uav", "recall", "depart", "search", "uplink")
PROBE_FATES = ("ok", "destroyed", "captured")  # how a probe's dial ends: rolled at the dial, applied at shutdown
UPLINK_OUTCOMES = ("full", "partial", "lost")  # how an extended report's uplink ends
SEARCHERS = ("malp", *ALL_TEAMS)               # a MALP, or the team sent to look


def _event(e, world_ids: set[str], mission_ids: set[int], deal_ids: set[int] = frozenset(),
           arc_ids: set[str] = frozenset(), team_ids: set[str] = frozenset(ALL_TEAMS)) -> None:
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

    def searcher() -> None:
        by = _one_of(need("by"), SEARCHERS, "searcher")
        if by != "malp":
            _one_of(by, team_ids, "searcher")

    def readings(v) -> None:
        if not isinstance(v, dict) or not all(isinstance(k, str) and isinstance(x, str) for k, x in v.items()):
            raise ValueError(f"{kind} readings must map text to text, got {v!r}")

    if "extended" in d:
        _bool(d["extended"], f"{kind} extended")
    if kind == "dial_out":
        op = _one_of(need("op"), DIAL_OPS, "dial-out op")
        if op in ("malp", "uav", "recall", "uplink"):
            world()
            if op == "uplink":
                _one_of(need("drone"), DRONES, "drone")
        else:
            mission()
            if op == "search":
                searcher()
    elif kind == "drone_report":
        world()
        _one_of(need("drone"), DRONES, "drone")
        _one_of(need("fate"), PROBE_FATES, "probe fate")
        readings(need("seen"))
        if "env" not in d["seen"]:
            raise ValueError(f"a drone report's readings start with env, got {d['seen']!r}")
    elif kind == "uplink":
        world()
        _one_of(need("drone"), DRONES, "drone")
        if _int(need("from"), "uplink from") > _int(need("to"), "uplink to"):
            raise ValueError("an uplink's window must not end before it starts")
    elif kind == "uplink_report":
        world()
        _one_of(need("drone"), DRONES, "drone")
        _one_of(need("outcome"), UPLINK_OUTCOMES, "uplink outcome")
        readings(need("seen"))
    elif kind == "drone_checkin":
        world()
        _one_of(need("drone"), DRONES, "drone")
    elif kind == "drone_home":
        world()
        mission()
        _one_of(need("team"), team_ids, "drone-home team")
        _one_of(need("drone"), DRONES, "drone")
    elif kind == "malp_return":                      # legacy: a report from before probes went live
        world()
        _one_of(need("drone"), DRONES, "drone")
        if "sent" in d:
            _int(d["sent"], f"{kind} sent")
    elif kind == "search_report":
        mission()
        searcher()
    elif kind == "team_return" and "mission" not in d:       # a reinforcement heading home, not a mission
        _one_of(need("team"), team_ids, "returning team")
    elif kind in ("checkin", "team_return", "overdue"):
        mission()
        if "since" in d:
            _int(d["since"], "check-in since")
    elif kind == "checkin_timeout":
        mission()
        _one_of(need("team"), team_ids, "check-in line team")
    elif kind == "faction_action":
        fid = _one_of(need("faction"), FACTION_IDS, "faction")
        if FACTION_KIND[fid] != "goauld":
            raise ValueError(f"only a Goa'uld acts against Earth, not {fid!r}")
    elif kind == "trade_delivery":
        _one_of(_int(need("deal"), "deal"), deal_ids, "deal")
    elif kind == "arc_step":
        _one_of(need("arc"), arc_ids, "arc")
        if _int(need("stage"), "arc step stage") < 1:
            raise ValueError("an arc step's stage starts at 1")
    elif "mission" in d:
        mission()


def _way_home(events: Scheduler, name: str) -> bool:
    """An offworld team on no mission is reinforcing or searching: an event brings it home."""
    return bool(events.find(lambda e: (e.kind == "team_return" and e.data.get("team") == name)
                            or (e.kind in ("dial_out", "search_report") and e.data.get("by") == name)))


def _stage_departures(teams: dict[str, Team], events: Scheduler) -> None:
    """A save from before STAGING had an assigned team offworld while its departure still waited for the gate:
    it is staging."""
    departing = {e.data["mission"] for e in events
                 if e.kind == "dial_out" and e.data.get("op") == "depart"}
    for tm in teams.values():
        if tm.status == "offworld" and tm.mission in departing:
            tm.status = "staging"


def _stage_checkins(worlds: list[World], events: Scheduler, minutes: float) -> None:
    """A save from before drone check-ins had a parked drone with nothing scheduled: it checks in for the
    first time 8 hours from here."""
    for w in worlds:
        if w.drone and not events.find(lambda e: e.kind == "drone_checkin" and e.data.get("world") == w.id):
            events.push(minutes + CHECKIN_HOURS * HOUR, "drone_checkin", {"world": w.id, "drone": w.drone})


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
        unlisted = [world_from_dict(w) for w in d["unlisted"]]
        if {w.id for w in unlisted} & world_ids:
            raise ValueError("an unlisted world is already on the dialing list")
        teams = {_one_of(n, ALL_TEAMS, "team"): _team(t) for n, t in d["teams"].items()}
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
        mode = _one_of(d["mode"], MODES, "mode")
        factions = d["factions"]
        if not isinstance(factions, dict) or set(factions) != set(FACTION_IDS):
            raise ValueError(f"factions must be exactly {sorted(FACTION_IDS)}")
        factions = {fid: _faction(fid, f) for fid, f in factions.items()}
        arcs = d["arcs"]
        if not isinstance(arcs, dict) or set(arcs) - set(ARC_IDS):
            raise ValueError(f"unknown arcs in {sorted(arcs)!r}")
        arcs = {aid: _arc(a) for aid, a in arcs.items()}
        deals = [_deal(x, world_ids) for x in d["deals"]]
        deal_ids = {x.id for x in deals}
        if len(deal_ids) != len(deals):
            raise ValueError("deal ids must be unique")
        captured = [_captured_drone(x, world_ids) for x in d["captured_drones"]]
        events = Scheduler.from_list(d["events"], d["event_seq"])
        for e in events:
            _event(e, world_ids, mission_id_set, deal_ids, set(arcs), set(teams))
        _stage_departures(teams, events)
        _stage_checkins(worlds, events, minutes)
        for name, tm in teams.items():
            if tm.status == "offworld" and tm.mission is None and not _way_home(events, name):
                raise ValueError(f"{name} is offworld with no mission and nothing bringing it home")
        stock = {k: _nonneg_int(v, "stock") for k, v in d["stock"].items()}
        meters = {k: _meter_int(v, "meters") for k, v in d["meters"].items()}
        if set(d["record"]) != set(_record()):
            raise ValueError(f"record keys must be {sorted(_record())}, got {sorted(d['record'])}")
        record = {k: _int(v, "record") for k, v in d["record"].items()}
        if not isinstance(d["ledger"], dict) or set(d["ledger"]) != set(LEDGER):
            raise ValueError(f"ledger keys must be {sorted(LEDGER)}")
        ledger = {k: _nonneg_int(v, "ledger") for k, v in d["ledger"].items()}
        reserve = d["reserve"]
        if not isinstance(reserve, dict) or set(reserve) != set(DRONES):
            raise ValueError(f"reserve must cover {DRONES}")
        reserve = {k: _nonneg_int(v, "reserve") for k, v in reserve.items()}
        if max(reserve.values()) > RESERVE_MAX:
            raise ValueError(f"a reserve is at most {RESERVE_MAX}")
        reviews = [(_int(m, "review minute"), _int(g, "review grant"), _str(s, "review summary"))
                   for m, g, s in d["reviews"]]
        alarms = d["alarms"]
        if not isinstance(alarms, list):
            raise ValueError("alarms must be a list of tables")
        alarms = [_alarm(a) for a in alarms]
        over = d["over"]
        if over is not None and not isinstance(over, str):
            raise ValueError(f"over must be null or a string, got {over!r}")
        ending = d["ending"]
        if ending is not None:
            _one_of(ending, ENDINGS, "ending")
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
            mode=mode, difficulty=_one_of(d["difficulty"], DIFFICULTIES, "difficulty"),
            seed=_int(d["seed"], "seed"), pace=_one_of(d["pace"], PACES, "pace"), minutes=minutes,
            events=events, gate_until=_int(d["gate_until"], "gate_until"),
            worlds={w.id: w for w in worlds}, teams=teams, missions=missions,
            stock=stock, orders=orders.validate(d["orders"]), meters=meters,
            inventory=set(inventory), used=set(used), alarms=alarms,
            record=record, rng_state=rng_state, over=over,
            funding=_nonneg_int(d["funding"], "funding"), naquadah=_nonneg_int(d["naquadah"], "naquadah"),
            upgrades={_one_of(u, UPGRADE_IDS, "upgrade") for u in _str_list(d["upgrades"], "upgrades")},
            reserve=reserve, factions=factions, arcs=arcs, deals=deals, captured_drones=captured,
            ledger=ledger, reviews=reviews, unlisted={w.id: w for w in unlisted},
            won=_opt_int(d["won"], "won"), ending=ending,
            hints=set(_str_list(d["hints"], "hints")))
    except (KeyError, TypeError, AttributeError, IndexError) as e:
        raise ValueError(str(e)) from e
    if not set(CORE_TEAMS) <= set(c.teams) or set(c.meters) != set(METERS) or set(c.stock) != set(STOCK) \
            or len(c.worlds) != len(worlds) or not c.worlds:
        raise ValueError("save file does not describe a valid campaign")
    return c


def upgrade_v2(d: dict) -> dict:
    """A Stage 1 (version 2) save brought up to version 3, with nothing in flight lost. Returns a new dict and
    leaves the input alone; from_dict checks the result."""
    if not isinstance(d, dict) or d.get("version") != 2:
        raise ValueError("not a version 2 save")
    d = copy.deepcopy(d)
    mode = d.get("mode")
    d["version"] = VERSION
    for t in d.get("teams", {}).values():
        if isinstance(t, dict):
            t.setdefault("secondary", None)
    for m in d.get("missions", []):
        if isinstance(m, dict):
            m.setdefault("target", None)
    minutes = d.get("minutes")
    now = minutes if isinstance(minutes, (int, float)) and not isinstance(minutes, bool) \
        and math.isfinite(minutes) else START
    due = START + REVIEW_EVERY
    while due <= now:
        due += REVIEW_EVERY
    seq = d.get("event_seq", 0)
    seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else 0
    events = d.get("events", [])
    if isinstance(events, list):
        seq = max([seq, *(e.get("seq", 0) + 1 for e in events if isinstance(e, dict)
                          and isinstance(e.get("seq"), int))])
        d["events"] = [*events, {"due": due, "seq": seq, "kind": "funding_review", "data": {}}]
        d["event_seq"] = seq + 1
    unlisted = [place(n) for n in ARC_UNLISTED] if mode == "campaign" else []
    d.update({
        "funding": 500, "naquadah": 0, "upgrades": ["uav_program"], "reserve": {"malp": 2, "uav": 0},
        "factions": {fid: asdict(f) for fid, f in _factions().items()},
        "arcs": {a: asdict(ArcState()) for a in ARC_IDS} if mode == "campaign" else {},
        "deals": [], "captured_drones": [], "ledger": _ledger(), "reviews": [],
        "unlisted": [world_to_dict(w) for w in unlisted],     # P1 designations: no v2 world has one
        "won": None, "ending": None, "hints": [],
    })
    return d
