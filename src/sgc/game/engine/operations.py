"""Departures, check-ins, missed check-ins and searches, returns, arrival, recovery and salvage, debriefs."""
from __future__ import annotations

from .. import clock, economy, factions, rules
from . import visuals
from .core import INTEL_ROLL
from ..state import Mission, available_teams, demote, has_specialty, rank_index
from ..world import World

SEEN = 4                                      # attention when a team departs for a Goa'uld's world
MISS = (3, 8, 15, 25)                # % chance of a missed check-in, by world danger
SEARCH = {"malp": (60, 85), "team": (80, 95)}     # a search finds the team / finds it pinned down (cumulative %)
OVERDUE = (50, 80)                   # after 12 hours: the team turns up / is captured (cumulative %); else lost
ALARM_WAIT = 5                       # game minutes a team at the gate waits, again, for an open decision
FOLLOWED = 0.15                      # chance hostiles follow a team home from a dangerous world
SALVAGE = {"crashed": 60, "shot_down": 30}     # % chance a team can bring a UAV wreck home for repair
REPAIR = economy.PRICES["uav"] // 2  # funding to repair a salvaged UAV
DRONE_HOME_MINUTES = 15              # game minutes after a team departs before it dials home a parked drone
CONTACT_TYPES = ("contact", "trade", "aid")   # these end in CONTACT; the rest in SURVEYED


class OperationsMixin:
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
        self._show(visuals.v_departure(self.d.scene if self.d else None, w, m.team))

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
        self._show(visuals.v_checkin(self.d.scene if self.d else None, m.team, keep_open=prompt))
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
        scene = self.d.scene if self.d else None
        self._show(visuals.v_drone(scene, w, "malp") if data["by"] == "malp"
                   else visuals.v_departure(scene, w, data["by"]))

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
            self._show(visuals.v_team_return(self.d.scene if self.d else None, m.team))
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
