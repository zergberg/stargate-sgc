"""The game engine: campaign cycles, decisions, briefings and missions, played through the gate director.

The engine never draws. It queues Steps on the director (with director.auto off) and puts
a Prompt on the scene when it needs an order; the player's keys come back through key().
A decision opens from the last queued step, so the director is idle while it waits.

Saves are only meaningful at cycle boundaries: a loaded game resumes at the next cycle, any
scenario that was in progress is dropped, and a team that was offworld is recalled to base.
Campaign randomness comes only from self.rng, drawn at build or decision time, never per frame,
so a campaign plays out the same at any frame rate.
"""
from __future__ import annotations

import math
import random
from typing import Callable

from .. import sequences as sq
from ..addresses import Address, load_canon, random_address
from ..director import Director
from ..events import REGISTRY, EventContext
from ..events.common import start_outgoing
from ..model import Figure, Prompt, Step
from . import rules
from .content import Node, Outcome, Scenario
from .state import TEAMS, Campaign

INFO_LEVELS = ("minimal", "partial", "full")
BASE_INFO = {"recruit": "full", "officer": "partial", "commander": "minimal"}
QUIET_EVENTS = ("science", "malp", "friendly")
BRIEFING_EVERY = 3
LANES = (-0.45, -0.15, 0.15, 0.45)
TEAM_LABEL = {"base": "AT BASE", "offworld": "OFFWORLD", "injured": "INFIRMARY", "captured": "MISSING",
              "lost": "LOST"}
Offer = tuple[Scenario, dict, Address]


