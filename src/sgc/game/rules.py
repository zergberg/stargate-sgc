"""Campaign rules: conditions, effects and odds, parsed once from content strings.

Conditions and effects are short strings in scenario files. They are parsed at load
time into callables taking (campaign, bindings); bindings fill {team}, {captive},
{goauld} and {target} with names chosen when the scenario is drawn.
"""
from __future__ import annotations

import math
import operator
from dataclasses import dataclass, field
from typing import Callable

from .state import METERS, POOL, STATUSES, TEAMS, Campaign, replace_lord

FLAGS = frozenset({"ally.tokra", "ally.asgard", "ally.tollan", "ally.nox", "ally.jaffa",
                   "tech.zat", "tech.naquadah_generator", "tech.lrs"})
SINGLE_USE = frozenset({"ally.asgard", "ally.nox"})
FLAG_NAMES = {"ally.tokra": "THE TOK'RA", "ally.asgard": "THE ASGARD", "ally.tollan": "THE TOLLAN",
              "ally.nox": "THE NOX", "ally.jaffa": "THE FREE JAFFA", "tech.zat": "ZAT'NIK'TEL",
              "tech.naquadah_generator": "NAQUADAH GENERATOR", "tech.lrs": "LONG-RANGE SENSORS"}
TEAM_SLOTS = ("{team}", "{captive}")
LORD_SLOTS = ("{goauld}", "{target}")
LOSS = {"recruit": 0.5, "officer": 1.0, "commander": 1.5}
GAIN = {"recruit": 1.25, "officer": 1.0, "commander": 0.75}
GOAL = 3                                   # System Lords to defeat in a campaign
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


def _lord_tok(tok: str, text: str) -> str:
    if tok in POOL or tok in LORD_SLOTS:
        return tok
    raise RuleError(f'"{text}": unknown Goa\'uld "{tok}"')


def _resolve(tok: str, bind: dict) -> str:
    return bind.get(tok[1:-1], "?") if tok.startswith("{") else tok


# ------------------------------------------------------------------ conditions

def parse_cond(text: str) -> Cond:
    t = text.split()
    if not t:
        raise RuleError("empty condition")
    if len(t) == 1:
        if t[0] in FLAGS:
            flag = t[0]
            return Cond(text, lambda c, b: has_flag(c, flag))
        if t[0] == "any_compromised_idc":
            return Cond(text, lambda c, b: any(tm.idc == "compromised" and tm.status != "lost"
                                               for tm in c.teams.values()))
        if t[0] == "any_captured":
            return Cond(text, lambda c, b: any(tm.status == "captured" for tm in c.teams.values()))
    if len(t) == 2 and t[0] == "not" and t[1] in FLAGS:
        flag = t[1]
        return Cond(text, lambda c, b: not has_flag(c, flag))
    if len(t) == 3 and t[1] in _OPS and (t[0] in METERS or t[0] == "cycles"):
        name, op, n = t[0], _OPS[t[1]], _int(t[2], text)
        if name == "cycles":
            return Cond(text, lambda c, b: op(c.cycles, n))
        return Cond(text, lambda c, b: op(c.meters[name], n))
    if len(t) == 3 and t[0] == "team":
        team = _team_tok(t[1], text)
        if t[2] not in STATUSES:
            raise RuleError(f'"{text}": unknown team status "{t[2]}"')
        status = t[2]
        return Cond(text, lambda c, b: c.teams[_resolve(team, b)].status == status)
    if len(t) == 5 and t[0] == "goauld" and t[2] in ("strength", "aggression") and t[3] in _OPS:
        lord, attr, op, n = _lord_tok(t[1], text), t[2], _OPS[t[3]], _int(t[4], text)

        def test(c: Campaign, b: dict) -> bool:
            l = c.lord(_resolve(lord, b))
            return l is not None and op(getattr(l, attr), n)
        return Cond(text, test)
    raise RuleError(f'unknown condition "{text}"')


def parse_mod(text: str) -> Mod:
    """'tech.zat +15' or 'personnel < 40 -15': a condition, then a signed odds change."""
    parts = text.rsplit(None, 1)
    if len(parts) != 2:
        raise RuleError(f'"{text}": expected "<condition> +N"')
    return Mod(parse_cond(parts[0]), _signed(parts[1], text))


def check_all(conds, c: Campaign, bind: dict) -> bool:
    return all(cond(c, bind) for cond in conds)


def odds(base: int, mods, c: Campaign, bind: dict) -> int:
    return max(5, min(95, base + sum(m.delta for m in mods if m.cond(c, bind))))


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
    unscaled = name == "intel" and delta < 0        # intel spent is a cost, not a scaled loss

    def fn(c: Campaign, b: dict) -> list[str]:
        before = c.meters[name]
        change = delta if unscaled else scaled(c, delta)
        c.meters[name] = max(0, min(100, before + change))
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
            tm.status, tm.out_cycles = "injured", 3
            return [f"THE NOX RETURNED {name} ALIVE"]
        tm.status = status
        if status == "captured":
            tm.idc, tm.captured_at, tm.out_cycles = "compromised", b.get("destination") or "an unknown world", 6
            return [f"{name} CAPTURED ON {tm.captured_at.upper()}"]
        if status == "lost":
            if prev != "lost":
                c.record["teams_lost"] += 1
            tm.idc, tm.out_cycles = "revoked", 4
            return [f"{name} LOST"]
        if status == "injured":
            tm.out_cycles = 3
            return [f"{name} TO THE INFIRMARY"]
        if status == "base" and prev == "captured":
            tm.idc, tm.captured_at, tm.out_cycles = "valid", "", 2
            return [f"{name} RESCUED — NEW IDC ISSUED"]
        return []
    return fn


