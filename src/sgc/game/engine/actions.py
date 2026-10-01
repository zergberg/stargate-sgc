"""The briefing-room player actions (probing, assigning, requisitions, standing orders, the roster) and
the schedule view (the Database's QUEUE tab)."""
from __future__ import annotations

from .. import clock, economy, missions, orders, roster, rules, schedule
from ..orders import SITUATIONS
from ..state import Mission, available_teams, has_specialty

EXTENDED = {"malp": "MALP EXTENDED REPORT", "uav": "UAV EXTENSIVE SURVEY"}
MISSION_HOURS = {"survey": 24, "contact": 36, "trade": 30, "raid": 18, "study": 36, "rescue": 20,
                 "recover": 12, "mine": 48, "aid": 30}
MISSION_NEEDS = {"contact": "diplomatic", "trade": "diplomatic", "raid": "combat", "study": "science",
                 "aid": "medical"}
PLANNABLE = ("probed", "surveyed", "contact", "hostile")


class ActionsMixin:
    def probe(self, wid: str, extended: bool = False) -> str:
        return self._launch(wid, "malp", extended)

    def send_uav(self, wid: str, extended: bool = False) -> str:
        return self._launch(wid, "uav", extended)

    def _launch(self, wid: str, drone: str, extended: bool = False) -> str:
        """Queue a probe; an extended one stays on to collect, and reports again at its uplink."""
        c = self.c
        w = c.worlds[wid]
        if self.ended:
            return "THE CAMPAIGN IS OVER"
        if c.stock[drone] <= 0:
            return f"NO {drone.upper()} IN STOCK"
        if w.drone:
            return f"A {w.drone.upper()} IS ALREADY ON {w.name.upper()}"
        if c.events.find(lambda e: e.kind in ("dial_out", "drone_report", "malp_return")
                         and e.data.get("world") == wid):
            return f"A DRONE IS ALREADY BOUND FOR {w.name.upper()}"
        if drone == "uav" and "uav_program" not in c.upgrades:
            return "NEEDS THE UAV PROGRAM"
        c.stock[drone] -= 1
        c.events.push(c.now, "dial_out", {"op": drone, "world": wid, **({"extended": True} if extended else {})})
        msg = f"{EXTENDED[drone] if extended else drone.upper()} QUEUED FOR {w.name.upper()}"
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
        if self.ended:
            return "THE CAMPAIGN IS OVER"
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
        return schedule.view(self.c)

    def cancel(self, item_id: str, confirm: bool = False) -> str:
        """Cancel a dial-out still waiting for the gate. Without confirm it only asks (or says why not)."""
        if self.ended:
            return "THE CAMPAIGN IS OVER"
        msg, lines, done = schedule.cancel(self.c, item_id, confirm)
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
        msg, done = schedule.move(self.c, item_id, delta)
        if done:
            self.save_now()
        return msg

    # ------------------------------------------------------------------ missions
    def mission_types(self, wid: str, team: str) -> list[str]:
        """The mission types this team can run on this world now: the world's options, less those that need a
        specialty the team lacks, or a captive or located drone that isn't there (or already has a team)."""
        w, tm = self.c.worlds[wid], self.c.teams[team]
        out = []
        for t in w.options:
            mt = missions.get(t)
            if mt.needs and not has_specialty(tm, mt.needs):
                continue
            if mt.target is not missions.base.NO_TARGET and self.mission_target(wid, t) is None:
                continue
            out.append(t)
        return out

    def mission_target(self, wid: str, mtype: str) -> str | None:
        """Who a rescue is for (a team), or what a recovery is after (a drone kind); None if nothing is."""
        return missions.get(mtype).target(self, wid)

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
        tm.status, tm.where, tm.mission = "staging", wid, m.id      # offworld once the gate takes it (_depart)
        c.record["missions"] += 1
        c.events.push(c.now, "dial_out", {"op": "depart", "mission": m.id})
        msg = f"{team} ASSIGNED: {mtype.upper()} OF {w.name.upper()}, {(m.end - m.start) // 60} HOURS"
        self._log(msg)
        self.save_now()
        return msg

    def _duration(self, team: str, mtype: str) -> int:
        tm = self.c.teams[team]
        hours = missions.get(mtype).hours * self.rng.uniform(0.85, 1.2)
        hours *= 1 - 0.25 * roster.strength(tm, "recon")
        return round(max(12, min(72, hours)) * clock.HOUR)
