"""Scenario content: TOML files parsed and checked into typed objects.

Every problem is reported as "<file>:<path>: <message>", e.g.
"stolen_idc.toml:node.start.choice[1].outcome: unknown effect ...".
"""
from __future__ import annotations

import math
import string
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..events import REGISTRY
from . import rules

KINDS = ("incoming", "mission")
GAME_VISUALS = ("incoming", "dial_out", "iris_hold", "arrival", "team_return", "firefight", "firefight_win",
                "bomb", "asgard_beam")
VISUALS = frozenset(GAME_VISUALS) | frozenset(REGISTRY)
LORD_PICKS = ("aggressor", "any", "weakest")
TEAM_PICKS = ("compromised", "captured", "base", "any")
RISKS = ("low", "medium", "high")
MISSION_TYPES = ("recon", "rescue", "ally", "tech", "sabotage", "strike", "science", "diplomacy")
PLACEHOLDERS = frozenset({"team", "captive", "goauld", "target", "destination", "captured_at"})
TEXT_LEVELS = ("full", "partial", "minimal")
_TOP_KEYS = {"id", "kind", "weight", "when", "visual", "goauld", "team", "captive", "hostile", "mission_type",
             "brief", "risk", "node"}
_OUTCOME_KEYS = {"goto", "visual", "effects", "end", "roll"}
BUNDLED = Path(__file__).resolve().parent.parent / "data" / "scenarios"
USER_DIR = Path.home() / ".config" / "stargate-sgc" / "scenarios"


class ContentError(ValueError):
    pass


@dataclass
class Outcome:
    goto: str | None = None
    visual: tuple[str, ...] = ()
    effects: tuple[rules.Effect, ...] = ()
    end: bool = False
    roll: "Roll | None" = None

    @property
    def game_over(self) -> bool:
        return any(e.text.startswith("game_over") for e in self.effects)


@dataclass
class Roll:
    odds: int
    mods: tuple[rules.Mod, ...]
    win: Outcome
    lose: Outcome


@dataclass
class Choice:
    key: str
    label: str
    requires: tuple[rules.Cond, ...]
    outcome: Outcome


@dataclass
class Node:
    name: str
    text: dict[str, str]
    countdown: float | None
    default: str
    choices: list[Choice]


@dataclass
class Scenario:
    id: str
    kind: str
    weight: float
    when: tuple[rules.Cond, ...]
    visual: tuple[str, ...]
    goauld: str | None
    team: str | None
    captive: bool
    hostile: bool
    mission_type: str | None
    brief: str | None
    risk: str | None
    nodes: dict[str, Node] = field(default_factory=dict)
    source: str = ""


