"""Scenario content: TOML files parsed and checked into typed objects.

Every problem is reported as "<file>:<path>: <message>", e.g.
"stolen_idc.toml:node.start.choice[1].outcome: unknown effect ...".
"""
from __future__ import annotations

import math
import re
import string
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from ..events import REGISTRY
from . import rules
from . import world as wd
from .arcs import ARCS, names_faction
from .factions import STAGE_NAMES
from .orders import SITUATIONS
from .state import MISSION_TYPES

KINDS = ("incoming", "probe", "checkin", "debrief", "faction", "arc")
GAME_VISUALS = ("incoming", "dial_out", "iris_hold", "arrival", "team_return", "firefight", "firefight_win",
                "bomb", "asgard_beam")
VISUALS = frozenset(GAME_VISUALS) | frozenset(REGISTRY)
TEAM_PICKS = ("compromised", "captured", "base", "any", "territory")
TRIGGERS = ("random", "team_return")            # when an incoming scenario plays
PLACEHOLDERS = frozenset({"team", "goauld", "world", "designation", "specialty", "captured_at", "faction",
                          "captive", "owner"})
# Placeholders that are never shown: {owner} is a hidden trait, and {goauld} names a random Goa'uld whom the
# SGC may never have heard of ({faction} says "a Goa'uld" until it has).
HIDDEN_IN_TEXT = {"owner": "is a hidden trait: use it only in effects and conditions",
                  "goauld": "names a Goa'uld the SGC may not know: use it only in effects and conditions"}
TEXT_LEVELS = ("full", "partial", "minimal")
_TOP_KEYS = {"id", "kind", "weight", "when", "visual", "goauld", "team", "mission_type", "on", "node", "stage",
             "arc", "arc_stage"}
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
    default: str
    choices: list[Choice]
    situation: str | None = None             # the standing order that answers this node on a timeout

    @property
    def routine(self) -> bool:
        """A node with one choice and no standing order just happens; anything else raises an alarm."""
        return len(self.choices) == 1 and self.situation is None


@dataclass
class Scenario:
    id: str
    kind: str
    weight: float
    when: tuple[rules.Cond, ...]
    visual: tuple[str, ...]
    goauld: bool
    team: str | None
    mission_type: str | None                 # checkin/debrief: one of MISSION_TYPES (None: any mission)
    on: str = "random"                       # incoming: "random", or "team_return" (a team coming home)
    stage: str | None = None                 # faction: the stage it plays at
    arc: str | None = None                   # arc: which arc...
    arc_stage: int | None = None             # ...and at which of its stages
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

    def text(self, value, path: str, display: bool = True) -> str:
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
            if display and name in HIDDEN_IN_TEXT:
                self.fail(path, f"{{{name}}} {HIDDEN_IN_TEXT[name]}")
        return value

    def strings(self, value, path: str, display: bool = True) -> list[str]:
        self.expect(value, list, path, "value")
        for i, v in enumerate(value):
            self.text(v, f"{path}[{i}]", display)
        return value

    def conds(self, value, path: str, allow_hidden: bool = True) -> tuple[rules.Cond, ...]:
        out = []
        for i, v in enumerate(self.strings(value, path, display=False)):
            try:
                cond = rules.parse_cond(v)
            except rules.RuleError as e:
                self.fail(f"{path}[{i}]", str(e))
            if not allow_hidden and _hidden_trait(cond.text):
                self.fail(f"{path}[{i}]", HIDDEN_ONLY_IN_WHEN)
            out.append(cond)
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
            for i, m in enumerate(self.strings(r.get("mods", []), f"{path}.roll.mods", display=False)):
                try:
                    mod = rules.parse_mod(m)
                except rules.RuleError as e:
                    self.fail(f"{path}.roll.mods[{i}]", str(e))
                if _hidden_trait(mod.cond.text):
                    self.fail(f"{path}.roll.mods[{i}]", HIDDEN_ONLY_IN_WHEN)
                mods.append(mod)
            if "win" not in r or "lose" not in r:
                self.fail(f"{path}.roll", "a roll needs both win and lose")
            return Outcome(roll=Roll(odds, tuple(mods), self.outcome(r["win"], f"{path}.roll.win", nodes),
                                     self.outcome(r["lose"], f"{path}.roll.lose", nodes)))
        effects = []
        for i, e in enumerate(self.strings(d.get("effects", []), f"{path}.effects", display=False)):
            try:
                effects.append(rules.parse_effect(e))
            except rules.RuleError as err:
                self.fail(f"{path}.effects[{i}]", str(err))
            if e.split(None, 1)[:1] == ["game_over"]:           # its message is shown
                self.text(e.split(None, 1)[1], f"{path}.effects[{i}]")
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
                                  self.conds(ch.get("requires", []), f"{cp}.requires", allow_hidden=False),
                                  self.outcome(ch.get("outcome"), f"{cp}.outcome", nodes)))
        default = self.expect(d.get("default"), str, f"{path}.default", "default")
        match = next((c for c in choices if c.key == default), None)
        if match is None:
            self.fail(f"{path}.default", f'default "{default}" is not one of the choice keys')
        if match.requires:
            self.fail(f"{path}.default", "the default choice may not have requires")
        situation = d.get("situation")
        if situation is not None:
            if situation not in SITUATIONS:
                self.fail(f"{path}.situation", f'unknown situation "{situation}"')
            sit = SITUATIONS[situation]
            if default != sit.default:
                self.fail(f"{path}.default", f'a {situation} node\'s default must be "{sit.default}"')
            missing = [k for k in sit.keys if k not in {c.key for c in choices}]
            if missing:
                self.fail(f"{path}.choice", f"a {situation} node needs choices keyed {', '.join(missing)}")
        for k in d:
            if k not in ("text", "choice", "default", "situation"):
                self.fail(path, f'unknown key "{k}"')
        return Node(name, dict(text), default, choices, situation)


