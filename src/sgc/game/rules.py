"""Campaign rules: conditions, effects and odds, parsed once from content strings.

Conditions and effects are short strings in scenario files. They are parsed at load
time into callables taking (campaign, bindings); bindings fill {team} and the other
placeholders with names chosen when the scenario is drawn.
"""
from __future__ import annotations

import math
import operator
import random
import re
from dataclasses import dataclass, field
from typing import Callable

from . import arcs, economy, factions, trade
from . import world as wd
from .clock import DAY, HOUR
from .state import (ALL_TEAMS, ARC_IDS, ARC_STATES, CORE_TEAMS, GOODS, METERS, MISSION_TYPES, RANK_NAMES,
                    SPECIALTIES, STATUSES, UPGRADE_IDS, Campaign, CapturedDrone, Mission, Team,
                    available_teams, has_specialty, rank, rank_index, team_names)

FLAGS = frozenset({"ally.tokra", "ally.asgard", "ally.tollan", "ally.nox", "ally.jaffa",
                   "tech.zat", "tech.naquadah_generator", "tech.lrs"})
SINGLE_USE = frozenset({"ally.asgard", "ally.nox"})
FLAG_NAMES = {"ally.tokra": "THE TOK'RA", "ally.asgard": "THE ASGARD", "ally.tollan": "THE TOLLAN",
              "ally.nox": "THE NOX", "ally.jaffa": "THE FREE JAFFA", "tech.zat": "ZAT'NIK'TEL",
              "tech.naquadah_generator": "NAQUADAH GENERATOR", "tech.lrs": "LONG-RANGE SENSORS"}
TEAM_SLOTS = ("{team}", "{captive}")
FACTION_SLOTS = ("{faction}", "{owner}")
EFFECT_STATUSES = ("base", "offworld", "injured", "captured", "lost")     # forming and training: roster.py only
ARC_ACTIONS = ("start", "advance", "resolve", "fail")
EXPLORED = "ALL ADDRESSES VISITED — RE-SURVEY, STUDY RUINS, ASK ALLIES"
_HIDDEN_WORLD = ("env", "inhabitants", "feature")
WORLD_SLOT = "{world}"
SCHEDULABLE = ("incoming",)
REINFORCE = 6 * HOUR            # a reinforcing team stays out this long
_DESIGNATION = re.compile(r"^P[0-9A-Z]{2}-\d{3}$")
_REVEAL_NAME = re.compile(r'^reveal name (\S+)(?: "([^"]+)")? from (\S+)$')
LOSS = {"recruit": 0.5, "officer": 1.0, "commander": 1.5}
GAIN = {"recruit": 1.25, "officer": 1.0, "commander": 0.75}
STAND_DOWN = 12 * HOUR           # after an IDC is reissued
INJURED = 2 * DAY
CAPTIVE = 5 * DAY                # a captured team is presumed lost after this
REFORM = 3 * DAY                 # a lost team's number is re-formed, Green, after this
_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq}


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Cond:
    text: str
    fn: Callable[[Campaign, dict], bool] = field(compare=False, repr=False)

    def __call__(self, c: Campaign, bind: dict) -> bool:
        return self.fn(c, bind)


@dataclass(frozen=True)
class Effect:
    text: str
    fn: Callable[[Campaign, dict], list[str]] = field(compare=False, repr=False)

    def __call__(self, c: Campaign, bind: dict) -> list[str]:
        return self.fn(c, bind)


@dataclass(frozen=True)
class Mod:
    cond: Cond
    delta: int


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return "?"


def fill(text: str, bind: dict) -> str:
    """Fill {placeholders} from the bindings; unknown ones become '?'."""
    return text.format_map(_Blank(bind))


def has_flag(c: Campaign, flag: str) -> bool:
    return flag in c.inventory and not (flag in SINGLE_USE and flag in c.used)


# ------------------------------------------------------------------ parsing helpers

def _int(tok: str, text: str) -> int:
    try:
        return int(tok)
    except ValueError:
        raise RuleError(f'"{text}": expected a whole number, got "{tok}"') from None


def _signed(tok: str, text: str) -> int:
    if not tok or tok[0] not in "+-":
        raise RuleError(f'"{text}": expected +N or -N, got "{tok}"')
    return _int(tok, text)


def _team_tok(tok: str, text: str) -> str:
    if tok in ALL_TEAMS or tok in TEAM_SLOTS:
        return tok
    raise RuleError(f'"{text}": unknown team "{tok}"')


def _resolve(tok: str, bind: dict) -> str:
    return bind.get(tok[1:-1], "?") if tok.startswith("{") else tok


