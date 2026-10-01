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

from ...director import Director
from ...model import Prompt, Scene
from .. import arcs, clock, economy, factions, rules, trade
from ..content import Scenario
from ..database import team_status
from ..state import Campaign, available_teams, team_names
from ..world import faction_name

DETAIL = {"recruit": "full", "officer": "partial", "commander": "minimal"}
INCOMING_EVERY = (36, 96)            # game hours between random incoming wormholes
GATE_KINDS = ("dial_out", "malp_return", "checkin", "team_return", "incoming", "trade_delivery", "faction_action",
              "drone_checkin", "drone_home")
IDLE_PANEL_TITLES = (Scene().panel_title, "SENSORS")   # Scene's own default, and the one reset_scene restores
INBOUND = ("incoming", "checkin", "team_return", "malp_return", "trade_delivery", "faction_action", "drone_checkin",
           "drone_home")
SHARE_TRUST = 50                     # an ally this friendly may share an address at a funding review...
SHARE_ODDS = 0.5                     # ...this often
# Who gets a free gate first: check-ins, then the rest of the inbound traffic, then queued dial-outs; within
# a rank, whatever came due first. So a check-in waits for one gate operation (30 minutes at most), plus any
# other check-ins already due.
GATE_RANK = {"checkin": 0, **{k: 1 for k in INBOUND if k != "checkin"}, "dial_out": 2}
UNSEEN = ("recovery_tick", "uplink")   # events with nothing to show, which never wait for the gate scene
_TIME_LEFT = re.compile(r"( \d+[DH])+$")        # team_status's trailing '1D 20H'
INTEL_ROLL = 0.15                    # chance each intel roll in a debrief turns up a new address


def team_label(c: Campaign, name: str, timer: bool = True) -> str:
    """A team's status in the Database's words (database.team_status), or AWAY: <world> while it's offworld and
    STAGING: <world> while it waits for the gate. Without the timer, the time left is dropped: the gate room's
    panel is narrow."""
    t = c.teams[name]
    if t.status in ("offworld", "staging"):
        w = c.worlds.get(t.where)
        return f"{'AWAY' if t.status == 'offworld' else 'STAGING'}: {w.name if w is not None else t.where}"
    status = team_status(c, name)
    return status if timer else _TIME_LEFT.sub("", status)


