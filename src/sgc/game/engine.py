"""The game engine: the SGC clock drives a scheduler of events; alarms wait for orders; the gate plays it all.

The engine never draws. Game logic runs in game minutes (advance); what happens is shown by queueing Steps
on the director, with director.auto off. With no director the engine runs headless (tests, the idle
simulation). Campaign randomness comes only from self.rng, drawn when events fire, never per frame, so a
campaign plays out the same at any frame rate, and a save resumes exactly: the scheduler, the missions in
flight, the pending alarms and the random state are all in the campaign.
"""
from __future__ import annotations

import math
import random
import re
from typing import Callable

from .. import sequences as sq
from ..director import Director
from ..events import REGISTRY, EventContext
from ..events.common import cleanup, start_outgoing
from ..model import Figure, Prompt, Step
from . import clock, economy, factions, orders, roster, rules, uav
from .content import TEXT_LEVELS, Node, Outcome, Scenario
from .database import team_status
from .orders import SITUATIONS
from .state import Campaign, Mission, available_teams, demote, has_specialty, rank_index, team_names
from .world import GOAULD, World, readings
from . import schedule

DETAIL = {"recruit": "full", "officer": "partial", "commander": "minimal"}
INCOMING_EVERY = (36, 96)            # game hours between random incoming wormholes
TRAVEL = {"malp": (60, 120), "uav": (45, 90)}     # game minutes until a drone's telemetry comes back
UAV_RAIL = 0.12                      # the UAV's launch rail stands here, at the foot of the ramp
UAV_CRUISE = 0.8                     # the UAV's altitude as it reaches the horizon (0..1)
DESTROYED = {"normal": 3, "toxic": 8, "radiation": 12, "extreme": 25, "no_lock": 0}
CAPTURED = {"jaffa": 25, "goauld": 40}
IDLE_SCENE = 45.0                    # real seconds of a quiet gate before an ambient scene plays
AMBIENT_WORLDS = ("probed", "surveyed", "contact")   # worlds the ambient science uplink may dial
LANES = (-0.45, -0.15, 0.15, 0.45)
TITLES = {"incoming": "INCOMING", "probe": "TELEMETRY", "checkin": "CHECK-IN", "debrief": "DEBRIEF"}
GATE_KINDS = ("dial_out", "malp_return", "checkin", "team_return", "incoming")
INBOUND = ("incoming", "checkin", "team_return", "malp_return")
# Who gets a free gate first: check-ins, then the rest of the inbound traffic, then queued dial-outs; within
# a rank, whatever came due first. So a check-in waits for one gate operation (30 minutes at most), plus any
# other check-ins already due.
GATE_RANK = {"checkin": 0, **{k: 1 for k in INBOUND if k != "checkin"}, "dial_out": 2}
ALARM_WAIT = 5                       # game minutes a team at the gate waits, again, for an open decision
MISSION_HOURS = {"survey": 24, "contact": 36, "trade": 30, "raid": 18, "study": 36, "rescue": 20, "recover": 12,
                 "mine": 48, "aid": 30}
MISSION_NEEDS = {"contact": "diplomatic", "trade": "diplomatic", "raid": "combat", "study": "science",
                 "aid": "medical"}
CONTACT_TYPES = ("contact", "trade", "aid")   # these end in CONTACT; the rest in SURVEYED
SEEN = 5                                      # attention when a team departs for a Goa'uld's world
MISS = (3, 8, 15, 25)                # % chance of a missed check-in, by world danger
SEARCH = {"malp": (60, 85), "team": (80, 95)}     # a search finds the team / finds it pinned down (cumulative %)
OVERDUE = (50, 80)                   # after 12 hours: the team turns up / is captured (cumulative %); else lost
UNSEEN = ("recovery_tick",)          # events with nothing to show, which never wait for the gate scene
_TIME_LEFT = re.compile(r"( \d+[DH])+$")        # team_status's trailing '1D 20H'
PLANNABLE = ("probed", "surveyed", "contact", "hostile")
FOLLOWED = 0.15                      # chance hostiles follow a team home from a dangerous world
INTEL_ROLL = 0.15                    # chance each intel roll in a debrief turns up a new address


def team_label(c: Campaign, name: str, timer: bool = True) -> str:
    """A team's status in the Database's words (database.team_status), or AWAY: <world> while it's offworld.
    Without the timer, the time left is dropped: the gate room's panel is narrow."""
    t = c.teams[name]
    if t.status == "offworld":
        w = c.worlds.get(t.where)
        return f"AWAY: {w.name if w is not None else t.where}"
    status = team_status(c, name)
    return status if timer else _TIME_LEFT.sub("", status)