class _Parser:
    def __init__(self, source: str):
        self.source = source
        self.bound: frozenset[str] = frozenset()

    def fail(self, path: str, msg: str):
        raise ContentError(f"{self.source}:{path}: {msg}")

    def expect(self, value, kind, path: str, what: str):
        if not isinstance(value, kind) or isinstance(value, bool) and kind is not bool:
            if isinstance(kind, tuple):
                name = "a number" if set(kind) == {int, float} else \
                    " or ".join(getattr(k, "__name__", str(k)) for k in kind)
            else:
                name = getattr(kind, "__name__", kind)
            self.fail(path, f"{what} must be {name}")
        return value

    def text(self, value, path: str) -> str:
        self.expect(value, str, path, "text")
        try:
            parsed = list(string.Formatter().parse(value))
        except ValueError as e:
            self.fail(path, f"bad braces in text ({e})")
        for _, name, spec, conv in parsed:
            if name is None:
                continue
            if spec or conv:
                self.fail(path, "format specs and conversions aren't allowed")
            if name not in PLACEHOLDERS:
                self.fail(path, f'unknown placeholder "{{{name}}}"')
            if name not in self.bound:
                self.fail(path, f"'{{{name}}}' is used but the scenario doesn't bind it (set {name} = ...)")
        return value

    def strings(self, value, path: str) -> list[str]:
        self.expect(value, list, path, "value")
        for i, v in enumerate(value):
            self.text(v, f"{path}[{i}]")
        return value

    def conds(self, value, path: str) -> tuple[rules.Cond, ...]:
        out = []
        for i, v in enumerate(self.strings(value, path)):
            try:
                out.append(rules.parse_cond(v))
            except rules.RuleError as e:
                self.fail(f"{path}[{i}]", str(e))
        return tuple(out)

    def visuals(self, value, path: str) -> tuple[str, ...]:
        names = [value] if isinstance(value, str) else self.strings(value, path)
        for v in names:
            if v not in VISUALS:
                self.fail(path, f'unknown visual "{v}"')
        return tuple(names)

    def outcome(self, d, path: str, nodes: set[str]) -> Outcome:
        self.expect(d, dict, path, "outcome")
        for k in d:
            if k not in _OUTCOME_KEYS:
                self.fail(path, f'unknown key "{k}"')
        if "roll" in d:
            if set(d) != {"roll"}:
                self.fail(path, "an outcome with a roll can't have other keys; put them in win/lose")
            r = self.expect(d["roll"], dict, f"{path}.roll", "roll")
            for k in r:
                if k not in ("odds", "mods", "win", "lose"):
                    self.fail(f"{path}.roll", f'unknown key "{k}"')
            odds = self.expect(r.get("odds"), int, f"{path}.roll.odds", "odds")
            if not 1 <= odds <= 99:
                self.fail(f"{path}.roll.odds", "odds must be 1-99")
            mods = []
            for i, m in enumerate(self.strings(r.get("mods", []), f"{path}.roll.mods")):
                try:
                    mods.append(rules.parse_mod(m))
                except rules.RuleError as e:
                    self.fail(f"{path}.roll.mods[{i}]", str(e))
            if "win" not in r or "lose" not in r:
                self.fail(f"{path}.roll", "a roll needs both win and lose")
            return Outcome(roll=Roll(odds, tuple(mods), self.outcome(r["win"], f"{path}.roll.win", nodes),
                                     self.outcome(r["lose"], f"{path}.roll.lose", nodes)))
        effects = []
        for i, e in enumerate(self.strings(d.get("effects", []), f"{path}.effects")):
            try:
                effects.append(rules.parse_effect(e))
            except rules.RuleError as err:
                self.fail(f"{path}.effects[{i}]", str(err))
        goto = d.get("goto")
        if goto is not None:
            self.expect(goto, str, f"{path}.goto", "goto")
            if goto not in nodes:
                self.fail(f"{path}.goto", f'unknown node "{goto}"')
        end = self.expect(d.get("end", False), bool, f"{path}.end", "end")
        out = Outcome(goto, self.visuals(d["visual"], f"{path}.visual") if "visual" in d else (),
                      tuple(effects), end)
        if goto and end:
            self.fail(path, "an outcome can't both goto and end")
        if not (goto or end or out.game_over):
            self.fail(path, "an outcome needs goto, end, roll, or a game_over effect")
        return out

    def node(self, name: str, d, nodes: set[str]) -> Node:
        path = f"node.{name}"
        self.expect(d, dict, path, "node")
        text = self.expect(d.get("text"), dict, f"{path}.text", "text table")
        if "full" not in text:
            self.fail(f"{path}.text", "text.full is required")
        for level, value in text.items():
            if level not in TEXT_LEVELS:
                self.fail(f"{path}.text", f'unknown text level "{level}"')
            self.text(value, f"{path}.text.{level}")
        countdown = d.get("countdown")
        if countdown is not None:
            self.expect(countdown, (int, float), f"{path}.countdown", "countdown")
            if not 5 <= countdown <= 60:
                self.fail(f"{path}.countdown", "countdown must be 5-60 seconds")
        raw = self.expect(d.get("choice"), list, f"{path}.choice", "choice list")
        if not 1 <= len(raw) <= 4:
            self.fail(f"{path}.choice", "a node needs 1-4 choices")
        choices = []
        for i, ch in enumerate(raw):
            cp = f"{path}.choice[{i}]"
            self.expect(ch, dict, cp, "choice")
            for k in ch:
                if k not in ("key", "label", "requires", "outcome"):
                    self.fail(cp, f'unknown key "{k}"')
            key = self.expect(ch.get("key"), str, f"{cp}.key", "key")
            if key in (c.key for c in choices):
                self.fail(f"{cp}.key", f'duplicate choice key "{key}"')
            choices.append(Choice(key, self.text(ch.get("label"), f"{cp}.label"),
                                  self.conds(ch.get("requires", []), f"{cp}.requires"),
                                  self.outcome(ch.get("outcome"), f"{cp}.outcome", nodes)))
        default = self.expect(d.get("default"), str, f"{path}.default", "default")
        match = next((c for c in choices if c.key == default), None)
        if match is None:
            self.fail(f"{path}.default", f'default "{default}" is not one of the choice keys')
        if match.requires:
            self.fail(f"{path}.default", "the default choice may not have requires")
        for k in d:
            if k not in ("text", "countdown", "choice", "default"):
                self.fail(path, f'unknown key "{k}"')
        return Node(name, dict(text), float(countdown) if countdown is not None else None, default, choices)


