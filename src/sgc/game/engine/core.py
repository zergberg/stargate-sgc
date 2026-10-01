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

from ... import sequences as sq
from ...director import Director
from ...events import REGISTRY, EventContext
from ...events.common import cleanup, start_outgoing
from ...model import Figure, Prompt, Scene, Step
from .. import arcs, clock, economy, factions, rules, trade, uav
from ..content import Scenario
from ..database import team_status
from ..state import Campaign, Mission, available_teams, demote, has_specialty, rank_index, team_names
from ..world import World, faction_name

DETAIL = {"recruit": "full", "officer": "partial", "commander": "minimal"}
INCOMING_EVERY = (36, 96)            # game hours between random incoming wormholes
MALP_FEED_S = 20.0                   # real seconds the side panel takes to fill with a MALP's readings
UAV_FEED_S = 90.0                    # real seconds of a UAV's aerial feed
UPLINK_FEED_S = 10.0                 # real seconds an uplink's extended data takes to fill the side panel
DRONE_HOME_MINUTES = 15              # game minutes after a team departs before it dials home a parked drone
SALVAGE = {"crashed": 60, "shot_down": 30}     # % chance a team can bring a UAV wreck home for repair
REPAIR = economy.PRICES["uav"] // 2  # funding to repair a salvaged UAV
UAV_RAIL = 0.12                      # the UAV's launch rail stands here, at the foot of the ramp
UAV_CRUISE = 0.8                     # the UAV's altitude as it reaches the horizon (0..1)
LANES = (-0.45, -0.15, 0.15, 0.45)
CHECKIN_FEED_S = 10.0                # real seconds a routine drone check-in's panel takes to read NOMINAL
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
ALARM_WAIT = 5                       # game minutes a team at the gate waits, again, for an open decision
CONTACT_TYPES = ("contact", "trade", "aid")   # these end in CONTACT; the rest in SURVEYED
SEEN = 4                                      # attention when a team departs for a Goa'uld's world
MISS = (3, 8, 15, 25)                # % chance of a missed check-in, by world danger
SEARCH = {"malp": (60, 85), "team": (80, 95)}     # a search finds the team / finds it pinned down (cumulative %)
OVERDUE = (50, 80)                   # after 12 hours: the team turns up / is captured (cumulative %); else lost
UNSEEN = ("recovery_tick", "uplink")   # events with nothing to show, which never wait for the gate scene
_TIME_LEFT = re.compile(r"( \d+[DH])+$")        # team_status's trailing '1D 20H'
FOLLOWED = 0.15                      # chance hostiles follow a team home from a dangerous world
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


