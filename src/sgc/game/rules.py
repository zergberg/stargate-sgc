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

from . import world as wd
from .clock import DAY, HOUR
from .state import (METERS, MISSION_TYPES, RANK_NAMES, SPECIALTIES, STATUSES, STOCK, TEAMS, Campaign, Mission, Team,
                    available_teams, has_specialty, rank, rank_index)

FLAGS = frozenset({"ally.tokra", "ally.asgard", "ally.tollan", "ally.nox", "ally.jaffa",
                   "tech.zat", "tech.naquadah_generator", "tech.lrs"})
SINGLE_USE = frozenset({"ally.asgard", "ally.nox"})
FLAG_NAMES = {"ally.tokra": "THE TOK'RA", "ally.asgard": "THE ASGARD", "ally.tollan": "THE TOLLAN",
              "ally.nox": "THE NOX", "ally.jaffa": "THE FREE JAFFA", "tech.zat": "ZAT'NIK'TEL",
              "tech.naquadah_generator": "NAQUADAH GENERATOR", "tech.lrs": "LONG-RANGE SENSORS"}
TEAM_SLOTS = ("{team}",)
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
    if tok in TEAMS or tok in TEAM_SLOTS:
        return tok
    raise RuleError(f'"{text}": unknown team "{tok}"')


def _resolve(tok: str, bind: dict) -> str:
    return bind.get(tok[1:-1], "?") if tok.startswith("{") else tok


def _world_tok(tok: str, text: str) -> str:
    if tok == WORLD_SLOT or _DESIGNATION.match(tok):
        return tok
    raise RuleError(f'"{text}": expected {{world}} or a designation like P3X-866, got "{tok}"')