def _world_tok(tok: str, text: str) -> str:
    if tok == WORLD_SLOT or _DESIGNATION.match(tok) or (tok.startswith("@") and tok[1:] in wd.PLACES):
        return tok
    raise RuleError(f'"{text}": expected {{world}}, a designation like P3X-866 or @Name of a canon world, '
                    f'got "{tok}"')


def _world(c: Campaign, tok: str, bind: dict) -> wd.World | None:
    """A world on the dialing list (an unlisted arc world isn't known yet, so it's None here)."""
    if tok.startswith("@"):
        return c.worlds.get(wd.place_id(tok[1:]))
    return c.worlds.get(bind.get("world_id", "") if tok == WORLD_SLOT else tok)


def _place(tok: str, text: str) -> str:
    if not tok.startswith("@") or tok[1:] not in wd.PLACES:
        raise RuleError(f'"{text}": expected @Name of a canon world, got "{tok}"')
    return tok[1:]


_KIND_WORDS = {"goauld": "a Goa'uld", "ally": "an ally"}


def _faction_tok(tok: str, text: str, kind: str | None = None) -> str:
    """A faction id or slot; a literal id must be of `kind` ("goauld" or "ally") when one is given."""
    if tok in FACTION_SLOTS:
        return tok
    if tok not in wd.FACTION_IDS:
        raise RuleError(f'"{text}": unknown faction "{tok}"')
    if kind is not None and wd.FACTION_KIND[tok] != kind:
        raise RuleError(f'"{text}": "{tok}" isn\'t {_KIND_WORDS[kind]}')
    return tok


def _kind_for(what: str) -> str:
    """Trust belongs to allies; attention and stage to the Goa'uld."""
    return "ally" if what == "trust" else "goauld"


def _fid(c: Campaign, tok: str, bind: dict) -> str | None:
    """A faction id from a token: an id, {faction} (the scenario's faction), or {owner} (the world's holder)."""
    if tok == "{faction}":
        return bind.get("faction_id")
    if tok == "{owner}":
        return factions.owner_of(c, bind.get("world_id", ""))
    return tok


def held(c: Campaign, wid: str) -> bool:
    """A captured team, or a located captured drone, is held on this world (both known to the SGC)."""
    return any(t.status == "captured" and t.where == wid for t in c.teams.values()) \
        or any(d.world == wid and d.located for d in c.captured_drones)


def hidden(text: str) -> bool:
    """A condition on something the SGC may not know: it picks which scenario plays, so only `when` may use it."""
    t = text.split()
    return ((len(t) >= 3 and t[0] == "world" and t[2] in _HIDDEN_WORLD)
            or (bool(t) and t[0] in ("attention", "stage", "is"))
            or "{owner}" in t)                                  # who holds a world is itself hidden


def attention(c: Campaign, fid: str | None, n: int) -> list[str]:
    """Change a Goa'uld's attention; an arc of theirs may reach its endgame."""
    if fid is None:
        return []
    return factions.adjust_attention(c, fid, n) + arcs.on_attention(c, fid)


def _one(tok: str, allowed, what: str, text: str) -> str:
    if tok not in allowed:
        raise RuleError(f'"{text}": unknown {what} "{tok}"')
    return tok


# ------------------------------------------------------------------ conditions