class CoreMixin:
    def __init__(self, campaign: Campaign, scenarios: dict[str, Scenario], director: Director | None = None,
                 pace_override: int | None = None,
                 save: Callable[[Campaign], None] | None = None,
                 on_end: Callable[[Campaign], None] | None = None,
                 log: Callable[[str], None] | None = None,
                 on_alarm: Callable[[str, str], None] | None = None,
                 on_victory: Callable[[Campaign], None] | None = None):
        self.c, self.scenarios, self.d = campaign, scenarios, director
        self.pace_override = pace_override
        self._save = save or (lambda c: None)
        self._on_end = on_end or (lambda c: None)
        self._log = log or (lambda line: None)
        self._on_alarm = on_alarm or (lambda title, text: None)
        self._on_victory = on_victory or (lambda c: None)
        self.rng = random.Random(campaign.seed)
        if campaign.rng_state is not None:
            self.rng.setstate(campaign.rng_state)
        self.prompt: Prompt | None = None
        self.finished = False            # the player has left the game; the app returns to the menu
        self.ended = False               # the campaign is over; nothing more is saved
        self._saved_at = campaign.minutes
        self._traffic = 0                # real gate scenes queued or playing on the director (never a walk)
        self._save_failed = False        # the last save failed; logged once until one succeeds
        self._handlers: dict[str, Callable[[dict], None]] = {
            "recovery_tick": self._recovery, "incoming": self._incoming, "dial_out": self._dial_out,
            "drone_report": self._drone_report, "uplink": self._uplink, "uplink_report": self._uplink_report,
            "malp_return": self._malp_return, "checkin": self._checkin, "team_return": self._team_return,
            "search_report": self._search_report, "overdue": self._overdue,
            "funding_review": self._funding_review, "faction_action": self._faction_action,
            "trade_delivery": self._trade_delivery, "arc_step": self._arc_step,
            "drone_checkin": self._drone_checkin, "drone_home": self._drone_home,
            "checkin_timeout": self._checkin_timeout,
        }
        if director is not None:
            director.auto = False
        if campaign.over:                    # a fallen base stays fallen
            self._game_over()
            return
        self._stage2_start()
        self._sweep_alarms()
        self._drop_lines()
        self._show_alarm()

    # ------------------------------------------------------------------ time
    @property
    def sph(self) -> int:
        return clock.seconds_per_hour(self.c.pace, self.pace_override)

    def update(self, dt: float) -> None:
        """Real seconds have passed: move the SGC clock on and keep the scene in step."""
        if self.d is not None and self.d.idle:
            self._traffic = 0                              # any traffic cut short is done
        if not self.ended:
            self.advance(clock.to_minutes(dt, self.sph), hold=True)
        self._close_idle_gate()
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
        """The gate has real traffic queued or playing. A walk between the rooms isn't traffic: it never holds
        the clock, and traffic queues behind it."""
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

    def _free_gate(self) -> None:
        """Lower the gate's hold to now, and wake only the gate traffic that was actually deferred while it
        was busy — not anything merely due in that window on its own, like a staged departure or a random
        incoming — in the order each was first due, so an early free doesn't let later-queued traffic cut
        in front of it. _fire always defers a GATE_KINDS event by pushing it to exactly c.gate_until, so a
        due that still matches the old gate_until is how a deferred event is told apart from one that isn't."""
        c = self.c
        old = c.gate_until
        c.gate_until = min(old, c.now)
        for ev in c.events.remove(lambda e: e.kind in GATE_KINDS and c.now < e.due == old):
            c.events.push(c.now, ev.kind, ev.data)

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

    def _stage2_start(self) -> None:
        """Whatever Stage 2 keeps pending is pending: a funding review, and an action for every Goa'uld that
        is curious or worse (a save from before these existed gets them now)."""
        c = self.c
        if not c.events.find(lambda e: e.kind == "funding_review"):
            c.events.push(economy.next_review(c.now), "funding_review")
        for fid in factions.GOAULD:
            factions.ensure_action(c, fid)

    def _funding_review(self, data: dict) -> None:
        """The weekly review (it schedules the next); a friendly ally may share an address."""
        c = self.c
        for line in economy.review(c):
            self._log(line)
        for fid in factions.ALLIES:
            f = c.factions[fid]
            if f.known and f.trust >= SHARE_TRUST and self.rng.random() < SHARE_ODDS:
                w = rules.new_address(c, f"intel from {faction_name(fid)}")
                economy.note(c, "intel")
                self._log(f"{faction_name(fid).upper()} SHARED AN ADDRESS: {w.id}")

    def _faction_action(self, data: dict) -> None:
        """A Goa'uld acts against Earth, at its stage; the next action is drawn unless it has lost interest."""
        c, fid = self.c, data["faction"]
        stage = factions.stage_of(c, fid)
        if stage != "unaware":
            drawn = self._draw("faction", factions.bind(c, fid), stage=stage)
            if drawn:
                sc, bind = drawn
                if "incoming" in sc.visual:
                    self._occupy("incoming")
                self._start(sc, bind)
        factions.schedule_next(c, fid, self.rng)

    def _trade_delivery(self, data: dict) -> None:
        c = self.c
        d = c.deal(data["deal"])
        lines, arrived = trade.deliver(c, data["deal"], self.rng.random() * 100)
        for line in lines:
            self._log(line)
        if arrived:
            self._occupy("trade_delivery")
            self._show(self._v_delivery(c.worlds[d.world]))

    def _arc_step(self, data: dict) -> None:
        """An arc's stage has come due: play its scenario, unless the arc has moved on since."""
        c = self.c
        aid, stage = data["arc"], data["stage"]
        st = c.arcs.get(aid)
        if st is None or st.state != "active" or st.stage != stage:
            return
        arc = arcs.ARCS[aid]
        w = arcs.arc_world(c, aid)
        base = {**(self._wbind(w) if w is not None else {}), **factions.bind(c, arc.faction)}
        drawn = self._draw("arc", base, arc=(aid, stage))
        if drawn:
            self._start(*drawn)
        elif arc.endgame == stage:                       # nothing to play at the endgame: the arc is lost
            for line in arcs.fail(c, aid):
                self._log(line)
            self._check_victory()
        else:
            self._log(f"{arc.title.upper()}: NO WORD")

    # ------------------------------------------------------------------ the scene
    def _sync(self) -> None:
        self._sync_prompt()
        if self.d is None:
            return
        s = self.d.scene
        s.prompt = self.prompt
        c = self.c
        away_first = sorted(team_names(c), key=lambda n: (n in available_teams(c), int(n.split("-")[1])))
        s.teams = {name: team_label(c, name, timer=False).upper() for name in away_first}
        line = c.events.find(lambda e: e.kind == "checkin_timeout")
        if line:
            ev = line[0]
            title = f"TEAM ON THE LINE · {ev.data.get('team', '?')}"
            if s.panel_title in IDLE_PANEL_TITLES or s.panel_title == title:
                s.panel_title = title
                s.panel_rows = [("TIME LEFT", f"{max(0, round(ev.due - c.now))} MIN")]