def _world(c: Campaign, tok: str, bind: dict) -> wd.World | None:
    return c.worlds.get(bind.get("world_id", "") if tok == WORLD_SLOT else tok)


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
    if len(t) == 3 and t[1] in _OPS and (t[0] in METERS or t[0] == "day"):
        name, op, n = t[0], _OPS[t[1]], _int(t[2], text)
        if name == "day":
            return lambda c, b: op(int(c.minutes // DAY) + 1, n)
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


def _meter(name: str, delta: int):
    def fn(c: Campaign, b: dict) -> list[str]:
        before = c.meters[name]
        c.meters[name] = max(0, min(100, before + scaled(c, delta)))
        d = c.meters[name] - before
        if name == "personnel" and d < 0:
            c.record["personnel_lost"] += -d
        return [f"{name.upper()} {d:+d}"] if d else []
    return fn


def _breach(n: int):
    security = _meter("security", -n)

    def fn(c: Campaign, b: dict) -> list[str]:
        if c.meters["security"] <= 0:
            c.over = "The SGC was overrun."
            return ["SECURITY BREACHED — THE BASE HAS FALLEN"]
        return ["SECURITY BREACH", *security(c, b)]
    return fn


def _team(tok: str, status: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        name = _resolve(tok, b)
        tm = c.teams[name]
        prev = tm.status
        if status == "lost" and prev != "lost" and has_flag(c, "ally.nox"):
            c.used.add("ally.nox")
            tm.status, tm.until = "injured", c.now + INJURED
            return [f"THE NOX RETURNED {name} ALIVE"]
        tm.status = status
        if status == "captured":
            tm.idc, tm.until = "compromised", c.now + CAPTIVE
            tm.where = b.get("world_id", tm.where)
            return [f"{name} CAPTURED ON {b.get('world', 'AN UNKNOWN WORLD').upper()}"]
        if status == "lost":
            if prev != "lost":
                c.record["teams_lost"] += 1
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
    w = wd.generate(rng, set(c.worlds), found=found, owner_odds=1.0 if c.mode == "campaign" else 0.3)
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
    c.events.push(c.now, "team_return", {"mission": m.id})
    return [line for ev in searches for line in withdraw_search(c, ev.data)]


def _reveal_address(c: Campaign, b: dict) -> list[str]:
    w = new_address(c, f"intel from {b['team']}" if b.get("team") else "intel")
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


def _drone(tok: str, fate: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        w = _world(c, tok, b)
        if w is None or w.drone is None:
            return []
        drone, w.drone = w.drone, None
        msgs = [f"{drone.upper()} ON {w.name.upper()} {fate.upper()}"]
        if fate == "captured":
            msgs += set_world_status(c, w, "hostile")
        return msgs
    return fn


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


def _effect_body(body: str, text: str):
    t = body.split()
    if not t:
        raise RuleError("empty effect")
    if t[0] == "reveal":
        if t == ["reveal", "address"]:
            return _reveal_address
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
        if t[2] not in STATUSES:
            raise RuleError(f'"{text}": unknown team status "{t[2]}"')
        return _team(team, t[2])
    if t[0] == "idc" and len(t) == 3 and t[2] in ("revoke", "compromise"):
        return _idc(_team_tok(t[1], text), t[2])
    if t[0] == "gain" and len(t) == 2 and t[1] in FLAGS:
        return _gain(t[1])
    if t[0] == "use" and len(t) == 2 and t[1] in SINGLE_USE:
        return _use(t[1])
    if t[0] == "game_over" and len(t) >= 2:
        return _game_over(body.split(None, 1)[1])
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

def teams_matching(c: Campaign, which: str) -> list[str]:
    if which == "base":
        return available_teams(c)
    if which == "compromised":
        return [t for t in TEAMS if c.teams[t].idc == "compromised" and c.teams[t].status != "lost"]
    if which == "captured":
        return [t for t in TEAMS if c.teams[t].status == "captured"]
    return [t for t in TEAMS if c.teams[t].status != "lost"]


def deployed(c: Campaign, drone: str) -> int:
    """Drones of this kind out of stores: queued to launch, in flight, searching, or parked on a world."""
    def out(e) -> bool:
        d = e.data
        return ((e.kind == "dial_out" and (d.get("op") == drone or (d.get("op") == "search" and d.get("by") == drone)))
                or (e.kind == "malp_return" and d.get("drone") == drone)
                or (e.kind == "search_report" and d.get("by") == drone))
    return sum(w.drone == drone for w in c.worlds.values()) + len(c.events.find(out))


def fleet(c: Campaign, drone: str) -> int:
    """Every drone of this kind the SGC has: in stores and in the field. Deliveries stop at the cap."""
    return c.stock[drone] + deployed(c, drone)


def stow(c: Campaign, drone: str) -> list[str]:
    """A drone comes home to stores; one that would take them over the cap is scrapped."""
    cap = STOCK[drone][1]
    if c.stock[drone] >= cap:
        return [f"{drone.upper()} SCRAPPED — STORES FULL ({cap})"]
    c.stock[drone] += 1
    return []


def hourly(c: Campaign) -> list[str]:
    """One game hour at the SGC: the base recovers, drones are restocked, team timers run out."""
    hour = c.now // HOUR
    if hour % 6 == 0:
        c.meters["security"] = min(100, c.meters["security"] + 1)
    if hour % 4 == 0:
        c.meters["personnel"] = min(100, c.meters["personnel"] + 1)
    msgs = []
    if hour % 24 == 0:
        for drone, (_, cap) in STOCK.items():
            if drone == "uav" and (hour // 24) % 3:
                continue
            if fleet(c, drone) < cap:
                c.stock[drone] += 1
                msgs.append(f"{drone.upper()} DELIVERED — {c.stock[drone]} IN STOCK")
    for name, tm in c.teams.items():
        if not tm.until or tm.until > c.now:
            continue
        tm.until = 0
        if tm.status == "captured":
            c.record["teams_lost"] += 1
            tm.status, tm.idc, tm.where, tm.until = "lost", "revoked", "", c.now + REFORM
            msgs.append(f"{name} PRESUMED LOST")
        elif tm.status == "injured":
            tm.status = "base"
            msgs.append(f"{name} BACK ON DUTY")
        elif tm.status == "lost":
            tm.status, tm.idc, tm.xp = "base", "valid", 0
            msgs.append(f"{name} RE-FORMED — GREEN")
    return msgs