def _shown(n: int, p: float) -> int:
    """How many of n rows a feed has sent back at progress p: one at a time, the last just before the end."""
    return min(n, math.floor(p * (n + 1)))


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

    # ------------------------------------------------------------------ missions
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
        tm = c.teams[m.team]
        if tm.status != "staging" or tm.mission != m.id:      # something happened to the team while it waited
            self._check_team(m)
            return
        tm.status = "offworld"
        self._occupy("depart")
        for line in rules.attention(c, factions.owner_of(c, m.world), SEEN):   # seen on their world
            self._log(line)
        length = m.end - m.start
        m.start, m.end = c.now, c.now + length
        self._next_checkin(m)
        c.events.push(m.end, "team_return", {"mission": m.id})
        w = c.worlds[m.world]
        if w.drone:                        # the team dials home the parked drone shortly after it arrives
            c.events.push(c.now + DRONE_HOME_MINUTES, "drone_home",
                          {"world": w.id, "mission": m.id, "team": m.team, "drone": w.drone})
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
        drawn = self._draw("checkin", self._mbind(m), m.type)
        # Assumes a routine "start" node never `goto`s to a node that raises a prompt — true of every scenario
        # bundled today, but a scenario that broke it would keep_open=False and lose its line right away.
        prompt = drawn is not None and not drawn[0].nodes["start"].routine
        self._show(self._v_checkin(m.team, keep_open=prompt))
        if drawn:
            self._start(*drawn)                            # its end schedules the next check-in
        else:
            self._log(f"{m.team} CHECKED IN FROM {w.name.upper()}")
            self._next_checkin(m)

    def _check_team(self, m: Mission) -> None:
        """After a scenario: a team that's no longer out there has ended its mission."""
        c = self.c
        tm = c.teams[m.team]
        if m.state not in ("active", "aborted") or tm.mission != m.id or tm.status in ("offworld", "staging"):
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
                self._schedule_checkin(w, "malp")
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
        for line in self._bring_home(m, w):
            self._log(line)
            m.findings.append(line)
        if m.state == "active":
            m.state = "complete"
            self._debrief(m, w)
        else:
            self._log(f"{m.team} HOME EARLY FROM {w.name.upper()}")

    def _bring_home(self, m: Mission, w: World) -> list[str]:
        """What a team brings home from the world: a parked drone, with an extended report's data if its uplink
        hadn't come yet (a full return), and a UAV wreck, if it can be salvaged and repaired."""
        return self._bring_drone(m, w) + self._salvage(w)

    def _salvage(self, w: World) -> list[str]:
        c = self.c
        if not w.wreck:
            return []
        salvaged = self.rng.random() * 100 < SALVAGE[w.wreck]
        w.wreck = None
        if not salvaged:
            return ["UAV WRECK WRITTEN OFF"]
        if c.funding < REPAIR:
            return ["UAV WRECK WRITTEN OFF — NO FUNDS FOR REPAIR"]
        c.funding -= REPAIR
        return [f"UAV WRECK SALVAGED — REPAIRED FOR {REPAIR}", *rules.stow(c, "uav")]

    def _extended_waiting(self, w: World, drone: str) -> list[str]:
        """An extended report still collecting when the drone leaves early comes home as a full return: its
        pending uplink (still collecting, or already a dial-out waiting for the gate) is cancelled and its data
        filed now, same as if the uplink itself had landed. Returns the report's lines, or none if there was no
        extended report still waiting."""
        waiting = self.c.events.remove(lambda e: e.data.get("world") == w.id and (
            e.kind == "uplink" or (e.kind == "dial_out" and e.data.get("op") == "uplink")))
        if not waiting:
            return []
        return self._extended(w, drone, self._extended_readings(w, drone))

    def _bring_drone(self, m: Mission, w: World) -> list[str]:
        """A report already rolled for this same minute (the team lands the very
        minute the uplink's gate shuts) plays as it landed — the team never overrules it."""
        c = self.c
        if not w.drone:
            return []
        drone = w.drone
        rolled = c.events.remove(lambda e: e.kind == "uplink_report" and e.data.get("world") == w.id)
        if rolled:
            self._uplink_report(rolled[0].data)
            if not w.drone:                     # captured or destroyed at the uplink: nothing left to fetch
                rules.clear_uplink(c, w.id)
                return []
            w.drone = None
            rules.clear_uplink(c, w.id)
            return [f"{m.team} BROUGHT THE {drone.upper()} HOME"] + rules.stow(c, drone)
        extended = self._extended_waiting(w, drone)
        tail = f"{m.team} BROUGHT THE {drone.upper()} AND ITS DATA HOME" if extended else \
            f"{m.team} BROUGHT THE {drone.upper()} HOME"
        w.drone = None
        rules.clear_uplink(c, w.id)
        return extended + [tail] + rules.stow(c, drone)

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
        if name == "close":
            if self.d is None:
                return []
            s = self.d.scene
            if s.horizon != "off" or s.locked:
                return [*sq.shutdown(), Step(0, lambda sc, p: sq.reset_scene(sc), cues=("stop:klaxon",))]
            return [Step(0, lambda sc, p: sq.reset_scene(sc), cues=("stop:klaxon",))]
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

    def _v_drone(self, w: World, drone: str) -> list[Step]:
        """A drone through the gate and the gate shut behind it (a search MALP; a probe plays _v_probe)."""
        if self.d is None:
            return []
        if drone == "uav":
            return [*self._outgoing(w), *self._uav_launch(), *sq.shutdown(), cleanup()]

        def roll(s, p):
            s.figures = [Figure("malp", p, 0.0)] if p < 1 else []
        return [*self._outgoing(w), Step(5.0, roll, f"{drone.upper()} IN TRANSIT"), *sq.shutdown(), cleanup()]

    def _v_recall(self, w: World, drone: str) -> list[Step]:
        """Legacy: an older save's recall, the drone coming home through the gate."""
        if self.d is None:
            return []
        if drone == "uav":
            return [*self._outgoing(w), *self._uav_home(), *sq.shutdown(), cleanup()]

        def roll(s, p):
            s.figures = [Figure("malp", 1 - p, 0.0)] if p < 1 else []
        return [*self._outgoing(w), Step(5.0, roll, "MALP RETURNING THROUGH THE GATE"), *sq.shutdown(), cleanup()]

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

    def _v_probe(self, w: World, drone: str, seen: dict[str, str], fate: str) -> list[Step]:
        """A live probe: out through the gate, its readings streaming back a row at a time while the gate stays
        open, then shutdown. A drone that's lost stops partway (the visual RNG picks where) on what the report
        keeps of it, and SIGNAL LOST."""
        if self.d is None:
            return []
        if fate == "ok":
            got, cut = dict(seen), 1.0
        else:
            got = {"env": seen["env"]} if fate == "destroyed" else {}
            cut = random.Random(self.c.now).uniform(0.2, 0.7)
        rows = [(k.upper(), v) for k, v in got.items()]
        title = f"TELEMETRY · {w.name.upper()}"

        def upto(p: float) -> list[tuple[str, str]]:
            return rows[:_shown(len(rows), p)] if fate == "ok" else rows
        if drone == "uav":
            sd = uav.seed(w)

            def feed(s, p):
                q = cut * p
                back = upto(q)
                s.feed = None if p >= 1 and fate == "ok" else uav.make(w, dict(list(got.items())[:len(back)]), q)
                s.panel_title = title
                s.panel_rows = [*uav.rows(sd, q), *back]

            def static(s, p):
                s.feed = uav.make(w, got, cut, lost=p)
                s.panel_rows = [("SIGNAL", "LOST")]
            steps = [*self._outgoing(w), *self._uav_launch(), Step(UAV_FEED_S * cut, feed, "TELEMETRY RECEIVED")]
            if fate != "ok":
                steps += [Step(1.5, static, "UAV SIGNAL LOST"), sq.hold(1.0)]
            return [*steps, *sq.shutdown(), cleanup()]

        def roll(s, p):
            s.figures = [Figure("malp", p, 0.0)] if p < 1 else []

        def show(s, p):
            s.panel_title = title
            s.panel_rows = [*upto(p), *([("SIGNAL", "LOST")] if fate != "ok" and p >= 1 else [])]
        steps = [*self._outgoing(w), Step(5.0, roll, "MALP IN TRANSIT"),
                 Step(MALP_FEED_S * cut, show, "TELEMETRY RECEIVED")]
        if fate != "ok":
            steps.append(sq.hold(1.0))
        return [*steps, *sq.shutdown(), cleanup()]

    def _v_uplink(self, w: World, outcome: str, seen: dict[str, str]) -> list[Step]:
        """The uplink: the SGC dials the drone, and the side panel fills with its extended data. Garbled data
        says so; a drone that's gone answers with no carrier."""
        if self.d is None:
            return []
        rows = [(k.upper(), v) for k, v in seen.items()] if outcome == "full" else \
            [("DATA", "GARBLED")] if outcome == "partial" else []

        def show(s, p):
            s.panel_title = f"UPLINK · {w.name.upper()}"
            s.panel_rows = rows[:_shown(len(rows), p)] if rows else [("SIGNAL", "LOST")]
        if not rows:
            return [*self._outgoing(w), Step(3.0, show, "NO CARRIER"), *sq.shutdown(), cleanup()]
        return [*self._outgoing(w), Step(UPLINK_FEED_S, show, "EXTENDED DATA RECEIVED"), *sq.shutdown(), cleanup()]

    def _v_drone_checkin(self, w: World, drone: str) -> list[Step]:
        """A parked drone's routine check-in: its known readings play back, then ALL READINGS NOMINAL."""
        if self.d is None:
            return []
        rows = [(k.upper(), v) for k, v in w.seen.items()]
        title = f"{drone.upper()} CHECK-IN · {w.name.upper()}"

        def show(s, p):
            s.panel_title = title
            s.panel_rows = rows[:_shown(len(rows), p)] if p < 1 else [*rows, ("ALL READINGS", "NOMINAL")]
        return [*self._outgoing(w), Step(CHECKIN_FEED_S, show, "ALL READINGS NOMINAL"), *sq.shutdown(), cleanup()]

    def _v_drone_home(self, w: World, team: str, drone: str) -> list[Step]:
        """A parked drone goes home right behind the team: an incoming wormhole, IDC accepted, the drone coming
        through (the UAV's landing, or the MALP rolling in — the same visuals a recall once played, reversed),
        then shutdown."""
        if self.d is None:
            return []

        def idc(s, p):
            s.alert, s.identified, s.status = "normal", True, f"IDC: {team}"
        if drone == "uav":
            through = self._uav_home()
        else:
            def roll(s, p):
                s.figures = [Figure("malp", 1 - p, 0.0)] if p < 1 else []
            through = [Step(5.0, roll, f"{drone.upper()} COMING HOME")]
        return [*sq.incoming(7), *sq.kawoosh(),
                Step(0, idc, f"IDC RECEIVED — {team}", ("idc_accept", "stop:klaxon")),
                *through, *sq.shutdown(), cleanup()]

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

    def _v_checkin(self, team: str, keep_open: bool = False) -> list[Step]:
        """A routine check-in shuts down as always. One that's about to raise a prompt (keep_open) leaves the
        wormhole up: the line stays open until the player answers or it times out (_checkin_timeout), and
        either way it's `_visual("close")`, reused from the scenario's own resolution, that shuts it."""
        if self.d is None:
            return []

        def idc(s, p):
            s.alert, s.identified, s.status = "normal", True, f"IDC: {team}"
        steps = [*sq.incoming(7), *sq.kawoosh(), Step(0, idc, f"IDC RECEIVED — {team}", ("idc_accept", "stop:klaxon")),
                 sq.hold(3.0, log=f"{team} CHECKING IN")]
        if keep_open:
            return steps
        return [*steps, *sq.shutdown(), cleanup()]

    def _v_team_return(self, team: str) -> list[Step]:
        if self.d is None:
            return []

        def idc(s, p):
            s.alert, s.identified, s.status = "normal", True, f"IDC: {team}"
        return [*sq.incoming(7), *sq.kawoosh(),
                Step(0, idc, f"IDC RECEIVED — {team}", ("idc_accept", "stop:klaxon")),
                *self._arrival(4, f"{team} COMING HOME"), *sq.shutdown(), cleanup()]

    def _v_delivery(self, w: World) -> list[Step]:
        """A trade delivery: a friendly wormhole, the iris opens, a crate rolls down the ramp."""
        if self.d is None:
            return []

        def idc(s, p):
            s.alert, s.identified, s.status = "normal", True, "IDC: TRADE PARTNER"

        def open_iris(s, p):
            s.iris = min(s.iris, 1 - p)

        def crate(s, p):
            s.figures = [Figure("crate", 1 - p, 0.0)] if p < 1 else []
        return [*sq.incoming(7), *sq.kawoosh(),
                Step(0, idc, f"TRADE DELIVERY FROM {w.name.upper()}", ("idc_accept", "stop:klaxon")),
                Step(1.2, open_iris, cues=("iris_open",)), Step(4.0, crate, "CRATE ON THE RAMP"),
                *sq.shutdown(), cleanup()]

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