def revoke(c: Campaign, name: str) -> list[str]:
    """Revoke a team's IDC and issue a new one; the team stands down for two cycles."""
    tm = c.teams[name]
    tm.idc, tm.out_cycles = "valid", max(tm.out_cycles, 2)
    return [f"{name} IDC REVOKED — NEW CODE ISSUED"]


def _idc(tok: str, action: str):
    def fn(c: Campaign, b: dict) -> list[str]:
        name = _resolve(tok, b)
        if action == "compromise":
            c.teams[name].idc = "compromised"
            return []
        return revoke(c, name)
    return fn


def _lord(tok: str, attr: str, delta: int):
    def fn(c: Campaign, b: dict) -> list[str]:
        l = c.lord(_resolve(tok, b))
        if l is None or l.defeated:
            return []
        if attr == "aggression":
            l.aggression = max(0, min(100, l.aggression + delta))
            return []
        l.strength = max(0, l.strength + delta)
        if l.strength > 0:
            return [f"{l.name.upper()} WEAKENED — STRENGTH {l.strength}"] if delta < 0 else []
        l.defeated = True
        c.record["goauld_defeated"] += 1
        out = [f"{l.name.upper()} HAS FALLEN"]
        if c.mode == "campaign":
            c.won = c.won or c.record["goauld_defeated"] >= GOAL
        else:
            new = replace_lord(c, l.name)
            if new:
                out.append(f"A NEW SYSTEM LORD RISES: {new.name.upper()}")
        return out
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


def _effect_body(body: str, text: str):
    t = body.split()
    if not t:
        raise RuleError("empty effect")
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
    if t[0] == "goauld" and len(t) == 4 and t[2] in ("strength", "aggression"):
        return _lord(_lord_tok(t[1], text), t[2], _signed(t[3], text))
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

def max_aggression(c: Campaign) -> int:
    return max((l.aggression for l in c.active_lords()), default=0)


def available_teams(c: Campaign) -> list[str]:
    return [t for t in TEAMS if c.teams[t].status == "base" and c.teams[t].out_cycles == 0]


def teams_matching(c: Campaign, which: str) -> list[str]:
    if which == "base":
        return available_teams(c)
    if which == "compromised":
        return [t for t in TEAMS if c.teams[t].idc == "compromised" and c.teams[t].status != "lost"]
    if which == "captured":
        return [t for t in TEAMS if c.teams[t].status == "captured"]
    return [t for t in TEAMS if c.teams[t].status != "lost"]


def start_cycle(c: Campaign) -> list[str]:
    """Count a cycle; injured teams heal, lost teams re-form, captives time out, stood-down teams return."""
    c.cycles += 1
    c.since_briefing += 1
    msgs = []
    for name, tm in c.teams.items():
        if tm.out_cycles <= 0:
            continue
        tm.out_cycles -= 1
        if tm.out_cycles > 0:
            continue
        if tm.status == "captured":
            c.record["teams_lost"] += 1
            tm.status, tm.idc, tm.out_cycles = "lost", "revoked", 4
            msgs.append(f"{name} PRESUMED LOST")
        elif tm.status in ("injured", "lost"):
            msgs.append(f"{name} BACK ON DUTY" if tm.status == "injured" else f"{name} RE-FORMED")
            if tm.status == "lost":
                tm.idc = "valid"
            tm.status = "base"
    return msgs


def quiet(c: Campaign) -> None:
    """A quiet cycle: the base recovers a little and the System Lords look elsewhere."""
    c.meters["personnel"] = min(100, c.meters["personnel"] + 2)
    c.meters["security"] = min(100, c.meters["security"] + 3)
    for l in c.active_lords():
        l.aggression = max(0, l.aggression - 3)


RANKS = ((85, "OUTSTANDING", "Outstanding work, Colonel."),
         (65, "COMMENDED", "Well done. The President sends his thanks."),
         (45, "SATISFACTORY", "We got it done. It cost us."),
         (float("-inf"), "UNDER REVIEW", "Report to the Pentagon."))


def rating(c: Campaign) -> tuple[str, str]:
    """Hammond's verdict: (rank, line)."""
    allies = sum(1 for f in c.inventory if f.startswith("ally."))
    score = (100 - 0.4 * max(0, c.cycles - 70) - 0.3 * c.record["personnel_lost"] - 8 * c.record["teams_lost"]
             + 4 * allies)
    return next((rank, line) for floor, rank, line in RANKS if score >= floor)