class Engine:
    def __init__(self, campaign: Campaign, scenarios: dict[str, Scenario], director: Director | None = None,
                 pace_override: int | None = None,
                 save: Callable[[Campaign], None] | None = None,
                 on_end: Callable[[Campaign], None] | None = None,
                 log: Callable[[str], None] | None = None,
                 on_alarm: Callable[[str, str], None] | None = None):
        self.c, self.scenarios, self.d = campaign, scenarios, director
        self.pace_override = pace_override
        self._save = save or (lambda c: None)
        self._on_end = on_end or (lambda c: None)
        self._log = log or (lambda line: None)
        self._on_alarm = on_alarm or (lambda title, text: None)
        self.rng = random.Random(campaign.seed)
        if campaign.rng_state is not None:
            self.rng.setstate(campaign.rng_state)
        self.prompt: Prompt | None = None
        self.finished = False            # the player has left the game; the app returns to the menu
        self.ended = False               # the campaign is over; nothing more is saved
        self._saved_at = campaign.minutes
        self._quiet = 0.0
        self._ambient = False            # the gate is playing the ambient scene, which real traffic cuts
        self._traffic = 0                # real gate scenes queued or playing on the director (never a walk)
        self._save_failed = False        # the last save failed; logged once until one succeeds
        self._handlers: dict[str, Callable[[dict], None]] = {
            "recovery_tick": self._recovery, "incoming": self._incoming, "dial_out": self._dial_out,
            "malp_return": self._malp_return, "checkin": self._checkin, "team_return": self._team_return,
            "search_report": self._search_report, "overdue": self._overdue,
        }
        if director is not None:
            director.auto = False
        if campaign.over:                    # a fallen base stays fallen
            self._game_over()
            return
        self._sweep_alarms()
        self._show_alarm()

    # ------------------------------------------------------------------ time
    @property
    def ambient(self) -> bool:
        """The gate is playing the ambient scene (nothing real); the app may cut it."""
        return self._ambient

    @property
    def sph(self) -> int:
        return clock.seconds_per_hour(self.c.pace, self.pace_override)

    def update(self, dt: float) -> None:
        """Real seconds have passed: move the SGC clock on and keep the scene in step."""
        if self.d is not None and self.d.idle:
            self._ambient = False                          # the ambient scene, if any, has finished
            self._traffic = 0                              # ... and so has any traffic cut short
        if not self.ended:
            self.advance(clock.to_minutes(dt, self.sph), hold=True)
        self._close_idle_gate()
        self._idle_scene(dt)
        self._sync()

    def advance(self, minutes: float, hold: bool = False) -> None:
        """Run the campaign forward by game minutes: due events in order, and alarm deadlines.

        With hold (the app's frames), the clock stops at the next thing due while the gate is still showing
        earlier traffic, so no result is logged before its scene has played. It only changes how real
        seconds map to game minutes: every event still fires at its own game minute, in the same order.
        """
        c = self.c
        target = c.minutes + minutes
        while not self.ended:
            ev = c.events.peek()
            deadline = self._deadline()
            nxt = min(ev.due if ev else math.inf, deadline)
            if nxt > target:
                break
            if hold and self.showing and (deadline <= nxt or ev.kind not in UNSEEN):
                c.minutes = max(c.minutes, nxt)                 # the clock waits here for the gate scene
                return
            c.minutes = max(c.minutes, nxt)
            if deadline <= c.minutes:
                self._timeout()
            else:
                self._fire(c.events.pop())
            if c.over:
                self._game_over()
        if not self.ended:
            c.minutes = max(c.minutes, target)
            if c.minutes - self._saved_at >= 5:
                self.save_now()

    @property
    def showing(self) -> bool:
        """The gate has real traffic queued or playing. The ambient scene, its cut, and the app's walks
        between the rooms aren't traffic: they never hold the clock, and traffic queues behind them."""
        return self.d is not None and self._traffic > 0 and not self.d.idle

    def set_pace(self, pace: str) -> None:
        self.c.pace = pace
        self._log(f"PACE: {pace.upper()}")
        a = self.c.alarms[0] if self.c.alarms else None
        if a is not None and a.get("deadline") is not None:      # the open decision's window fits the new pace
            a["deadline"] = min(a["deadline"], self.c.now + clock.window(self.sph))
        self._sync_prompt()
        self.save_now()

    def save_now(self) -> None:
        if self.ended or self.c.over:                      # a fallen base is never saved
            return
        self.c.rng_state = self.rng.getstate()
        self._saved_at = self.c.minutes                    # a failed save is tried again five minutes on
        try:
            self._save(self.c)
        except OSError as e:
            if not self._save_failed:
                self._save_failed = True
                self._log(f"SAVE FAILED — {(e.strerror or type(e).__name__).upper()[:40]}")
            return
        if self._save_failed:
            self._save_failed = False
            self._log("SAVE OK")

    # ------------------------------------------------------------------ events
    def _fire(self, ev: clock.Event) -> None:
        c = self.c
        if ev.kind in GATE_KINDS and c.gate_until > c.now:
            data = {**ev.data, "since": ev.data.get("since", ev.due)} if ev.kind == "checkin" else ev.data
            c.events.push(c.gate_until, ev.kind, data)          # the gate is busy: wait for it
            return
        if ev.kind in GATE_RANK and c.events.find(lambda e: e.due <= c.now and self._gate_first(e, ev)):
            c.events.push(c.now, ev.kind, ev.data)              # traffic that goes first is due: wait behind it
            return
        handler = self._handlers.get(ev.kind)
        if handler is None:
            self._log(f"UNKNOWN EVENT {ev.kind.upper()} DROPPED")
            return
        handler(ev.data)
        self.save_now()

    @staticmethod
    def _gate_first(e: clock.Event, ev: clock.Event) -> bool:
        """Does e get a free gate before ev? By rank, and among check-ins by when each first came due."""
        if e.kind not in GATE_RANK:
            return False
        if GATE_RANK[e.kind] != GATE_RANK[ev.kind]:
            return GATE_RANK[e.kind] < GATE_RANK[ev.kind]
        return ev.kind == "checkin" and e.data.get("since", e.due) < ev.data.get("since", ev.due)

    def _occupy(self, what: str) -> None:
        self.c.gate_until = self.c.now + clock.GATE_MINUTES[what]

    def _hold(self, what: str) -> None:
        """Keep the gate busy at least this long, never shortening a longer hold already on it."""
        self.c.gate_until = max(self.c.gate_until, self.c.now + clock.GATE_MINUTES[what])

    def _recovery(self, data: dict) -> None:
        for line in rules.hourly(self.c):
            self._log(line)
        self.c.events.push(self.c.now + clock.HOUR, "recovery_tick")

    def _incoming(self, data: dict) -> None:
        self._occupy("incoming")
        drawn = self._draw("incoming", {}, on="random")
        if drawn:
            self._start(*drawn)
        if not self.c.events.find(lambda e: e.kind == "incoming"):
            self.c.events.push(self.c.now + self.rng.randint(*INCOMING_EVERY) * clock.HOUR, "incoming")

    # ------------------------------------------------------------------ player actions (the briefing room)
    def probe(self, wid: str) -> str:
        return self._launch(wid, "malp")

    def send_uav(self, wid: str) -> str:
        return self._launch(wid, "uav")

    def _launch(self, wid: str, drone: str) -> str:
        c = self.c
        w = c.worlds[wid]
        if self.ended:
            return "THE CAMPAIGN IS OVER"
        if c.stock[drone] <= 0:
            return f"NO {drone.upper()} IN STOCK"
        if w.drone:
            return f"A {w.drone.upper()} IS ALREADY ON {w.name.upper()}"
        if c.events.find(lambda e: e.kind in ("dial_out", "malp_return") and e.data.get("world") == wid):
            return f"A DRONE IS ALREADY BOUND FOR {w.name.upper()}"
        if drone == "uav" and "uav_program" not in c.upgrades:
            return "NEEDS THE UAV PROGRAM"
        c.stock[drone] -= 1
        c.events.push(c.now, "dial_out", {"op": drone, "world": wid})
        msg = f"{drone.upper()} QUEUED FOR {w.name.upper()}"
        self._log(msg)
        self.save_now()
        return msg

    def recall_drone(self, wid: str) -> str:
        w = self.c.worlds[wid]
        if not w.drone:
            return f"NO DRONE ON {w.name.upper()}"
        if self.c.events.find(lambda e: e.kind == "dial_out" and e.data.get("op") == "recall"
                              and e.data.get("world") == wid):
            return "RECALL ALREADY QUEUED"
        why = economy.charge_recall(self.c, w.drone)
        if why:
            return why
        wear = economy.recall_cost(w.drone)
        self.c.events.push(self.c.now, "dial_out", {"op": "recall", "world": wid, "wear": wear})
        msg = f"RECALL QUEUED FOR THE {w.drone.upper()} ON {w.name.upper()} — {wear} FOR WEAR"
        self._log(msg)
        self.save_now()
        return msg

    def add_note(self, wid: str, text: str) -> None:
        text = text.strip()
        if text:
            self.c.worlds[wid].notes.append((self.c.now, text))
            self.save_now()

    def set_order(self, sid: str, key: str) -> None:
        if sid not in SITUATIONS:
            raise ValueError(f"unknown situation {sid!r}")
        if key not in SITUATIONS[sid].keys:
            raise ValueError(f"unknown order {key!r} for {sid}")
        self.c.orders[sid] = key
        self._log(f"STANDING ORDER: {SITUATIONS[sid].label.upper()} — {orders.label(sid, key).upper()}")
        self.save_now()

    def revoke_idc(self, team: str) -> None:
        for line in rules.revoke(self.c, team):
            self._log(line)
        self.save_now()

    def _purchase(self, msg: str, done: bool) -> str:
        if done:
            self._log(msg)
            self.save_now()
        return msg

    def buy(self, item: str) -> str:
        """A drone ("malp", "uav") or an upgrade id."""
        if self.ended:
            return "THE CAMPAIGN IS OVER"
        done = economy.reason(self.c, item) is None
        return self._purchase(economy.buy(self.c, item), done)

    def set_reserve(self, drone: str, n: int) -> str:
        return self._purchase(economy.set_reserve(self.c, drone, n), True)

    def commission(self, specialty: str) -> str:
        if self.ended:
            return "THE CAMPAIGN IS OVER"
        done = roster.commission_reason(self.c) is None
        return self._purchase(roster.commission(self.c, specialty), done)

    def train(self, team: str, specialty: str) -> str:
        if self.ended:
            return "THE CAMPAIGN IS OVER"
        done = roster.train_reason(self.c, team, specialty) is None
        return self._purchase(roster.train(self.c, team, specialty), done)

    # ------------------------------------------------------------------ the schedule (the Database's QUEUE tab)
    def schedule_view(self) -> list[schedule.QueueItem]:
        return schedule.view(self.c, TRAVEL)

    def cancel(self, item_id: str, confirm: bool = False) -> str:
        """Cancel a dial-out still waiting for the gate. Without confirm it only asks (or says why not)."""
        if self.ended:
            return "THE CAMPAIGN IS OVER"
        msg, lines, done = schedule.cancel(self.c, item_id, confirm, TRAVEL)
        if done:
            for line in lines:
                self._log(line)
            self._log(msg)
            self.save_now()
        return msg

    def move(self, item_id: str, delta: int) -> str:
        """Move a waiting dial-out up (delta < 0) or down the gate queue; inbound traffic keeps its priority."""
        if self.ended:
            return "THE CAMPAIGN IS OVER"
        msg, done = schedule.move(self.c, item_id, delta, TRAVEL)
        if done:
            self.save_now()
        return msg

    # ------------------------------------------------------------------ drones
    def _dial_out(self, data: dict) -> None:
        op = data["op"]
        if op in ("malp", "uav"):
            self._drone_out(data["world"], op)
        elif op == "recall":
            self._recall_out(data["world"], data.get("wear", 0))
        elif op == "depart":
            self._depart(data["mission"])
        elif op == "search":
            self._search_out(data)

    def _drone_out(self, wid: str, drone: str) -> None:
        c = self.c
        w = c.worlds[wid]
        if w.env != "no_lock" or w.status != "lost":            # a known dead address isn't a new probe
            c.record["probes"] += 1
        if w.env == "no_lock":
            self._stow(drone)                        # it never left the ramp
            self._log(f"NO LOCK ON {w.name.upper()} — ADDRESS MARKED LOST")
            for line in rules.set_world_status(c, w, "lost"):
                self._log(line)
            w.reports.append((c.now, "Dialing failed: the seventh chevron would not lock."))
            self._show(self._registry("failed_dial", w))
            return
        self._occupy("probe")
        self._log(f"{drone.upper()} SENT TO {w.name.upper()}")
        c.events.push(c.now + self.rng.randint(*TRAVEL[drone]), "malp_return",
                      {"world": wid, "drone": drone, "sent": c.now})
        self._show(self._v_drone(w, drone))

    def _malp_return(self, data: dict) -> None:
        c = self.c
        w, drone = c.worlds[data["world"]], data["drone"]
        self._occupy("malp_return")
        roll = self.rng.random() * 100
        destroyed = DESTROYED[w.env] + (10 if drone == "uav" and w.inhabitants in CAPTURED else 0)
        captured = CAPTURED.get(w.inhabitants, 0)
        seen = readings(w, drone, DETAIL[c.difficulty], self.rng)
        w.last_visit = c.now
        if roll < destroyed:
            w.seen["env"] = seen["env"]
            w.reports.append((c.now, f"{drone.upper()} destroyed. Last reading: {seen['env']}."))
            self._log(f"{drone.upper()} SIGNAL LOST ON {w.name.upper()} — {seen['env'].upper()}")
            for line in rules.set_world_status(c, w, "probed"):
                self._log(line)
            if drone == "uav":
                self._show(self._v_signal_lost(w, {"env": seen["env"]}))
            return
        if roll < destroyed + captured:
            w.reports.append((c.now, f"{drone.upper()} captured. Armed humanoids seen before the feed was cut."))
            w.seen["life"] = "armed humanoids"
            self._log(f"{drone.upper()} CAPTURED ON {w.name.upper()}")
            for line in rules.capture_drone(c, w, drone):
                self._log(line)
            if drone == "uav":
                self._show(self._v_signal_lost(w, {}))
            return
        w.seen.update(seen)
        w.telemetry = [f"{k.upper()}: {v}" for k, v in seen.items()]
        w.drone = drone
        w.reports.append((c.now, f"{drone.upper()} telemetry: " + "; ".join(f"{k} {v}" for k, v in seen.items())))
        self._log(f"{drone.upper()} TELEMETRY FROM {w.name.upper()}")
        for line in rules.set_world_status(c, w, "probed"):
            self._log(line)
        if drone == "uav":                                  # a drone of ours held here shows up on the feed
            for line in rules.parse_effect("locate {world}")(c, self._wbind(w)):
                self._log(line)
        if drone == "uav" and w.inhabitants != "none" and self.rng.random() < 0.4:
            for line in rules.parse_effect("reveal name {world} from comms")(c, self._wbind(w)):
                self._log(line)
        self._show(self._v_telemetry(w, seen, drone))
        drawn = self._draw("probe", self._wbind(w))
        if drawn:
            self._start(*drawn)

    def _stow(self, drone: str) -> None:
        for line in rules.stow(self.c, drone):
            self._log(line)

    def _recall_out(self, wid: str, wear: int = 0) -> None:
        w = self.c.worlds[wid]
        if not w.drone:
            self.c.funding += wear                   # nothing left to fetch: the wear wasn't spent
            return
        self._occupy("recall")
        drone, w.drone = w.drone, None
        self._log(f"{drone.upper()} RECALLED FROM {w.name.upper()}")
        self._stow(drone)
        self._show(self._v_drone(w, drone, home=True))

    # ------------------------------------------------------------------ scenarios
    def _wbind(self, w: World) -> dict:
        return {"world": w.name, "world_id": w.id, "designation": w.id}

    def _binding(self, sc: Scenario, base: dict) -> dict | None:
        """Names for the scenario's placeholders, or None if it can't be played right now."""
        c, b = self.c, dict(base)
        if sc.team:
            teams = rules.teams_matching(c, sc.team)
            if not teams:
                return None
            b["team"] = self.rng.choice(teams)
            where = c.worlds.get(c.teams[b["team"]].where)
            b["captured_at"] = where.name if where else "an unknown world"
        if "team" in b:
            b["specialty"] = c.teams[b["team"]].specialty
        if sc.goauld:
            b["goauld"] = self.rng.choice(GOAULD)
        return b if rules.check_all(sc.when, c, b) else None

    def _draw(self, kind: str, base: dict, mission_type: str | None = None,
              on: str = "random") -> tuple[Scenario, dict] | None:
        pool = []
        for sc in sorted(self.scenarios.values(), key=lambda s: s.id):
            if sc.kind != kind or sc.on != on or (mission_type and sc.mission_type not in (None, mission_type)):
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
        self._show(self._chain(list(sc.visual), bind), urgent=sc.kind == "incoming")
        self._run(sc, bind, "start")

    def _run(self, sc: Scenario, bind: dict, name: str, follow_up: bool = False) -> None:
        """Play a node. A follow-up to an answered alarm goes to the front of the queue, without a new ring."""
        node = sc.nodes[name]
        text = rules.fill(self._text(node), bind)
        if not node.routine:
            self._raise({"type": "node", "scenario": sc.id, "node": name, "bind": bind, "deadline": None,
                         "title": TITLES[sc.kind], "text": text}, front=follow_up)
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
        self._show(self._chain(list(o.visual), bind), urgent=sc.kind == "incoming")
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

    # ------------------------------------------------------------------ alarms
    def _deadline(self) -> float:
        a = self.c.alarms[0] if self.c.alarms else None
        return a["deadline"] if a and a.get("deadline") is not None else math.inf

    def _raise(self, alarm: dict, front: bool = False) -> None:
        """Queue an alarm and ring; a follow-up to the answered alarm goes first, and doesn't ring again."""
        self.cut_ambient()                                 # an alarm is real, even with nothing to show
        self._log(f"ALARM: {alarm['title']} — {alarm['text'][:60]}")
        if front:
            self.c.alarms.insert(0, alarm)
            self._show_alarm()
        else:
            self.c.alarms.append(alarm)
            self._on_alarm(alarm["title"], alarm["text"])
            if len(self.c.alarms) == 1:
                self._show_alarm()
        self.save_now()

    def _alarm_mission(self, a: dict) -> Mission | None:
        """The mission an alarm is about, if any."""
        bind = a.get("bind")
        mid = a.get("mission") if a.get("type") == "missed_checkin" else \
            bind.get("mission") if isinstance(bind, dict) else None
        try:
            return None if mid is None else self.c.mission(int(mid))
        except (TypeError, ValueError):
            return None

    def _live(self, m: Mission) -> bool:
        """The mission's team is still out on it (under way, or recalled and not yet home)."""
        return m.state in ("active", "aborted") and self.c.teams[m.team].mission == m.id

    def _mission_alarm(self, m: Mission) -> bool:
        return any(self._alarm_mission(a) is m for a in self.c.alarms)

    def _options(self, a: dict) -> list[tuple[str, str, bool]]:
        """(choice key, label, enabled) for an alarm; empty if it can no longer be answered."""
        try:
            return self._choices(a)
        except (KeyError, TypeError, ValueError, AttributeError):
            return []                                       # malformed: it can't be shown

    def _choices(self, a: dict) -> list[tuple[str, str, bool]]:
        c = self.c
        if a["type"] == "node":
            sc = self.scenarios.get(a["scenario"])
            node = sc.nodes.get(a["node"]) if sc else None
            if node is None:
                return []
            if "mission" in a["bind"]:
                m = c.mission(int(a["bind"]["mission"]))
                if m is None or (sc.kind != "debrief" and not self._live(m)):
                    return []                               # stale: the team is no longer out there
            return [(ch.key, rules.fill(ch.label, a["bind"]), rules.check_all(ch.requires, c, a["bind"]))
                    for ch in node.choices]
        if a["type"] == "missed_checkin":
            m = c.mission(a["mission"])
            if m is None or m.state != "active":
                return []
            ok = {"malp": c.stock["malp"] > 0, "team": bool(available_teams(c)), "wait": True}
            return [(k, label, ok[k]) for k, label in SITUATIONS["missed_checkin"].choices]
        return []

    def _situation(self, a: dict) -> tuple[str | None, str]:
        if a["type"] == "node":
            node = self.scenarios[a["scenario"]].nodes[a["node"]]
            return node.situation, node.default
        return a["type"], SITUATIONS[a["type"]].default

    def _withdraw(self, a: dict) -> None:
        """Drop an alarm that can't be answered; a team left waiting on it still gets home."""
        self._log(f"ALARM WITHDRAWN: {a.get('title', '?')}")
        m = self._alarm_mission(a)
        if m is not None and self._live(m) and self.c.teams[m.team].status == "offworld" \
                and not self._mission_alarm(m) and not self.c.events.find(lambda e: e.data.get("mission") == m.id):
            self.c.events.push(max(self.c.now, m.end), "team_return", {"mission": m.id})

    def _sweep_alarms(self) -> None:
        """On load: withdraw every alarm that can't be shown (its scenario, node or mission is gone)."""
        for a in [a for a in self.c.alarms if not self._options(a)]:
            self.c.alarms.remove(a)
            self._withdraw(a)

    def _show_alarm(self) -> None:
        c = self.c
        while c.alarms and not self._options(c.alarms[0]):
            self._withdraw(c.alarms.pop(0))                 # its scenario or mission is gone
        if not c.alarms:
            self.prompt = None
            return
        a = c.alarms[0]
        if a.get("deadline") is None:
            a["deadline"] = c.now + clock.window(self.sph)
        self.prompt = Prompt(a["title"], a["text"], [(label, ok) for _, label, ok in self._options(a)])
        self._sync_prompt()

    def _sync_prompt(self) -> None:
        if self.prompt is None or not self.c.alarms or self.ended:
            return
        a = self.c.alarms[0]
        self.prompt.total = clock.to_seconds(clock.window(self.sph), self.sph)
        remaining = clock.to_seconds(a["deadline"] - self.c.minutes, self.sph)
        self.prompt.remaining = min(self.prompt.total, max(0.0, remaining))

    @property
    def alarm_title(self) -> str | None:
        return self.c.alarms[0]["title"] if self.c.alarms and not self.ended else None

    def key(self, k: str) -> bool:
        """Handle a key; returns True if the game used it."""
        if self.ended and k in ("1", "enter"):
            self.finished = True
            return True
        if self.ended or not self.c.alarms or not (len(k) == 1 and k in "123456789"):
            return False
        opts = self._options(self.c.alarms[0])
        if not opts:                                       # it went stale while it was up
            self._show_alarm()
            self.save_now()
            return True
        i = int(k) - 1
        if not 0 <= i < len(opts) or not opts[i][2]:
            return True
        self._log(f"ORDER: {opts[i][1].upper()}")
        self._answer(opts[i][0])
        return True

    def _timeout(self) -> None:
        opts = self._options(self.c.alarms[0])
        if not opts:
            self._show_alarm()                             # nothing left to order: withdraw it
            self.save_now()
            return
        sid, default = self._situation(self.c.alarms[0])
        key = orders.on_timeout(sid, self.c.orders, [(k, ok) for k, _, ok in opts], default)
        label = next(lb for k, lb, _ in opts if k == key)
        self._log(f"NO ORDER GIVEN — STANDING ORDER: {label.upper()}")
        self._answer(key)

    def _answer(self, key: str) -> None:
        c = self.c
        a = c.alarms.pop(0)
        self.prompt = None
        if a["type"] == "node":
            sc = self.scenarios[a["scenario"]]
            choice = next(ch for ch in sc.nodes[a["node"]].choices if ch.key == key)
            self._resolve(sc, a["bind"], choice.outcome, follow_up=True)
        elif a["type"] == "missed_checkin":
            self._missed_order(c.mission(a["mission"]), key)
        if c.over:
            self._game_over()
            return
        self._show_alarm()
        self.save_now()

    # ------------------------------------------------------------------ missions
    def mission_types(self, wid: str, team: str) -> list[str]:
        """The mission types this team can run on this world now: the world's options, less those that need a
        specialty the team lacks, or a captive or located drone that isn't there (or already has a team)."""
        w, tm = self.c.worlds[wid], self.c.teams[team]
        out = []
        for t in w.options:
            need = MISSION_NEEDS.get(t)
            if need and not has_specialty(tm, need):
                continue
            if t in ("rescue", "recover") and self.mission_target(wid, t) is None:
                continue
            out.append(t)
        return out

    def mission_target(self, wid: str, mtype: str) -> str | None:
        """Who a rescue is for (a team), or what a recovery is after (a drone kind); None if nothing is."""
        c = self.c
        taken = [m.target for m in c.active_missions() if m.world == wid and m.type == mtype]
        if mtype == "rescue":
            return next((n for n in team_names(c) if c.teams[n].status == "captured" and c.teams[n].where == wid
                         and n not in taken), None)
        if mtype == "recover":
            held = [d.drone for d in c.captured_drones if d.world == wid and d.located]
            for drone in taken:
                if drone in held:
                    held.remove(drone)
            return held[0] if held else None
        return None

    def assign(self, wid: str, team: str, mtype: str) -> str:
        c = self.c
        w = c.worlds[wid]
        if self.ended:
            return "THE CAMPAIGN IS OVER"
        if w.status not in PLANNABLE:
            return f"{w.name.upper()} MUST BE PROBED FIRST"
        if team not in available_teams(c):
            return f"{team} IS NOT AVAILABLE"
        if mtype not in self.mission_types(wid, team):
            return f"{team} CAN'T RUN A {mtype.upper()} MISSION ON {w.name.upper()}"
        m = Mission(len(c.missions) + 1, team, wid, mtype, c.now, c.now + self._duration(team, mtype),
                    target=self.mission_target(wid, mtype))
        c.missions.append(m)
        tm = c.teams[team]
        tm.status, tm.where, tm.mission = "offworld", wid, m.id
        c.record["missions"] += 1
        c.events.push(c.now, "dial_out", {"op": "depart", "mission": m.id})
        msg = f"{team} ASSIGNED: {mtype.upper()} OF {w.name.upper()}, {(m.end - m.start) // 60} HOURS"
        self._log(msg)
        self.save_now()
        return msg

    def _duration(self, team: str, mtype: str) -> int:
        tm = self.c.teams[team]
        hours = MISSION_HOURS[mtype] * self.rng.uniform(0.85, 1.2)
        hours *= 1 - 0.25 * roster.strength(tm, "recon")
        return round(max(12, min(72, hours)) * clock.HOUR)

    def _interval(self, team: str) -> int:
        return (6 if self.c.teams[team].specialty == "recon" else 8) * clock.HOUR

    def _mbind(self, m: Mission) -> dict:
        tm = self.c.teams[m.team]
        b = {**self._wbind(self.c.worlds[m.world]), "team": m.team, "specialty": tm.specialty, "mission": str(m.id)}
        if m.type == "rescue" and m.target:
            b["captive"] = m.target
        return b

    def _next_checkin(self, m: Mission) -> None:
        due = self.c.now + self._interval(m.team)
        if due < m.end and not self.c.events.find(lambda e: e.kind == "checkin" and e.data.get("mission") == m.id):
            self.c.events.push(due, "checkin", {"mission": m.id})

    def _depart(self, mid: int) -> None:
        c = self.c
        m = c.mission(mid)
        if m is None or m.state != "active":
            return
        self._occupy("depart")
        for line in rules.attention(c, factions.owner_of(c, m.world), SEEN):   # seen on their world
            self._log(line)
        length = m.end - m.start
        m.start, m.end = c.now, c.now + length
        self._next_checkin(m)
        c.events.push(m.end, "team_return", {"mission": m.id})
        w = c.worlds[m.world]
        self._log(f"{m.team} DEPARTING FOR {w.name.upper()}")
        self._show(self._v_departure(w, m.team))

    def _checkin(self, data: dict) -> None:
        c = self.c
        m = c.mission(data["mission"])
        if m is None or m.state != "active":
            return
        self._occupy("checkin")
        w, tm = c.worlds[m.world], c.teams[m.team]
        miss = MISS[w.danger] - 5 * rank_index(tm) - (5 if tm.specialty == "recon" else 0)
        if self.rng.random() * 100 < miss:
            c.events.cancel(lambda e: e.data.get("mission") == m.id and e.kind in ("checkin", "team_return"))
            self._raise({"type": "missed_checkin", "mission": m.id, "deadline": None, "title": "MISSED CHECK-IN",
                         "text": f"{m.team} missed its scheduled check-in from {w.name}. No signal on any channel."})
            return
        self._show(self._v_checkin(m.team))
        drawn = self._draw("checkin", self._mbind(m), m.type)
        if drawn:
            self._start(*drawn)                            # its end schedules the next check-in
        else:
            self._log(f"{m.team} CHECKED IN FROM {w.name.upper()}")
            self._next_checkin(m)

    def _check_team(self, m: Mission) -> None:
        """After a scenario: a team that's no longer out there has ended its mission."""
        c = self.c
        tm = c.teams[m.team]
        if m.state not in ("active", "aborted") or tm.mission != m.id or tm.status == "offworld":
            return
        m.state = {"captured": "captured", "lost": "lost"}.get(tm.status, "aborted")
        c.events.cancel(lambda e: e.data.get("mission") == m.id)
        tm.mission = None
        if tm.status in ("injured", "lost", "base"):
            tm.where = ""
        if tm.status in ("injured", "lost"):
            m.casualties += 1
        if tm.status == "injured":
            demote(tm)
        self._log(f"{m.team} MISSION ON {c.worlds[m.world].name.upper()} ENDED: {m.state.upper()}")

    def _missed_order(self, m: Mission, key: str) -> None:
        c = self.c
        if key == "malp" and c.stock["malp"] > 0:
            c.stock["malp"] -= 1
            c.events.push(c.now, "dial_out", {"op": "search", "mission": m.id, "by": "malp"})
            self._log(f"MALP QUEUED TO FIND {m.team}")
        elif key == "team" and available_teams(c):
            helper = available_teams(c)[0]
            ht = c.teams[helper]
            ht.status, ht.where = "offworld", m.world
            c.events.push(c.now, "dial_out", {"op": "search", "mission": m.id, "by": helper})
            self._log(f"{helper} SENT TO FIND {m.team}")
        else:
            c.events.push(c.now + 12 * clock.HOUR, "overdue", {"mission": m.id})
            self._log(f"WAITING 12 HOURS FOR {m.team}")

    def _search_out(self, data: dict) -> None:
        c = self.c
        self._occupy("search")
        delay = clock.HOUR if data["by"] == "malp" else 6 * clock.HOUR
        c.events.push(c.now + delay, "search_report", {"mission": data["mission"], "by": data["by"]})
        w = c.worlds[c.mission(data["mission"]).world]
        self._show(self._v_drone(w, "malp") if data["by"] == "malp" else self._v_departure(w, data["by"]))

    def _search_report(self, data: dict) -> None:
        c = self.c
        m, by = c.mission(data["mission"]), data["by"]
        w = c.worlds[m.world]
        if by == "malp":
            if w.drone:
                self._stow("malp")                        # a drone is already parked there: this one comes home
            else:
                w.drone = "malp"
        else:
            ht = c.teams[by]
            if ht.status == "offworld":
                ht.status, ht.where = "base", ""
        if m.state != "active":
            return
        found, pinned = SEARCH["malp" if by == "malp" else "team"]
        roll = self.rng.random() * 100
        if roll < found:
            self._log(f"{m.team} FOUND ON {w.name.upper()} — COMMS FAILURE")
            self._resume(m)
        elif roll < pinned:
            self._log(f"{m.team} FOUND PINNED DOWN ON {w.name.upper()}")
            self._team_effect(m, "injured")
        else:
            self._log(f"NO SIGN OF {m.team} ON {w.name.upper()}")
            self._team_effect(m, "captured")

    def _overdue(self, data: dict) -> None:
        m = self.c.mission(data["mission"])
        if m is None or m.state != "active":
            return
        roll = self.rng.random() * 100
        if roll < OVERDUE[0]:
            self._log(f"{m.team} MADE CONTACT AT LAST")
            self._resume(m)
        else:
            self._team_effect(m, "captured" if roll < OVERDUE[1] else "lost")

    def _team_effect(self, m: Mission, status: str) -> None:
        for line in rules.parse_effect(f"team {{team}} {status}")(self.c, self._mbind(m)):
            self._log(line)
            m.findings.append(line)
        self._check_team(m)

    def _resume(self, m: Mission) -> None:
        self._next_checkin(m)
        self.c.events.push(max(self.c.now, m.end), "team_return", {"mission": m.id})

    def _team_return(self, data: dict) -> None:
        c = self.c
        if "team" in data:                                  # a reinforcement coming home
            tm = c.teams[data["team"]]
            if tm.status == "offworld":
                tm.status, tm.where = "base", ""
                self._occupy("team_return")
                self._log(f"{data['team']} BACK FROM REINFORCING")
            return
        m = c.mission(data["mission"])
        tm = c.teams[m.team]
        if tm.status != "offworld" or tm.mission != m.id or m.state not in ("active", "aborted"):
            return
        if self._mission_alarm(m):                          # a decision about this team is still open
            c.events.push(c.now + ALARM_WAIT, "team_return", {"mission": m.id})
            return
        self._occupy("team_return")
        if c.worlds[m.world].danger >= 2 and self.rng.random() < FOLLOWED:
            drawn = self._draw("incoming", self._mbind(m), on="team_return")
            if drawn:
                self._start(*drawn)                        # its end brings the team in, or doesn't
                return
        self._arrive(m)

    def _arrive(self, m: Mission, shown: bool = False) -> None:
        """The team steps through the gate: the drone comes home, and a finished mission is debriefed."""
        c = self.c
        tm, w = c.teams[m.team], c.worlds[m.world]
        tm.status, tm.where, tm.mission = "base", "", None
        w.last_visit = c.now
        if shown:
            self._hold("team_return")                      # after the hostiles: the team's own arrival
        else:
            self._show(self._v_team_return(m.team))
        if w.drone:
            self._log(f"{m.team} BROUGHT THE {w.drone.upper()} HOME")
            self._stow(w.drone)
            w.drone = None
        if m.state == "active":
            m.state = "complete"
            self._debrief(m, w)
        else:
            self._log(f"{m.team} HOME EARLY FROM {w.name.upper()}")

    def _debrief(self, m: Mission, w: World) -> None:
        c = self.c
        tm = c.teams[m.team]
        economy.note(c, "missions")
        bind = self._mbind(m)
        lines = rules.parse_effect("xp {team} +1")(c, bind)
        lines += rules.set_world_status(c, w, "contact" if m.type in CONTACT_TYPES else "surveyed")
        drawn = self._draw("debrief", bind, m.type)
        if drawn:
            self._start(*drawn)
        rolls = 1 + rank_index(tm) + (1 if has_specialty(tm, "science") and {"ruins", "technology"} & set(w.features)
                                      else 0) + (1 if "database_analysts" in c.upgrades else 0)
        for _ in range(rolls):
            if self.rng.random() < INTEL_ROLL:
                lines += rules.parse_effect("reveal address")(c, bind)
        for line in lines:
            self._log(line)
        m.findings += lines
        self._log(f"{m.team} DEBRIEFED: {m.type.upper()} OF {w.name.upper()} COMPLETE")

    # ------------------------------------------------------------------ the end
    def _game_over(self) -> None:
        if self.ended:
            return
        c = self.c
        self.ended = True
        c.alarms.clear()
        self._log(c.over.upper())

        def red(s, p):
            s.alert, s.status = "red", "SGC OVERRUN"
        self._show([Step(0, red, "THE SGC HAS FALLEN", ("loop:klaxon",)), sq.hold(4.0)], urgent=True)
        self.prompt = Prompt("BASE OVERRUN", f"{c.over}\nDay {clock.day(c.minutes)}. "
                             f"Worlds surveyed: {c.record['surveyed']}. Teams lost: {c.record['teams_lost']}.",
                             [("Return to the briefing room", True)])
        self._on_end(c)

    # ------------------------------------------------------------------ the scene
    def _sync(self) -> None:
        self._sync_prompt()
        if self.d is None:
            return
        s = self.d.scene
        s.prompt = self.prompt
        s.teams = {name: team_label(self.c, name, timer=False).upper() for name in team_names(self.c)}

    def _idle_scene(self, dt: float) -> None:
        """A quiet gate now and then plays a science uplink to a world we know; it never touches the campaign.

        The world is picked with a visual RNG seeded from the game minute, never self.rng.
        """
        c = self.c
        if self.d is None or not self.d.idle or self.ended or c.alarms or c.gate_until > c.now:
            self._quiet = 0.0
            return
        self._quiet += dt
        if self._quiet >= IDLE_SCENE:
            self._quiet = 0.0
            known = [w for w in c.worlds.values() if w.status in AMBIENT_WORLDS]
            if known:
                self.d.run_steps(self._registry("science", random.Random(c.now).choice(known)))
                self._ambient = True

    def _close_idle_gate(self) -> None:
        """A wormhole left up once its scene is over, with no order pending, disengages on its own."""
        if self.d is None or not self.d.idle or self.c.alarms or self.prompt is not None:
            return
        s = self.d.scene
        if s.horizon != "off" or s.locked:
            self._queue([*sq.shutdown(), Step(0, lambda sc, p: sq.reset_scene(sc))])

    def cut_ambient(self) -> None:
        """The app takes the gate from the ambient scene: shut it quickly; it is no longer ambient."""
        if not self._ambient:
            return
        self._ambient = False
        if self.d is not None and not self.d.idle:
            self.d.skip(log=None)

    def _show(self, steps: list[Step], urgent: bool = False) -> None:
        """Queue a visual. Real traffic cuts the ambient scene; routine traffic is skipped while other traffic
        is on screen, and waits behind a walk between the rooms."""
        if self.d is None or not steps:
            return
        if self._ambient:
            self.cut_ambient()
        elif not urgent and self.showing:
            return
        self._queue(steps)

    def _queue(self, steps: list[Step]) -> None:
        """Put traffic on the director, counted as showing until its last step has played."""
        def played(s, p):
            self._traffic = max(0, self._traffic - 1)
        self._traffic += 1
        self.d.run_steps([*steps, Step(0, played)])

    # ------------------------------------------------------------------ visuals
    def _chain(self, names: list[str], bind: dict) -> list[Step]:
        """Play the named visuals in turn, building each one from the scene as it is when it starts."""
        if not names or self.d is None:
            return []

        def cb(s, p):
            self._queue([*self._visual(names[0], bind), *self._chain(names[1:], bind)])
        return [Step(0, cb)]

    def _visual(self, name: str, bind: dict) -> list[Step]:
        w = self.c.worlds.get(bind.get("world_id", ""))
        if name == "incoming":
            return [*sq.incoming(7), *sq.kawoosh()]
        if name == "dial_out":
            return self._v_departure(w, bind.get("team", "SG TEAM")) if w else []
        if name == "iris_hold":
            return self._iris_hold()
        if name == "arrival":
            return self._arrival(3, "TRAVELLERS ARRIVING")
        if name == "team_return":
            return self._v_team_return(bind.get("team", "SG TEAM"))
        if name == "firefight":
            return sq.firefight(8.0, random.Random(self.c.now), None)
        if name == "firefight_win":
            return sq.firefight(5.0, random.Random(self.c.now), True)
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
        return [*pre, *self._registry(name, w)]

    def _registry(self, name: str, w: World | None) -> list[Step]:
        if self.d is None:
            return []
        ev, s = REGISTRY[name], self.d.scene
        addr = w.address() if w is not None else None
        if ev.kind == "outgoing" and addr is None:
            return []
        rng = random.Random(self.c.now)          # events draw per frame; keep that off self.rng
        return [*ev.build(EventContext(addr if ev.kind == "outgoing" else None, rng, s, 1.0, 1.0, s.ring_angle)),
                sq.idle(2.0)]

    def _outgoing(self, w: World) -> list[Step]:
        dial, _ = sq.dial(w.address(), self.d.scene.ring_angle)
        return [start_outgoing(w.address()), *dial, *sq.kawoosh()]

    def _v_drone(self, w: World, drone: str, home: bool = False) -> list[Step]:
        if self.d is None:
            return []
        if drone == "uav":
            return [*self._outgoing(w), *(self._uav_home() if home else self._uav_launch()), *sq.shutdown(),
                    cleanup()]

        def roll(s, p):
            s.figures = [Figure("malp", 1 - p if home else p, 0.0)] if p < 1 else []
        verb = "RETURNING THROUGH THE GATE" if home else "IN TRANSIT"
        return [*self._outgoing(w), Step(5.0, roll, f"{drone.upper()} {verb}"), *sq.shutdown(), cleanup()]

    def _v_telemetry(self, w: World, seen: dict[str, str], drone: str = "malp") -> list[Step]:
        if self.d is None:
            return []
        if drone == "uav":
            def feed(s, p):
                s.feed = uav.make(w, seen, p) if p < 1 else None
                s.panel_title = f"TELEMETRY · {w.name.upper()}"
                s.panel_rows = [*uav.rows(uav.seed(w), p), *((k.upper(), v) for k, v in seen.items())]
            return [*self._outgoing(w), Step(6.0, feed, "TELEMETRY RECEIVED"), *sq.shutdown(), cleanup()]

        def show(s, p):
            s.panel_title = f"TELEMETRY · {w.name.upper()}"
            s.panel_rows = [(k.upper(), v) for k, v in seen.items()]
        return [*self._outgoing(w), Step(6.0, show, "TELEMETRY RECEIVED"), *sq.shutdown(), cleanup()]

    def _uav_launch(self) -> list[Step]:
        """The rail at the foot of the ramp, the UAV firing off it and climbing, then through the horizon.
        5 seconds in all, as long as the MALP's roll."""
        def on_rail(s, p):
            s.figures = [Figure("rail", UAV_RAIL), Figure("uav", UAV_RAIL)]

        def fly(s, p):
            climb = 1 - (1 - p) ** 2                                        # ease-out
            s.figures = [Figure("rail", UAV_RAIL),
                         Figure("uav", UAV_RAIL + (0.95 - UAV_RAIL) * p, alt=UAV_CRUISE * climb)]

        def through(s, p):
            s.figures = [] if p >= 1 else [Figure("rail", UAV_RAIL)]
            s.splashes = [] if p >= 1 else [[0.0, 0.14, p]]
        return [Step(0.6, on_rail, "UAV LAUNCHED"), Step(4.0, fly), Step(0.4, through, "UAV IN TRANSIT")]

    def _uav_home(self) -> list[Step]:
        """The UAV comes out of the horizon nose first, descends toward us, lands and rolls out. 5 seconds."""
        def descend(s, p):
            s.figures = [Figure("uav", 0.95 - 0.9 * p, alt=UAV_CRUISE * (1 - p * p), facing="toward")]
            s.splashes = [[0.0, 0.14, p / 0.2]] if p < 0.2 else []

        def roll_out(s, p):                  # it rolls on toward us and off the bottom of the picture
            s.figures = [] if p >= 1 else [Figure("uav", 0.05 - 0.97 * p, facing="toward")]
        return [Step(4.2, descend, "UAV RETURNING THROUGH THE GATE"), Step(0.8, roll_out, "UAV RECOVERED")]

    def _v_signal_lost(self, w: World, seen: dict[str, str]) -> list[Step]:
        """A UAV shot down or captured: its feed goes to static, SIGNAL LOST flashes, the wormhole disengages."""
        if self.d is None:
            return []

        def live(s, p):
            s.feed = uav.make(w, seen, 0.3 * p)
            s.panel_title = f"TELEMETRY · {w.name.upper()}"
            s.panel_rows = uav.rows(uav.seed(w), 0.3 * p)

        def static(s, p):
            s.feed = uav.make(w, seen, 0.3, lost=p)
            s.panel_rows = [("SIGNAL", "LOST")]
        return [*self._outgoing(w), Step(1.0, live), Step(1.5, static, "UAV SIGNAL LOST"), sq.hold(1.0),
                *sq.shutdown(), cleanup()]

    def _v_checkin(self, team: str) -> list[Step]:
        if self.d is None:
            return []

        def idc(s, p):
            s.alert, s.identified, s.status = "normal", True, f"IDC: {team}"
        return [*sq.incoming(7), *sq.kawoosh(), Step(0, idc, f"IDC RECEIVED — {team}", ("idc_accept", "stop:klaxon")),
                sq.hold(3.0, log=f"{team} CHECKING IN"), *sq.shutdown(), cleanup()]

    def _v_team_return(self, team: str) -> list[Step]:
        if self.d is None:
            return []

        def idc(s, p):
            s.alert, s.identified, s.status = "normal", True, f"IDC: {team}"
        return [*sq.incoming(7), *sq.kawoosh(),
                Step(0, idc, f"IDC RECEIVED — {team}", ("idc_accept", "stop:klaxon")),
                *self._arrival(4, f"{team} COMING HOME"), *sq.shutdown(), cleanup()]

    def _v_departure(self, w: World, team: str) -> list[Step]:
        if self.d is None:
            return []

        def through(s, p):
            t = p * 5.0
            s.figures = [Figure("person", min(1.0, (t - i * 0.6) / 3.0), lane) for i, lane in enumerate(LANES)
                         if 0 <= t - i * 0.6 < 3.0]
            if p >= 1:
                s.figures = []
        return [*self._outgoing(w), Step(5.0, through, f"{team} STEPPING THROUGH"), *sq.shutdown(), cleanup()]

    def _iris_hold(self, seconds: float = 6.0) -> list[Step]:
        rng = random.Random(self.c.now)
        hits = sorted(rng.uniform(0.5, seconds - 0.5) for _ in range(4))
        spots = [(rng.uniform(-0.6, 0.6), rng.uniform(-0.6, 0.6)) for _ in hits]

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