HIDDEN_ONLY_IN_WHEN = ("hidden conditions (world traits, attention, stage, is, {owner}) may only appear in a "
                       "scenario's when")


def _hidden_trait(text: str) -> bool:
    """A condition on something the SGC may not know (rules.hidden): it picks a scenario, so only `when`.
    Anything about {owner} asks who holds the world, a hidden trait."""
    return rules.hidden(text) or "{owner}" in text


# ------------------------------------------------------------------ continuity
# Nobody is known before first contact. A text, label or game_over message may name a Goa'uld, an ally, a named
# world or an arc's hero only where the SGC is sure to know it: the scenario's kind or `when` guarantees it (an
# arc under way, `aware`, an ally flag), every path to the node filed it, or every way out of the node files it
# (the scenario that reveals a name). `requires` guarantees nothing: a greyed-out label is still shown.

_FACTION_WORDS = {**{fid: name for name, fid in wd.GOAULD_IDS.items()},
                  **{fid: name.removeprefix("the ") for fid, name in wd.ALLY_NAMES.items() if fid != "locals"}}
_KNOWN_AT_START = ("Abydos",)
_WORLD_WORDS = {n for place, spec in (*wd.CANON.items(), *wd.ARC_WORLDS.items()) if place not in _KNOWN_AT_START
                for n in (place, *spec[-1].values())}
NAMES = frozenset({*_FACTION_WORDS.values(), *_WORLD_WORDS, "Thor"})
_NAME_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(n) for n in sorted(NAMES, key=len, reverse=True))
                      + r")(?!\w)", re.IGNORECASE)
_BY_LOWER = {n.lower(): n for n in NAMES}
_REVEAL_NAME = re.compile(r'^reveal name (\S+)(?: "([^"]+)")? from (\S+)$')
# Arcs run only in Campaign (Sandbox has none, so "arc <id> start" does nothing there), and in Campaign the canon
# worlds are on the dialing list ("@Name": a world a name reveal can reach).
_CAMPAIGN = frozenset({"#campaign", *(f"@{n}" for n in (*_KNOWN_AT_START, *wd.CAMPAIGN_CANON))})


def _names_in(text: str) -> set[str]:
    return {_BY_LOWER[m.lower()] for m in _NAME_RE.findall(text)}


def _faction_names(fid: str | None) -> set[str]:
    return {_FACTION_WORDS[fid]} if fid in _FACTION_WORDS else set()


def _arc_names(aid: str) -> set[str]:
    """What an arc under way tells the SGC: the names in its title (logged when it starts), and its faction if
    the title names it (arcs.start files it then)."""
    arc = ARCS[aid]
    return _names_in(arc.title) | (_faction_names(arc.faction) if names_faction(arc) else set())


