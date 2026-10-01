"""Drawing, binding and running scenarios: the pool of content triggered by probes, check-ins, debriefs,
factions and arcs."""
from __future__ import annotations

from .. import missions, rules
from ..content import TEXT_LEVELS, Node, Outcome, Scenario
from .core import DETAIL
from ..state import Mission, rank_index
from ..world import GOAULD, World

TITLES = {"incoming": "INCOMING", "probe": "TELEMETRY", "checkin": "CHECK-IN", "debrief": "DEBRIEF",
          "faction": "SECURITY", "arc": "PRIORITY ONE"}
URGENT_KINDS = ("incoming", "faction", "arc")       # their visuals always play, even over other traffic
# Rescue and recover missions draw only scenarios written for them: a generic one would never free the
# captive or bring the drone home.
EXACT_TYPES = ("rescue", "recover")


class ScenariosMixin:
    def _wbind(self, w: World) -> dict:
        return {"world": w.name, "world_id": w.id, "designation": w.id}

    def _binding(self, sc: Scenario, base: dict) -> dict | None:
        """Names for the scenario's placeholders, or None if it can't be played right now."""
        c, b = self.c, dict(base)
        if sc.team:
            teams = rules.teams_matching(c, sc.team, b)
            if not teams:
                return None
            b["team"] = self.rng.choice(teams)
            tm = c.teams[b["team"]]
            where = c.worlds.get(tm.where)
            b["captured_at"] = where.name if where else "an unknown world"
            if sc.team == "territory" and where is not None:
                b.update(self._wbind(where))
                b["mission"] = str(tm.mission)          # so the scenario's end closes the mission (_check_team)
        if "team" in b:
            b["specialty"] = c.teams[b["team"]].specialty
        if sc.goauld:
            b["goauld"] = self.rng.choice(GOAULD)
        return b if rules.check_all(sc.when, c, b) else None

    def _draw(self, kind: str, base: dict, mission_type: str | None = None, on: str = "random",
              stage: str | None = None, arc: tuple[str, int] | None = None) -> tuple[Scenario, dict] | None:
        pool = []
        for sc in sorted(self.scenarios.values(), key=lambda s: s.id):
            if sc.kind != kind or sc.on != on:
                continue
            if mission_type and sc.mission_type != mission_type and (
                    sc.mission_type is not None or missions.get(mission_type).exact_scenarios):
                continue
            if sc.kind == "faction" and sc.stage != stage:
                continue
            if sc.kind == "arc" and (sc.arc, sc.arc_stage) != arc:
                continue
            b = self._binding(sc, base)
            if b is not None:
                pool.append((sc, b))
        if not pool:
            return None
        return pool[self.rng.choices(range(len(pool)), [sc.weight for sc, _ in pool])[0]]

    def _text(self, node: Node) -> str:
        """The node's text at the difficulty's level of detail, or the nearest fuller one it has."""
        fullest_first = TEXT_LEVELS[:TEXT_LEVELS.index(DETAIL[self.c.difficulty]) + 1]
        return next(node.text[lv] for lv in reversed(fullest_first) if lv in node.text)

    def _start(self, sc: Scenario, bind: dict) -> None:
        self._show(self._chain(list(sc.visual), bind), urgent=sc.kind in URGENT_KINDS)
        self._run(sc, bind, "start")

    def _run(self, sc: Scenario, bind: dict, name: str, follow_up: bool = False) -> None:
        """Play a node. A follow-up to an answered alarm goes to the front of the queue, without a new ring."""
        node = sc.nodes[name]
        text = rules.fill(self._text(node), bind)
        if not node.routine:
            if sc.kind == "checkin" and not follow_up:     # the prompt just opened: its wormhole stays up for it
                self._hold_line(bind["team"], bind["mission"])
            self._raise({"type": "node", "scenario": sc.id, "node": name, "bind": bind, "deadline": None,
                         "title": "INCOMING" if sc.kind == "faction" and "incoming" in sc.visual else TITLES[sc.kind],
                         "text": text}, front=follow_up)
            return
        self._report(bind, text)
        self._resolve(sc, bind, node.choices[0].outcome, follow_up)

    def _report(self, bind: dict, text: str) -> None:
        w = self.c.worlds.get(bind.get("world_id", ""))
        if w is not None and text:
            w.reports.append((self.c.now, text))
        for line in text.splitlines()[:1]:
            self._log(line[:90])

    def _resolve(self, sc: Scenario, bind: dict, o: Outcome, follow_up: bool = False) -> None:
        c = self.c
        if o.roll:
            team = c.teams.get(bind.get("team", ""))
            bonus = 5 * rank_index(team) if team is not None and sc.kind in ("checkin", "debrief") else 0
            chance = rules.odds(o.roll.odds, o.roll.mods, c, bind, bonus)
            won = self.rng.random() * 100 < chance
            self._log(f"ODDS {chance}% — {'SUCCESS' if won else 'FAILURE'}")
            return self._resolve(sc, bind, o.roll.win if won else o.roll.lose, follow_up)
        lines = rules.apply_all(o.effects, c, bind)
        for line in lines:
            self._log(line)
        m = c.mission(int(bind["mission"])) if "mission" in bind else None
        if m is not None:
            m.findings += [line for line in lines if not line.startswith(("SECURITY", "PERSONNEL"))]
        self._check_victory()
        finished = not o.goto and not c.over
        names = list(o.visual) + ["close"] if finished else list(o.visual)
        self._show(self._chain(names, bind), urgent=sc.kind in URGENT_KINDS)
        if c.over:
            return
        if o.goto:
            self._run(sc, bind, o.goto, follow_up)
        elif m is not None:
            self._scenario_over(sc, m)

    def _scenario_over(self, sc: Scenario, m: Mission) -> None:
        """A mission's scenario has run its course: the team comes through the gate, carries on, or is gone."""
        tm = self.c.teams[m.team]
        on_it = tm.status == "offworld" and tm.mission == m.id and m.state in ("active", "aborted")
        if sc.on == "team_return" and on_it:
            self._arrive(m, shown=True)                    # the hostiles are dealt with: the team walks in
            return
        self._check_team(m)
        if sc.kind == "checkin" and m.state == "active" and tm.status == "offworld" and tm.mission == m.id:
            self._next_checkin(m)                          # the check-in's decisions are made: next one