def _cond_body(t: list[str], text: str) -> Callable[[Campaign, dict], bool] | None:
    """Parse a condition; None if it isn't one this module knows."""
    if len(t) == 1:
        if t[0] in FLAGS:
            flag = t[0]
            return lambda c, b: has_flag(c, flag)
        if t[0] == "any_compromised_idc":
            return lambda c, b: any(tm.idc == "compromised" and tm.status != "lost" for tm in c.teams.values())
        if t[0] == "any_captured":
            return lambda c, b: any(tm.status == "captured" for tm in c.teams.values())
        if t[0] == "team_available":
            return lambda c, b: bool(available_teams(c))
    if len(t) == 2 and t[0] == "not" and t[1] in FLAGS:
        flag = t[1]
        return lambda c, b: not has_flag(c, flag)
    if len(t) == 3 and t[1] in _OPS and (t[0] in METERS or t[0] in ("day", "funding", "naquadah")):
        name, op, n = t[0], _OPS[t[1]], _int(t[2], text)
        if name == "day":
            return lambda c, b: op(int(c.minutes // DAY) + 1, n)
        if name in ("funding", "naquadah"):
            return lambda c, b: op(getattr(c, name), n)
        return lambda c, b: op(c.meters[name], n)
    if len(t) == 3 and t[0] == "team":
        team = _team_tok(t[1], text)
        if t[2] not in STATUSES:
            raise RuleError(f'"{text}": unknown team status "{t[2]}"')
        status = t[2]
        return lambda c, b: (tm := c.teams.get(_resolve(team, b))) is not None and tm.status == status
    if len(t) == 4 and t[0] == "team" and t[2] == "specialty":
        team, spec = _team_tok(t[1], text), _one(t[3], SPECIALTIES, "specialty", text)
        return lambda c, b: (tm := c.teams.get(_resolve(team, b))) is not None and has_specialty(tm, spec)
    if len(t) == 4 and t[0] == "rank" and t[2] in _OPS:
        team, op, need = _team_tok(t[1], text), _OPS[t[2]], RANK_NAMES.index(_one(t[3], RANK_NAMES, "rank", text))
        return lambda c, b: (tm := c.teams.get(_resolve(team, b))) is not None and op(rank_index(tm), need)
    if len(t) == 2 and t[0] == "known":
        tok = _world_tok(t[1], text)
        return lambda c, b: _world(c, tok, b) is not None
    if len(t) == 3 and t[0] == "status":
        tok, status = _world_tok(t[1], text), _one(t[2], wd.STATUSES, "world status", text)
        return lambda c, b: (w := _world(c, tok, b)) is not None and w.status == status
    if len(t) == 3 and t[0] == "unlocked":
        option, tok = _one(t[1], MISSION_TYPES, "mission type", text), _world_tok(t[2], text)
        return lambda c, b: (w := _world(c, tok, b)) is not None and option in w.options
    if len(t) == 4 and t[0] == "world" and t[2] in ("env", "inhabitants", "feature"):
        tok = _world_tok(t[1], text)
        if t[2] == "env":
            env = _one(t[3], wd.ENVIRONMENTS, "environment", text)
            return lambda c, b: (w := _world(c, tok, b)) is not None and w.env == env
        if t[2] == "inhabitants":
            who = _one(t[3], wd.INHABITANTS, "inhabitants", text)
            return lambda c, b: (w := _world(c, tok, b)) is not None and w.inhabitants == who
        feat = _one(t[3], wd.FEATURES, "feature", text)
        return lambda c, b: (w := _world(c, tok, b)) is not None and feat in w.features
    if len(t) == 2 and t[0] == "aware":
        tok = _faction_tok(t[1], text)
        return lambda c, b: (fid := _fid(c, tok, b)) is not None and c.factions[fid].known
    if len(t) == 2 and t[0] == "upgrade":
        uid = _one(t[1], UPGRADE_IDS, "upgrade", text)
        return lambda c, b: uid in c.upgrades
    if len(t) == 2 and t[0] == "held":
        tok = _world_tok(t[1], text)
        return lambda c, b: (w := _world(c, tok, b)) is not None and held(c, w.id)
    if len(t) == 4 and t[0] in ("trust", "attention") and t[2] in _OPS:
        tok, op, n, attr = _faction_tok(t[1], text, _kind_for(t[0])), _OPS[t[2]], _int(t[3], text), t[0]
        return lambda c, b: (fid := _fid(c, tok, b)) is not None and op(getattr(c.factions[fid], attr), n)
    if len(t) == 3 and t[0] == "stage":
        tok, stage = _faction_tok(t[1], text, "goauld"), _one(t[2], factions.STAGE_NAMES, "stage", text)
        return lambda c, b: (fid := _fid(c, tok, b)) is not None and factions.stage_of(c, fid) == stage
    if len(t) == 3 and t[0] == "arc":
        aid, state = _one(t[1], ARC_IDS, "arc", text), _one(t[2], ARC_STATES, "arc state", text)
        return lambda c, b: (a := c.arcs.get(aid)) is not None and a.state == state
    if len(t) == 5 and t[0] == "arc" and t[2] == "stage" and t[3] in _OPS:
        aid, op, n = _one(t[1], ARC_IDS, "arc", text), _OPS[t[3]], _int(t[4], text)
        return lambda c, b: (a := c.arcs.get(aid)) is not None and a.state == "active" and op(a.stage, n)
    if len(t) == 3 and t[0] == "is":
        tok, wid = _world_tok(t[1], text), wd.place_id(_place(t[2], text))
        return lambda c, b: (w := _world(c, tok, b)) is not None and w.id == wid
    return None


def parse_cond(text: str) -> Cond:
    t = text.split()
    if not t:
        raise RuleError("empty condition")
    fn = _cond_body(t, text)
    if fn is None:
        raise RuleError(f'unknown condition "{text}"')
    return Cond(text, fn)


def parse_mod(text: str) -> Mod:
    """'tech.zat +15' or 'personnel < 40 -15': a condition, then a signed odds change."""
    parts = text.rsplit(None, 1)
    if len(parts) != 2:
        raise RuleError(f'"{text}": expected "<condition> +N"')
    return Mod(parse_cond(parts[0]), _signed(parts[1], text))


def check_all(conds, c: Campaign, bind: dict) -> bool:
    return all(cond(c, bind) for cond in conds)


def odds(base: int, mods, c: Campaign, bind: dict, bonus: int = 0) -> int:
    return max(5, min(95, base + bonus + sum(m.delta for m in mods if m.cond(c, bind))))


# ------------------------------------------------------------------ effects

def scaled(c: Campaign, delta: int) -> int:
    """Scale a meter change by difficulty, rounding half away from zero.

    A nonzero delta never scales to a no-op: the magnitude is at least 1.
    """
    if delta == 0:
        return 0
    x = delta * (LOSS if delta < 0 else GAIN)[c.difficulty]
    n = int(math.copysign(math.floor(abs(x) + 0.5), x))
    return n if n != 0 else (1 if delta > 0 else -1)


def apply_meter(c: Campaign, name: str, delta: int) -> list[str]:
    """Change a meter by difficulty; Security losses are first cut by the iris upgrades."""
    if name == "security" and delta < 0:
        delta = -max(1, int(-delta * economy.defense(c) + 0.5))
    before = c.meters[name]
    c.meters[name] = max(0, min(100, before + scaled(c, delta)))
    d = c.meters[name] - before
    if name == "personnel" and d < 0:
        c.record["personnel_lost"] += -d
    return [f"{name.upper()} {d:+d}"] if d else []


def _meter(name: str, delta: int):
    return lambda c, b: apply_meter(c, name, delta)


def _breach(n: int):
    def fn(c: Campaign, b: dict) -> list[str]:
        if c.meters["security"] <= 0:
            c.over = "The SGC was overrun."
            return ["SECURITY BREACHED — THE BASE HAS FALLEN"]
        economy.note(c, "breaches")
        return ["SECURITY BREACH", *apply_meter(c, "security", -n)]
    return fn


def _team(tok: str, status: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        name = _resolve(tok, b)
        tm = c.teams.get(name)
        if tm is None:                  # a number not on the roster (never commissioned, or disbanded)
            return []
        prev = tm.status
        if status == "lost" and prev != "lost" and has_flag(c, "ally.nox"):
            c.used.add("ally.nox")
            tm.status, tm.until = "injured", c.now + INJURED
            return [f"THE NOX RETURNED {name} ALIVE"]
        if status == "captured" and prev in ("captured", "lost"):
            return []                   # already held, or gone: nothing more to take
        tm.status = status
        if status == "captured":
            tm.idc, tm.until = "compromised", c.now + CAPTIVE
            tm.where = b.get("world_id", tm.where)
            economy.note(c, "captured")
            msgs = [f"{name} CAPTURED ON {b.get('world', 'AN UNKNOWN WORLD').upper()}"]
            w = c.worlds.get(tm.where)
            if w is not None:
                if "rescue" not in w.options:
                    w.options.append("rescue")
                msgs += attention(c, wd.faction_id(w.owner), 15)
            return msgs
        if status == "lost":
            if prev != "lost":
                c.record["teams_lost"] += 1
                economy.note(c, "lost")
            tm.idc, tm.until, tm.where = "revoked", c.now + REFORM, ""
            return [f"{name} LOST"]
        if status == "injured":
            tm.until = c.now + INJURED
            return [f"{name} TO THE INFIRMARY"]
        if status == "base" and prev == "captured":
            tm.idc, tm.where, tm.until = "valid", "", c.now + STAND_DOWN
            return [f"{name} RESCUED — NEW IDC ISSUED"]
        if status == "base" and prev == "lost":
            tm.idc, tm.until = "valid", 0
            return []
        return []
    return fn


def stands_down(tm: Team) -> bool:
    """Does revoking this team's IDC run a 12-hour stand-down? Only at base, or on top of an injured or
    re-forming team's own timer (whichever is longer). A captured team's capture clock is never touched."""
    return tm.status == "base" or (tm.status in ("injured", "lost") and tm.until > 0)


def revoke(c: Campaign, name: str) -> list[str]:
    """Revoke a team's IDC and issue a new one; see stands_down for the timer."""
    tm = c.teams[name]
    tm.idc = "valid"
    if stands_down(tm):
        tm.until = max(tm.until, c.now + STAND_DOWN)
    return [f"{name} IDC REVOKED — NEW CODE ISSUED"]


def _idc(tok: str, action: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        name = _resolve(tok, b)
        if name not in c.teams:
            return []
        if action == "compromise":
            c.teams[name].idc = "compromised"
            return []
        return revoke(c, name)
    return fn


def _gain(flag: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        if has_flag(c, flag):
            return []
        c.inventory.add(flag)
        c.used.discard(flag)
        economy.note(c, "allies" if flag.startswith("ally.") else "tech")
        kind = "ALLIANCE" if flag.startswith("ally.") else "NEW TECHNOLOGY"
        return [f"{kind}: {FLAG_NAMES[flag]}"]
    return fn


def _use(flag: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        c.used.add(flag)
        return [f"{FLAG_NAMES[flag]} CALLED IN"]
    return fn


def _game_over(message: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        c.over = fill(message, b)
        return [c.over.upper()]
    return fn


def set_world_status(c: Campaign, w: wd.World, status: str) -> list[str]:
    """Move a world's status (see world.set_status), counting each world surveyed once for the records.

    A survey still counts on a hostile world, even though its status then stays HOSTILE.
    """
    before = w.status
    changed = wd.set_status(w, status)
    survey = status == "surveyed" or (status == "contact" and before in ("unexplored", "probed"))
    if survey and not w.surveyed and (changed or w.status in ("hostile", "contact")):
        w.surveyed = True
        c.record["surveyed"] += 1
    return [f"{w.name.upper()}: {status.upper()}"] if changed else []


def new_address(c: Campaign, found: str) -> wd.World:
    """A generated world added to the dialing list; the same campaign always gets the same worlds."""
    rng = random.Random(f"{c.seed}/{len(c.worlds)}")
    w = wd.generate(rng, set(c.worlds) | set(c.unlisted), found=found,
                    owner_odds=1.0 if c.mode == "campaign" else 0.3)
    c.worlds[w.id] = w
    return w


_MISSION_TIMED_KINDS = ("checkin", "team_return", "dial_out", "overdue")


def withdraw_search(c: Campaign, d: dict) -> list[str]:
    """Take back a search queued for a missing team: the MALP goes back to stores, a helper team stands down."""
    if d["by"] == "malp":
        return stow(c, "malp")
    ht = c.teams[d["by"]]
    ht.status, ht.where = "base", ""
    return []


def recall(c: Campaign, m: Mission) -> list[str]:
    """End an active mission early: its pending check-ins and dials are cancelled, home now. A search still
    waiting for the gate is withdrawn as the QUEUE tab's cancel would (its MALP or helper team back at base)."""
    searches = c.events.remove(lambda e: e.kind == "dial_out" and e.data.get("op") == "search"
                               and e.data.get("mission") == m.id)
    c.events.cancel(lambda e: e.data.get("mission") == m.id and e.kind in _MISSION_TIMED_KINDS)
    m.end, m.state = c.now, "aborted"
    tm = c.teams.get(m.team)
    if tm is not None and tm.status == "staging" and tm.mission == m.id:     # it never left: it stands by
        tm.status, tm.where, tm.mission = "base", "", None
        return [line for ev in searches for line in withdraw_search(c, ev.data)]
    c.events.push(c.now, "team_return", {"mission": m.id})
    return [line for ev in searches for line in withdraw_search(c, ev.data)]


def _reveal_address(c: Campaign, b: dict) -> list[str]:
    w = new_address(c, f"intel from {b['team']}" if b.get("team") else "intel")
    economy.note(c, "intel")
    return [f"NEW ADDRESS: {w.id}"]


def _reveal_name(tok: str, name: str | None, source: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        w = _world(c, tok, b)
        if w is None:
            return []
        before = w.id
        if name is None:
            learned = wd.reveal_name(w, source, c.now)
        else:
            learned = name if wd.learn_name(w, name, wd.SOURCE_TEXT[source], c.now) else None
        if learned:
            economy.note(c, "intel")
        return [f"{before} IS CALLED {learned.upper()} BY {wd.SOURCE_TEXT[source].upper()}"] if learned else []
    return fn


def _unlock(option: str, tok: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        w = _world(c, tok, b)
        if w is None or option in w.options:
            return []
        w.options.append(option)
        return [f"{option.upper()} MISSIONS POSSIBLE ON {w.name.upper()}"]
    return fn


def _status(tok: str, status: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        w = _world(c, tok, b)
        return set_world_status(c, w, status) if w is not None else []
    return fn


def _xp(tok: str, n: int):
    def fn(c: Campaign, b: dict) -> list[str]:
        name = _resolve(tok, b)
        tm = c.teams.get(name)
        if tm is None:
            return []
        before = rank(tm)
        tm.xp += n
        return [f"{name} PROMOTED: {rank(tm).upper()}"] if rank(tm) != before else []
    return fn


def _schedule(kind: str, hours: int):
    def fn(c: Campaign, b: dict) -> list[str]:
        c.events.push(c.now + hours * HOUR, kind)
        return []
    return fn


def clear_uplink(c: Campaign, wid: str) -> None:
    """Cancel a world's pending uplink: its timer, its gate dial-out, or a rolled report waiting for the gate
    to shut. Call this wherever a world's drone leaves outside the uplink's own path, so no stale uplink can
    later fire on a different drone sent to the same world."""
    c.events.cancel(lambda e: e.data.get("world") == wid and (
        e.kind in ("uplink", "uplink_report") or (e.kind == "dial_out" and e.data.get("op") == "uplink")))


def _drone(tok: str, fate: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        w = _world(c, tok, b)
        if w is None or w.drone is None:
            return []
        drone, w.drone = w.drone, None
        clear_uplink(c, w.id)
        msgs = [f"{drone.upper()} ON {w.name.upper()} {fate.upper()}"]
        if fate == "captured":
            msgs += capture_drone(c, w, drone)
        return msgs
    return fn


def capture_drone(c: Campaign, w: wd.World, drone: str) -> list[str]:
    """A drone taken on a world: held there until recovered; the world turns hostile; its Goa'uld notices."""
    c.captured_drones.append(CapturedDrone(drone, w.id, c.now))
    return set_world_status(c, w, "hostile") + attention(c, wd.faction_id(w.owner), 10)


def _recall(tok: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        name = _resolve(tok, b)
        tm = c.teams.get(name)
        if tm is None:
            return []
        m = c.mission(tm.mission)
        if m is None or m.state != "active":
            return []
        return [f"{name} RECALLED", *recall(c, m)]
    return fn


def _reinforce(tok: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        name = _resolve(tok, b)
        target = c.teams.get(name)
        if target is None or target.status != "offworld":
            return []
        free = [t for t in available_teams(c) if t != name]
        if not free:
            return []
        helper, tm = free[0], c.teams[free[0]]
        tm.status, tm.where = "offworld", target.where
        c.events.push(c.now + REINFORCE, "team_return", {"team": helper})
        return [f"{helper} SENT TO REINFORCE {name}"]
    return fn


def _faction_delta(kind: str, tok: str, n: int):
    def fn(c: Campaign, b: dict) -> list[str]:
        fid = _fid(c, tok, b)
        if fid is None:
            return []
        return factions.adjust_trust(c, fid, n) if kind == "trust" else attention(c, fid, n)
    return fn


def _resource(name: str, n: int):
    def fn(c: Campaign, b: dict) -> list[str]:
        before = getattr(c, name)
        setattr(c, name, max(0, before + n))
        d = getattr(c, name) - before
        return [f"{name.upper()} {d:+d}"] if d else []
    return fn


def _incident(n: int):
    def fn(c: Campaign, b: dict) -> list[str]:
        economy.note(c, "incidents", n)
        return ["THE NID HAS TAKEN AN INTEREST"]
    return fn


def _arc(aid: str, action: str):
    return lambda c, b: getattr(arcs, action)(c, aid)


def _deal(tok: str, goods: str, n: int):
    def fn(c: Campaign, b: dict) -> list[str]:
        w = _world(c, tok, b)
        return trade.new_deal(c, w.id, goods, n) if w is not None else []
    return fn


def _reveal_place(name: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        wid = wd.place_id(name)
        if wid in c.worlds:
            return []
        w = c.unlisted.pop(wid, None) or wd.place(name)
        w.found = f"intel from {b['team']}" if b.get("team") else "intel"
        c.worlds[wid] = w
        economy.note(c, "intel")
        return [f"NEW ADDRESS: {w.id}"]
    return fn


def _reveal_faction(tok: str, source: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        fid = _fid(c, tok, b)
        if fid is None:
            return []
        lines = factions.know(c, fid, wd.SOURCE_TEXT[source])
        if lines:
            economy.note(c, "intel")
        w = c.worlds.get(b.get("world_id", ""))
        if tok == "{owner}" and w is not None:
            w.seen["owner"] = wd.faction_name(fid)           # the world file now says who holds it
        return lines
    return fn


def _locate(tok: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        w = _world(c, tok, b)
        if w is None:
            return []
        out = []
        for d in c.captured_drones:
            if d.world == w.id and not d.located:
                d.located = True
                out.append(f"THE {d.drone.upper()} TAKEN ON {w.name.upper()} IS HELD THERE")
        if out and "recover" not in w.options:
            w.options.append("recover")
        return out
    return fn


def _recover(tok: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        w = _world(c, tok, b)
        d = next((d for d in c.captured_drones if w is not None and d.world == w.id and d.located), None)
        if d is None:
            return []
        c.captured_drones.remove(d)
        return [f"{d.drone.upper()} RECOVERED FROM {w.name.upper()}", *stow(c, d.drone)]
    return fn


def _effect_body(body: str, text: str):
    t = body.split()
    if not t:
        raise RuleError("empty effect")
    if t[0] == "reveal":
        if t == ["reveal", "address"]:
            return _reveal_address
        if len(t) == 3 and t[1] == "address":
            return _reveal_place(_place(t[2], text))
        if len(t) == 5 and t[1] == "faction" and t[3] == "from":
            return _reveal_faction(_faction_tok(t[2], text), _one(t[4], wd.NAME_SOURCES, "name source", text))
        m = _REVEAL_NAME.match(body)
        if not m:
            raise RuleError(f'"{text}": expected reveal address, or reveal name {{world}} ["Name"] from <source>')
        return _reveal_name(_world_tok(m[1], text), m[2], _one(m[3], wd.NAME_SOURCES, "name source", text))
    if t[0] == "unlock" and len(t) == 3:
        return _unlock(_one(t[1], MISSION_TYPES, "mission type", text), _world_tok(t[2], text))
    if t[0] == "status" and len(t) == 3:
        return _status(_world_tok(t[1], text), _one(t[2], wd.STATUSES, "world status", text))
    if t[0] == "xp" and len(t) == 3:
        n = _signed(t[2], text)
        if n <= 0:
            raise RuleError(f'"{text}": xp can only go up')
        return _xp(_team_tok(t[1], text), n)
    if t[0] == "schedule" and len(t) == 4 and t[2] == "in" and t[3].endswith("h"):
        hours = _int(t[3][:-1], text)
        if hours <= 0:
            raise RuleError(f'"{text}": schedule needs a positive number of hours')
        return _schedule(_one(t[1], SCHEDULABLE, "event", text), hours)
    if t[0] == "drone" and len(t) == 3:
        return _drone(_world_tok(t[1], text), _one(t[2], ("lost", "captured"), "drone fate", text))
    if t[0] == "recall" and len(t) == 2:
        return _recall(_team_tok(t[1], text))
    if t[0] == "reinforce" and len(t) == 2:
        return _reinforce(_team_tok(t[1], text))
    if t[0] in METERS and len(t) == 2:
        return _meter(t[0], _signed(t[1], text))
    if t[0] == "breach" and len(t) == 2:
        n = _int(t[1], text)
        if n <= 0:
            raise RuleError(f'"{text}": breach needs a positive number')
        return _breach(n)
    if t[0] == "team" and len(t) == 3:
        team = _team_tok(t[1], text)
        if t[2] not in EFFECT_STATUSES:
            raise RuleError(f'"{text}": a team can be set only to {", ".join(EFFECT_STATUSES)}, not "{t[2]}"')
        return _team(team, t[2])
    if t[0] == "idc" and len(t) == 3 and t[2] in ("revoke", "compromise"):
        return _idc(_team_tok(t[1], text), t[2])
    if t[0] == "gain" and len(t) == 2 and t[1] in FLAGS:
        return _gain(t[1])
    if t[0] == "use" and len(t) == 2 and t[1] in SINGLE_USE:
        return _use(t[1])
    if t[0] == "game_over" and len(t) >= 2:
        return _game_over(body.split(None, 1)[1])
    if t[0] in ("attention", "trust") and len(t) == 3:
        return _faction_delta(t[0], _faction_tok(t[1], text, _kind_for(t[0])), _signed(t[2], text))
    if t[0] in ("funding", "naquadah") and len(t) == 2:
        return _resource(t[0], _signed(t[1], text))
    if t[0] == "incident" and len(t) == 2:
        n = _signed(t[1], text)
        if n <= 0:
            raise RuleError(f'"{text}": incident can only go up')
        return _incident(n)
    if t[0] == "arc" and len(t) == 3:
        return _arc(_one(t[1], ARC_IDS, "arc", text), _one(t[2], ARC_ACTIONS, "arc action", text))
    if t[0] == "deal" and len(t) == 4:
        n = _int(t[3], text)
        if n <= 0:
            raise RuleError(f'"{text}": a deal delivers a positive amount')
        return _deal(_world_tok(t[1], text), _one(t[2], GOODS, "goods", text), n)
    if t[0] == "locate" and len(t) == 2:
        return _locate(_world_tok(t[1], text))
    if t[0] == "recover" and len(t) == 2:
        return _recover(_world_tok(t[1], text))
    raise RuleError(f'unknown effect "{text}"')


def parse_effect(text: str) -> Effect:
    if text.split(None, 1)[:1] == ["game_over"]:      # a game_over message may itself say "unless"
        return Effect(text, _effect_body(text, text))
    body, sep, guard = text.partition(" unless ")
    fn = _effect_body(body.strip(), text)
    if not sep:
        return Effect(text, fn)
    guard = guard.strip()
    if guard not in FLAGS:
        raise RuleError(f'"{text}": unknown flag "{guard}" after unless')
    return Effect(text, lambda c, b: [] if has_flag(c, guard) else fn(c, b))


def apply_all(effects, c: Campaign, bind: dict) -> list[str]:
    out: list[str] = []
    for e in effects:
        out += e(c, bind)
        if c.over:
            break
    return out


# ------------------------------------------------------------------ campaign bookkeeping

def teams_matching(c: Campaign, which: str, bind: dict | None = None) -> list[str]:
    names = team_names(c)
    if which == "base":
        return available_teams(c)
    if which == "compromised":
        return [t for t in names if c.teams[t].idc == "compromised" and c.teams[t].status != "lost"]
    if which == "captured":
        return [t for t in names if c.teams[t].status == "captured"]
    if which == "territory":                     # out on a mission on one of this faction's worlds
        fid = (bind or {}).get("faction_id")
        return [t for t in names if c.teams[t].status == "offworld" and c.teams[t].mission is not None
                and fid is not None and factions.owner_of(c, c.teams[t].where) == fid]
    return [t for t in names if c.teams[t].status != "lost"]


def deployed(c: Campaign, drone: str) -> int:
    """Drones of this kind out of stores: queued to launch, in flight, searching, or parked on a world."""
    def out(e) -> bool:
        d = e.data
        return ((e.kind == "dial_out" and (d.get("op") == drone or (d.get("op") == "search" and d.get("by") == drone)))
                or (e.kind in ("drone_report", "malp_return") and d.get("drone") == drone)
                or (e.kind == "search_report" and d.get("by") == drone))
    return sum(w.drone == drone for w in c.worlds.values()) + len(c.events.find(out))


def fleet(c: Campaign, drone: str) -> int:
    """Every drone of this kind the SGC has: in stores and in the field."""
    return c.stock[drone] + deployed(c, drone)


def stow(c: Campaign, drone: str) -> list[str]:
    """A drone comes home to stores. It was paid for, so it always fits (the cap only limits purchases)."""
    c.stock[drone] += 1
    return []


def _rescue_for(c: Campaign, name: str) -> Mission | None:
    return next((m for m in c.missions if m.type == "rescue" and m.target == name and m.state == "active"), None)


def _explored(c: Campaign) -> list[str]:
    """Said once each time the dialing list runs out of unexplored addresses."""
    if any(w.status == "unexplored" for w in c.worlds.values()):
        c.hints.discard("explored")
        return []
    if "explored" in c.hints:
        return []
    c.hints.add("explored")
    return [EXPLORED]


def hourly(c: Campaign) -> list[str]:
    """One game hour at the SGC: the base recovers, team timers run out; at midnight, requisitions, the
    Goa'uld's attention fading, and the address hint."""
    hour = c.now // HOUR
    if hour % 6 == 0:
        c.meters["security"] = min(100, c.meters["security"] + (2 if "security_detail" in c.upgrades else 1))
    if hour % 4 == 0:
        c.meters["personnel"] = min(100, c.meters["personnel"] + 1)
    msgs: list[str] = []
    if hour % 24 == 0:
        msgs += economy.requisition(c) + factions.decay(c) + _explored(c)
    for name in team_names(c):
        tm = c.teams[name]
        if not tm.until or tm.until > c.now:
            continue
        if tm.status == "captured" and (m := _rescue_for(c, name)) is not None:
            tm.until = max(tm.until, m.end + DAY)          # a rescue is on its way: hold on
            continue
        tm.until = 0
        if tm.status == "captured":
            c.record["teams_lost"] += 1
            economy.note(c, "lost")
            tm.status, tm.idc, tm.where, tm.until = "lost", "revoked", "", c.now + REFORM
            msgs.append(f"{name} PRESUMED LOST")
        elif tm.status == "injured":
            tm.status = "base"
            msgs.append(f"{name} BACK ON DUTY")
        elif tm.status == "lost" and name in CORE_TEAMS:
            tm.status, tm.idc, tm.xp = "base", "valid", 0
            msgs.append(f"{name} RE-FORMED — GREEN")
        elif tm.status == "lost":
            del c.teams[name]
            msgs.append(f"{name} DISBANDED — THE NUMBER CAN BE RECOMMISSIONED")
        elif tm.status == "forming":
            tm.status = "base"
            msgs.append(f"{name} READY FOR DUTY")
        elif tm.status == "training":
            tm.status = "base"
            msgs.append(f"{name} TRAINING COMPLETE: {tm.secondary.upper()}")
    return msgs