class Engine:
    def __init__(self, director: Director, campaign: Campaign, scenarios: dict[str, Scenario],
                 transition: float = 10.0, countdown: float = 12.0,
                 save: Callable[[Campaign], None] | None = None,
                 on_end: Callable[[Campaign], None] | None = None,
                 log: Callable[[str], None] | None = None):
        self.d, self.campaign, self.scenarios = director, campaign, scenarios
        self.transition, self.countdown = transition, countdown
        self._save = save or (lambda c: None)
        self._on_end = on_end or (lambda c: None)
        self._log = log or (lambda line: None)
        self.rng = random.Random(campaign.seed)
        if campaign.rng_state is not None:
            self.rng.setstate(campaign.rng_state)
        self._canon = [a for a in load_canon() if a.name != "Earth"]
        self.mode = "busy"          # busy | idle | decision | briefing | revoke | over | debrief
        self.finished = False       # the player has left the game; the app returns to the menu
        self.scenario: Scenario | None = None
        self.node: Node | None = None
        self.bind: dict[str, str] = {}
        self._dest: Address | None = None
        self._actions: list[Callable[[], None]] = []
        self._forced: str | None = None
        self._revoke_pending = False
        self._ended = False
        director.auto = False
        for name, tm in campaign.teams.items():      # a save made mid-mission resumes between cycles
            if tm.status == "offworld":
                tm.status = "base"
                self._log(f"{name} RECALLED TO BASE")

    # ------------------------------------------------------------------ driving
    def begin(self) -> None:
        self.mode = "idle"
        if self.campaign.won:
            self._debrief()

    def queue_scenario(self, scenario_id: str) -> None:
        """Make the next cycle play this scenario (tests, and a future --event for the game)."""
        self._forced = scenario_id

    def update(self, dt: float) -> None:
        self._sync()
        p = self.d.scene.prompt
        if self.mode == "decision" and p is not None and p.total > 0:
            p.remaining = max(0.0, p.remaining - dt)
            if p.remaining <= 0:
                self._timeout()
        elif self.mode == "idle" and self.d.idle:
            self._next_cycle()

    def key(self, k: str) -> bool:
        """Handle a key; returns True if the game used it."""
        if k == "r" and self.mode in ("idle", "busy") and not (self._ended or self.campaign.over):
            self._revoke_pending = True
            self._log("IDC REVIEW REQUESTED FOR THE NEXT CYCLE")
            return True
        if self.mode in ("over", "debrief") and k in ("1", "enter"):
            self.finished = True
            return True
        if self.mode in ("decision", "briefing", "revoke") and len(k) == 1 and k in "123456789":
            self._pick(int(k) - 1)
            return True
        return False

    def save_now(self) -> None:
        if self._ended:
            return
        self.campaign.rng_state = self.rng.getstate()
        self._save(self.campaign)

    def _sync(self) -> None:
        teams = self.d.scene.teams
        for name, tm in self.campaign.teams.items():
            teams[name] = "STANDING DOWN" if tm.status == "base" and tm.out_cycles else TEAM_LABEL[tm.status]

    # ------------------------------------------------------------------ prompts
    def _ask(self, title: str, text: str, options: list[tuple[str, bool, Callable[[], None]]],
             countdown: float, mode: str) -> None:
        self._actions = [action for _, _, action in options]
        self.d.scene.prompt = Prompt(title, text, [(label, ok) for label, ok, _ in options], countdown, countdown)
        self.mode = mode
        self._log(f"{title}: {(text.splitlines() or [''])[0][:70]}")

    def _pick(self, i: int) -> None:
        p = self.d.scene.prompt
        if p is None or not 0 <= i < len(self._actions) or not p.options[i][1]:
            return
        self._log(f"ORDER: {p.options[i][0].upper()}")
        action = self._actions[i]
        self.d.scene.prompt, self._actions, self.mode = None, [], "busy"
        action()

    def _timeout(self) -> None:
        self._log("NO ORDER GIVEN — STANDING PROCEDURE")
        self._pick([ch.key for ch in self.node.choices].index(self.node.default))

    # ------------------------------------------------------------------ cycles
    def _next_cycle(self) -> None:
        c = self.campaign
        if self._revoke_pending:
            self._revoke_pending = False
            return self._ask_revoke()
        for line in rules.start_cycle(c):
            self._log(line)
        if self._forced:
            sc, self._forced = self.scenarios[self._forced], None
            bind = self._binding(sc)
            if bind is not None:
                if sc.kind == "mission":
                    dest = self._destination()
                    bind["destination"] = dest.label
                    return self._launch(sc, bind, dest)
                return self._start(sc, bind)
        if c.since_briefing >= BRIEFING_EVERY or (c.since_briefing >= 1 and self._strike_ready()):
            offers = self._offers()
            if offers:
                return self._briefing(offers)
        pool = self._eligible("incoming")
        if pool and self.rng.random() < 0.45 + rules.max_aggression(c) / 200:
            weights = [sc.weight * (1 + rules.max_aggression(c) / 50 if sc.hostile else 1) for sc, _ in pool]
            sc, bind = pool[self.rng.choices(range(len(pool)), weights)[0]]
            return self._start(sc, bind)
        self._quiet()

    def _strike_ready(self) -> bool:
        """A strike mission can be played now, so Hammond calls a briefing early."""
        return any(sc.mission_type == "strike" and self._binding(sc) is not None
                   for sc in sorted(self.scenarios.values(), key=lambda s: s.id) if sc.kind == "mission")

    def _cycle_done(self) -> None:
        c = self.campaign
        if self.scenario is not None and self.scenario.kind == "mission":
            team = self.bind["team"]
            if c.teams[team].status == "offworld":
                c.teams[team].status = "base"
                self._log(f"{team} DEBRIEFED")
        self.scenario, self.node, self.bind, self._dest = None, None, {}, None
        self.save_now()
        if c.won:
            return self._debrief()
        self.mode = "idle"

    def _quiet(self) -> None:
        rules.quiet(self.campaign)
        self.d.run_steps([*self._event(self.rng.choice(QUIET_EVENTS)), Step(0, lambda s, p: self._cycle_done())])
        self.mode = "busy"

    # ------------------------------------------------------------------ drawing scenarios
    def _destination(self) -> Address:
        return self.rng.choice(self._canon) if self.rng.random() < 0.6 else random_address(self.rng)

    def _binding(self, sc: Scenario) -> dict | None:
        """Names for the scenario's placeholders, or None if it can't be played right now."""
        c, b = self.campaign, {}
        if sc.kind == "mission":
            teams = rules.available_teams(c)
            if not teams:
                return None
            b["team"] = teams[0]
        elif sc.team:
            teams = rules.teams_matching(c, sc.team)
            if not teams:
                return None
            b["team"] = self.rng.choice(teams)
        if sc.captive:
            captives = rules.teams_matching(c, "captured")
            if not captives:
                return None
            b["captive"] = captives[0]
        who = b.get("captive") or b.get("team")
        if who:
            b["captured_at"] = c.teams[who].captured_at or "an unknown world"
        if sc.goauld:
            lords = c.active_lords()
            if not lords:
                return None
            if sc.goauld == "aggressor":
                lord = max(lords, key=lambda l: (l.aggression, l.name))
            elif sc.goauld == "weakest":
                lord = min(lords, key=lambda l: (l.strength, -l.aggression, l.name))
            else:
                lord = self.rng.choice(lords)
            b["goauld"] = b["target"] = lord.name
        b.setdefault("destination", "the gate room")
        return b if rules.check_all(sc.when, c, b) else None

    def _eligible(self, kind: str) -> list[tuple[Scenario, dict]]:
        out = []
        for sc in sorted(self.scenarios.values(), key=lambda s: s.id):
            if sc.kind == kind:
                b = self._binding(sc)
                if b is not None:
                    out.append((sc, b))
        return out

    def _info(self, kind: str) -> str:
        i = INFO_LEVELS.index(BASE_INFO[self.campaign.difficulty])
        if (kind == "mission" and rules.has_flag(self.campaign, "ally.tokra")) or \
                (kind == "incoming" and rules.has_flag(self.campaign, "tech.lrs")):
            i = min(len(INFO_LEVELS) - 1, i + 1)
        return INFO_LEVELS[i]

    def _text(self, node: Node, kind: str) -> str:
        start = INFO_LEVELS.index(self._info(kind))
        return next(node.text[lv] for lv in INFO_LEVELS[start:] if lv in node.text)

    def _redact(self, line: str) -> str:
        kind = self.scenario.kind if self.scenario is not None else "incoming"
        if self._info(kind) == "minimal" and " CAPTURED ON " in line:
            return line.split(" CAPTURED ON ")[0] + " MISSED CHECK-IN"
        return line

    # ------------------------------------------------------------------ running scenarios
    def _start(self, sc: Scenario, bind: dict) -> None:
        self.scenario, self.bind, self._dest = sc, bind, None
        self.d.run_steps(self._chain(list(sc.visual), [Step(0, lambda s, p: self._open("start"))]))
        self.mode = "busy"

    def _open(self, name: str) -> None:
        sc = self.scenario
        node = sc.nodes[name]
        self.node = node
        options = [(rules.fill(ch.label, self.bind), rules.check_all(ch.requires, self.campaign, self.bind),
                    lambda ch=ch: self._resolve(ch.outcome)) for ch in node.choices]
        self._ask("MISSION" if sc.kind == "mission" else "DECISION", rules.fill(self._text(node, sc.kind), self.bind),
                  options, node.countdown or self.countdown, "decision")
        self.d.scene.status = "AWAITING ORDERS"

    def _resolve(self, o: Outcome) -> None:
        c = self.campaign
        if o.roll:
            chance = rules.odds(o.roll.odds, o.roll.mods, c, self.bind)
            won = self.rng.random() * 100 < chance
            self._log(f"ODDS {chance}% — {'SUCCESS' if won else 'FAILURE'}")
            return self._resolve(o.roll.win if won else o.roll.lose)
        for line in rules.apply_all(o.effects, c, self.bind):
            self._log(self._redact(line))
        if c.over:
            return self._game_over(list(o.visual))
        then = [Step(0, lambda s, p, g=o.goto: self._open(g))] if o.goto else self._finish()
        self.d.run_steps(self._chain(list(o.visual), then))
        self.save_now()

    def _finish(self) -> list[Step]:
        def close(scene, p):
            tail = sq.shutdown() if scene.horizon != "off" or scene.locked else []
            self.d.run_steps([*tail, Step(0, lambda s, q: sq.reset_scene(s), cues=("stop:klaxon",)),
                              sq.idle(2.0), Step(0, lambda s, q: self._cycle_done())])
        return [Step(0, close)]

    def _game_over(self, visuals: list[str]) -> None:
        c = self.campaign

        def red(s, p):
            s.alert, s.status = "red", "SGC OVERRUN"

        def show(s, p):
            self._ask("BASE OVERRUN", c.over, [("Return to the briefing room", True, self._leave)], 0.0, "over")
        self.d.run_steps(self._chain(visuals, [Step(0, red, "THE SGC HAS FALLEN", ("loop:klaxon",)), sq.hold(4.0),
                                               Step(0, show)]))
        self.mode = "busy"
        self._ended = True
        self._on_end(c)

    def _debrief(self) -> None:
        c = self.campaign
        rank, line = rules.rating(c)
        r = c.record
        text = (f'General Hammond: "{line}"\nRATING: {rank}\n'
                f'{r["goauld_defeated"]} System Lords defeated in {c.cycles} cycles. '
                f'Personnel lost: {r["personnel_lost"]}. Teams lost: {r["teams_lost"]}.')
        self._ask("DEBRIEF", text, [("Return to the briefing room", True, self._leave)], 0.0, "debrief")
        self._ended = True
        self._on_end(c)

    def _leave(self) -> None:
        self.finished = True

    # ------------------------------------------------------------------ IDC review
    def _ask_revoke(self) -> None:
        c = self.campaign
        options = [(f"{t}  {TEAM_LABEL[c.teams[t].status]}", c.teams[t].status != "lost",
                    lambda t=t: self._revoke(t)) for t in TEAMS]
        options.append(("Cancel", True, self._after_revoke))
        self._ask("IDC REVIEW", "Revoke and reissue which team's IDC code? That team stands down for a cycle.",
                  options, 0.0, "revoke")

    def _revoke(self, team: str) -> None:
        for line in rules.revoke(self.campaign, team):
            self._log(line)
        self._after_revoke()

    def _after_revoke(self) -> None:
        self.save_now()
        self.mode = "idle"

    # ------------------------------------------------------------------ briefings and missions
    def _offers(self) -> list[Offer]:
        pool = self._eligible("mission")
        offers: list[Offer] = []
        while pool and len(offers) < 3:
            sc, b = pool.pop(self.rng.choices(range(len(pool)), [sc.weight for sc, _ in pool])[0])
            dest = self._destination()
            b["destination"] = dest.label
            offers.append((sc, b, dest))
        return offers

    def _briefing(self, offers: list[Offer]) -> None:
        self.d.run_steps([*sq.to_briefing(self.transition), Step(0, lambda s, p: self._show_briefing(offers))])
        self.mode = "busy"

    def _show_briefing(self, offers: list[Offer]) -> None:
        options = [(f"{rules.fill(sc.brief, b)} — {sc.risk.upper()} RISK", True,
                    lambda sc=sc, b=b, a=a: self._launch(sc, b, a)) for sc, b, a in offers]
        options.append(("Stand down", True, self._stand_down))
        ready = ", ".join(rules.available_teams(self.campaign)) or "none"
        text = f'General Hammond: "Missions on the board. Teams ready: {ready}."'
        self._ask("MISSION BRIEFING", text, options, 0.0, "briefing")

    def _stand_down(self) -> None:
        self.campaign.since_briefing = 0
        self.d.run_steps([*sq.to_gateroom(self.transition), Step(0, lambda s, p: self._cycle_done())])

    def _launch(self, sc: Scenario, bind: dict, dest: Address) -> None:
        c = self.campaign
        self.scenario, self.bind, self._dest = sc, bind, dest
        c.teams[bind["team"]].status = "offworld"
        c.since_briefing = 0
        c.record["missions"] += 1
        self._log(f"{bind['team']} DEPLOYING TO {dest.label.upper()}")
        walk = sq.to_gateroom(self.transition) if self.d.scene.view_p < 1 else []
        self.d.run_steps([*walk, *self._visual("dial_out"), Step(0, lambda s, p: self._open("start"))])
        self.mode = "busy"

    # ------------------------------------------------------------------ visuals
    def _chain(self, names: list[str], then: list[Step]) -> list[Step]:
        """Play the named visuals in turn, building each one from the scene as it is when it starts."""
        def cb(s, p):
            if names:
                self.d.run_steps([*self._visual(names[0]), *self._chain(names[1:], then)])
            else:
                self.d.run_steps(then)
        return [Step(0, cb)]

    def _visual(self, name: str) -> list[Step]:
        if name == "incoming":
            return [*sq.incoming(7), *sq.kawoosh()]
        if name == "dial_out":
            return self._dial_out()
        if name == "iris_hold":
            return self._iris_hold()
        if name == "arrival":
            return self._arrival(3, "TRAVELLERS ARRIVING")
        if name == "team_return":
            team = self.bind.get("team", "SG TEAM")

            def idc(s, p):
                s.alert, s.identified, s.status = "normal", True, f"IDC: {team}"
            return [*sq.incoming(7), *sq.kawoosh(),
                    Step(0, idc, f"IDC RECEIVED — {team}", ("idc_accept", "stop:klaxon")),
                    *self._arrival(4, f"{team} COMING HOME")]
        if name == "firefight":
            return sq.firefight(8.0, self.rng, None)
        if name == "firefight_win":
            return sq.firefight(5.0, self.rng, True)
        if name == "bomb":
            return sq.bomb(4.0)
        if name == "asgard_beam":
            def beam(s, p):
                s.vaporize = math.sin(math.pi * p)
                if p >= 0.5:
                    s.figures = []
            return [Step(2.0, beam, "ASGARD TRANSPORT BEAM", ("idc_accept",))]
        s = self.d.scene                                   # an existing ambient event
        pre = []
        if s.horizon != "off" or s.locked:
            pre = [*sq.shutdown(), Step(0, lambda sc, p: sq.reset_scene(sc), cues=("stop:klaxon",))]
        return [*pre, *self._event(name)]

    def _event(self, name: str) -> list[Step]:
        ev = REGISTRY[name]
        s = self.d.scene
        dest = self._destination() if ev.kind == "outgoing" else None
        rng = random.Random(self.rng.random())      # events may draw per frame; keep that off self.rng
        return [*ev.build(EventContext(dest, rng, s, 1.0, 1.0, s.ring_angle)), sq.idle(3.0)]

    def _iris_hold(self, seconds: float = 6.0) -> list[Step]:
        hits = sorted(self.rng.uniform(0.5, seconds - 0.5) for _ in range(4))
        spots = [(self.rng.uniform(-0.6, 0.6), self.rng.uniform(-0.6, 0.6)) for _ in hits]

        def close(s, p):
            s.iris = max(s.iris, p)

        def impacts(s, p):
            t = p * seconds
            s.impacts = [[x, y, 1 - (t - h) / 0.4] for (x, y), h in zip(spots, hits) if 0 <= t - h < 0.4]
            if p >= 1:
                s.impacts = []
        return [Step(1.2, close, "IRIS CLOSING", ("iris_close",)),
                Step(seconds, impacts, "IMPACTS ON THE IRIS", ("iris_impact",))]

    def _arrival(self, n: int, log: str) -> list[Step]:
        walk = 5.0
        total = walk + n - 1

        def open_iris(s, p):
            s.iris = min(s.iris, 1 - p)

        def walk_out(s, p):
            t = p * total
            s.figures = [Figure("person", 1 - (t - i) / walk, lane) for i, lane in enumerate(LANES[:n])
                         if 0 <= t - i < walk]
            if p >= 1:
                s.figures = []
        return [Step(1.2, open_iris, cues=("iris_open",)), Step(total, walk_out, log)]

    def _dial_out(self) -> list[Step]:
        if self._dest is None:                             # dial_out used outside a mission
            self._dest = self._destination()
        addr, team = self._dest, self.bind.get("team", "SG TEAM")
        dial, _ = sq.dial(addr, self.d.scene.ring_angle)

        def through(s, p):
            t = p * 5.0
            s.figures = [Figure("person", min(1.0, (t - i * 0.6) / 3.0), lane) for i, lane in enumerate(LANES)
                         if 0 <= t - i * 0.6 < 3.0]
            if p >= 1:
                s.figures = []
        return [start_outgoing(addr), *dial, *sq.kawoosh(), Step(5.0, through, f"{team} STEPPING THROUGH"),
                *sq.shutdown(), Step(0, lambda s, p: sq.reset_scene(s))]