def _terminates(o: Outcome, ends: set[str]) -> bool:
    if o.roll:
        return _terminates(o.roll.win, ends) and _terminates(o.roll.lose, ends)
    return o.end or o.game_over or (o.goto is not None and o.goto in ends)


def _bound_placeholders(kind: str | None, team: str | None, goauld: str | None, captive: bool) -> frozenset[str]:
    """Which {placeholders} a scenario may use, given its own top-level fields."""
    bound = {"destination"}
    if kind == "mission" or team is not None:
        bound |= {"team", "captured_at"}
    if captive:
        bound |= {"captive", "captured_at"}
    if goauld is not None:
        bound |= {"goauld", "target"}
    return frozenset(bound)


def parse_scenario(data: dict, source: str) -> Scenario:
    p = _Parser(source)
    for k in data:
        if k not in _TOP_KEYS:
            p.fail(k, f'unknown key "{k}"')
    sid = p.expect(data.get("id"), str, "id", "id")
    kind = data.get("kind")
    if kind not in KINDS:
        p.fail("kind", f"kind must be one of {', '.join(KINDS)}")
    weight = p.expect(data.get("weight", 1), (int, float), "weight", "weight")
    if not (weight > 0 and math.isfinite(weight)):
        p.fail("weight", "weight must be positive")
    goauld = data.get("goauld")
    if goauld is not None and goauld not in LORD_PICKS:
        p.fail("goauld", f"goauld must be one of {', '.join(LORD_PICKS)}")
    team = data.get("team")
    if team is not None and team not in TEAM_PICKS:
        p.fail("team", f"team must be one of {', '.join(TEAM_PICKS)}")
    p.bound = _bound_placeholders(kind, team, goauld, bool(data.get("captive", False)))
    mission_type = brief = risk = None
    if kind == "mission":
        mission_type = data.get("mission_type")
        if mission_type not in MISSION_TYPES:
            p.fail("mission_type", f"mission_type must be one of {', '.join(MISSION_TYPES)}")
        if "brief" not in data:
            p.fail("brief", "missions need a brief")
        brief = p.text(data["brief"], "brief")
        risk = data.get("risk")
        if risk not in RISKS:
            p.fail("risk", f"risk must be one of {', '.join(RISKS)}")
    raw_nodes = p.expect(data.get("node"), dict, "node", "node table")
    if "start" not in raw_nodes:
        p.fail("node", "a scenario needs a node named start")
    names = set(raw_nodes)
    nodes = {name: p.node(name, d, names) for name, d in raw_nodes.items()}
    ends: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, node in nodes.items():
            if name in ends:
                continue
            default_choice = next(c for c in node.choices if c.key == node.default)
            if _terminates(default_choice.outcome, ends):
                ends.add(name)
                changed = True
    for name in nodes:
        if name not in ends:
            p.fail(f"node.{name}", "this node can never reach an end")
    return Scenario(
        id=sid, kind=kind, weight=float(weight), when=p.conds(data.get("when", []), "when"),
        visual=p.visuals(data["visual"], "visual") if "visual" in data else (),
        goauld=goauld, team=team, captive=p.expect(data.get("captive", False), bool, "captive", "captive"),
        hostile=p.expect(data.get("hostile", False), bool, "hostile", "hostile"),
        mission_type=mission_type, brief=brief, risk=risk, nodes=nodes, source=source)


def load_file(path: Path) -> Scenario:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise ContentError(f"{path.name}: {e}") from None
    return parse_scenario(data, path.name)


def load(bundled: Path = BUNDLED, user: Path | None = USER_DIR) -> tuple[dict[str, Scenario], list[str]]:
    """Bundled scenarios (errors raise ContentError) plus the user's (errors skip the file with a warning)."""
    scenarios: dict[str, Scenario] = {}
    for path in sorted(bundled.glob("*.toml")):
        sc = load_file(path)
        if sc.id in scenarios:
            raise ContentError(f"{path.name}:id: duplicate id {sc.id!r}")
        scenarios[sc.id] = sc
    warnings: list[str] = []
    if user is not None and user.is_dir():
        for path in sorted(user.glob("*.toml")):
            try:
                sc = load_file(path)
            except ContentError as e:
                warnings.append(f"scenario skipped: {e}")
                continue
            scenarios[sc.id] = sc
    return scenarios, warnings