@cache
def _designated() -> dict[str, str]:
    return {wd.place_id(n): n for n in wd.PLACES}


def _place_of(tok: str, sc: Scenario) -> str | None:
    """The named world a world token stands for, if the checker can tell."""
    if tok.startswith("@"):
        return tok[1:]
    if tok == rules.WORLD_SLOT:
        return ARCS[sc.arc].world if sc.kind == "arc" else None
    return _designated().get(tok)


def _fid_of(tok: str, sc: Scenario) -> str | None:
    if tok == "{faction}" and sc.kind == "arc":
        return ARCS[sc.arc].faction
    return tok if tok in wd.FACTION_IDS else None


def _guaranteed(sc: Scenario) -> frozenset[str]:
    """What the SGC surely knows whenever this scenario plays."""
    known = _arc_names(sc.arc) | _CAMPAIGN if sc.kind == "arc" else set()
    for cond in sc.when:
        t = cond.text.split()
        if t[0] == "aware":
            known |= _faction_names(_fid_of(t[1], sc))
        elif len(t) == 1 and t[0].startswith("ally."):
            known |= _faction_names(t[0].removeprefix("ally."))
        elif t[0] == "arc":                                # any arc condition holds only in Campaign
            known |= _CAMPAIGN | (_arc_names(t[1]) if t[2] != "dormant" else set())
        elif t[0] == "known" and (place := _place_of(t[1], sc)) is not None:
            known.add(f"@{place}")
    return frozenset(known)


def _check_names(p: _Parser, path: str, text: str, known: frozenset[str] | set[str]) -> None:
    for name in sorted(_names_in(text) - set(known)):
        p.fail(path, f'"{name}" is named before the SGC knows it: guarantee it in when (aware, an ally flag, '
                     "an arc under way), or reveal it on every choice here")


def _after(effects, known: frozenset[str], sc: Scenario, p: _Parser | None, path: str) -> frozenset[str]:
    """What the SGC surely knows once these effects have run (a guarded "unless" effect may not run)."""
    out = set(known)
    for i, e in enumerate(effects):
        t = e.text.split()
        if t[0] == "game_over":
            if p is not None:
                _check_names(p, f"{path}.effects[{i}]", e.text.split(None, 1)[1], out)
            continue
        if " unless " in e.text:
            continue
        if t[:2] == ["reveal", "faction"]:
            out |= _faction_names(_fid_of(t[2], sc))
        elif t[0] == "gain" and t[1].startswith("ally."):
            out |= _faction_names(t[1].removeprefix("ally."))
        elif t[0] == "arc" and t[2] == "start" and "#campaign" in out:
            out |= _arc_names(t[1])
        elif t[:2] == ["reveal", "address"] and len(t) == 3:
            out.add(t[2])
        elif (m := _REVEAL_NAME.match(e.text)) is not None:
            place = _place_of(m[1], sc)
            if place is None:                          # a world only the engine can pick: its given name, if any
                out |= _names_in(m[2] or "") if m[1] == rules.WORLD_SLOT else set()
            elif f"@{place}" in out:                   # an unlisted world can't be named yet
                names = (wd.CANON[place] if place in wd.CANON else wd.ARC_WORLDS[place])[-1]
                out |= _names_in(m[2] or names.get(m[3]) or names.get("locals") or "")
    return frozenset(out)


def _leaves(o: Outcome, known: frozenset[str], sc: Scenario, p: _Parser | None,
            path: str) -> list[tuple[Outcome, frozenset[str]]]:
    """Every way an outcome can end (a roll's win and lose), with what the SGC surely knows after it."""
    if o.roll:
        return (_leaves(o.roll.win, known, sc, p, f"{path}.roll.win")
                + _leaves(o.roll.lose, known, sc, p, f"{path}.roll.lose"))
    return [(o, _after(o.effects, known, sc, p, path))]


def _check_continuity(p: _Parser, sc: Scenario) -> None:
    start = _guaranteed(sc)
    at = {"start": start}                  # what the SGC surely knows on entering each node, over every path
    changed = True
    while changed:
        changed = False
        for name, node in sc.nodes.items():
            if name not in at:
                continue
            for ch in node.choices:
                for o, known in _leaves(ch.outcome, at[name], sc, None, ""):
                    if o.goto and (new := known & at.get(o.goto, known)) != at.get(o.goto):
                        at[o.goto], changed = new, True
    for name, node in sc.nodes.items():
        path = f"node.{name}"
        leaves = [known for i, ch in enumerate(node.choices)
                  for _, known in _leaves(ch.outcome, at.get(name, start), sc, p, f"{path}.choice[{i}].outcome")]
        shown = frozenset.intersection(*leaves)       # every way out of the node files these
        for level, text in node.text.items():
            _check_names(p, f"{path}.text.{level}", text, shown)
        for i, ch in enumerate(node.choices):
            _check_names(p, f"{path}.choice[{i}].label", ch.label, shown)


def _terminates(o: Outcome, ends: set[str]) -> bool:
    if o.roll:
        return _terminates(o.roll.win, ends) and _terminates(o.roll.lose, ends)
    return o.end or o.game_over or (o.goto is not None and o.goto in ends)


def _bound_placeholders(kind: str | None, team: str | None, goauld: bool, on: str,
                        mission_type: str | None) -> frozenset[str]:
    """Which {placeholders} a scenario may use, given its kind and its own top-level fields."""
    bound: set[str] = set()
    world = {"world", "designation", "owner"}
    if kind in ("probe", "checkin", "debrief") or on == "team_return":
        bound |= world
    if kind in ("checkin", "debrief") or on == "team_return":
        bound |= {"team", "specialty"}
    if mission_type == "rescue":
        bound.add("captive")
    if team is not None:
        bound |= {"team", "captured_at"}
    if team == "territory":
        bound |= world | {"specialty"}
    if goauld:
        bound.add("goauld")
    if kind in ("faction", "arc"):
        bound.add("faction")
    if kind == "arc":
        bound |= world
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
    if goauld is not None and goauld != "any":
        p.fail("goauld", 'goauld must be "any"')
    team = data.get("team")
    if team is not None and (team not in TEAM_PICKS or kind not in ("incoming", "faction")
                             or (team == "territory" and kind != "faction")):
        p.fail("team", f"team must be one of {', '.join(TEAM_PICKS)}, only on incoming or faction scenarios "
                       "(territory: faction only)")
    on = data.get("on", "random")
    if on not in TRIGGERS or (on != "random" and kind != "incoming"):
        p.fail("on", f"on must be one of {', '.join(TRIGGERS)}, and only on incoming scenarios")
    if on == "team_return" and team is not None:
        p.fail("team", "a team_return scenario is bound to the team coming home; leave team out")
    mission_type = data.get("mission_type")
    if mission_type is not None and (mission_type not in MISSION_TYPES or kind not in ("checkin", "debrief")):
        p.fail("mission_type", f"mission_type must be one of {', '.join(MISSION_TYPES)}, on checkin or debrief")
    stage = data.get("stage")
    if (kind == "faction") != (stage is not None) or (stage is not None and stage not in STAGE_NAMES[1:]):
        p.fail("stage", f"a faction scenario needs stage = one of {', '.join(STAGE_NAMES[1:])}; other kinds "
                        "take no stage")
    arc, arc_stage = data.get("arc"), data.get("arc_stage")
    if kind == "arc":
        if not isinstance(arc, str) or arc not in ARCS:
            p.fail("arc", f"arc must be one of {', '.join(ARCS)}")
        if not isinstance(arc_stage, int) or isinstance(arc_stage, bool) \
                or not 1 <= arc_stage <= len(ARCS[arc].stages):
            p.fail("arc_stage", f"arc_stage must be 1-{len(ARCS[arc].stages)} for arc {arc}")
    elif arc is not None or arc_stage is not None:
        p.fail("arc", "only arc scenarios take arc and arc_stage")
    p.bound = _bound_placeholders(kind, team, goauld is not None, on, mission_type)
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
    sc = Scenario(
        id=sid, kind=kind, weight=float(weight), when=p.conds(data.get("when", []), "when"),
        visual=p.visuals(data["visual"], "visual") if "visual" in data else (),
        goauld=goauld is not None, team=team,
        mission_type=mission_type, on=on, nodes=nodes, source=source, stage=stage, arc=arc, arc_stage=arc_stage)
    _check_continuity(p, sc)
    return sc


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
