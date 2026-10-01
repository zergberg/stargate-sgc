"""Probes, drone reports, uplinks and extended reports, drone check-ins, drone_home, and the legacy
malp_return and recall."""
from __future__ import annotations

from .. import clock, rules
from . import visuals
from .core import DETAIL, INTEL_ROLL
from ..world import World, readings, subsurface

UPLINK_HOURS = {"malp": (4, 6), "uav": (3, 5)}    # game hours an extended report collects before its uplink
UPLINK_ODDS = {                      # an uplink's (full, partial, lost) %, by drone and who lives on the world
    "malp": {"calm": (85, 10, 5), "jaffa": (75, 10, 15), "goauld": (65, 10, 25)},
    "uav": {"calm": (75, 20, 5), "jaffa": (60, 20, 20), "goauld": (45, 20, 35)},
}
HARSH = ("radiation", "extreme")     # a world this harsh moves 10 more points from full to lost
DESTROYED = {"normal": 3, "toxic": 8, "radiation": 12, "extreme": 25, "no_lock": 0}
CAPTURED = {"jaffa": 25, "goauld": 40}


def uplink_odds(w: World, drone: str) -> tuple[int, int, int]:
    """An extended report's (full, partial, lost) odds at its uplink, in %."""
    full, partial, lost = UPLINK_ODDS[drone].get(w.inhabitants, UPLINK_ODDS[drone]["calm"])
    if w.env in HARSH:
        full, lost = full - 10, lost + 10
    return full, partial, lost


def uplink_outcome(w: World, drone: str, roll: float) -> str:
    """The uplink's outcome for a roll in [0, 100)."""
    full, partial, _ = uplink_odds(w, drone)
    return "full" if roll < full else "partial" if roll < full + partial else "lost"


class DronesMixin:
    def _dial_out(self, data: dict) -> None:
        op = data["op"]
        if op in ("malp", "uav"):
            self._drone_out(data["world"], op, data.get("extended", False))
        elif op == "uplink":
            self._uplink_out(data)
        elif op == "recall":
            self._recall_out(data["world"], data.get("wear", 0))
        elif op == "depart":
            self._depart(data["mission"])
        elif op == "search":
            self._search_out(data)

    def _drone_out(self, wid: str, drone: str, extended: bool = False) -> None:
        """The probe's one connection: the drone goes through and its readings stream back live while the gate
        stays open. Its fate is rolled now; drone_report applies it when the gate shuts."""
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
        self._occupy("probe" if drone == "malp" else "uav")
        self._log(f"{drone.upper()} SENT TO {w.name.upper()}")
        roll = self.rng.random() * 100
        destroyed = DESTROYED[w.env] + (10 if drone == "uav" and w.inhabitants in CAPTURED else 0)
        captured = destroyed + CAPTURED.get(w.inhabitants, 0)
        fate = "destroyed" if roll < destroyed else "captured" if roll < captured else "ok"
        seen = readings(w, drone, DETAIL[c.difficulty], self.rng)
        c.events.push(c.gate_until, "drone_report", {"world": wid, "drone": drone, "fate": fate, "seen": seen,
                                                     **({"extended": True} if extended else {})})
        self._show(visuals.v_probe(self.d.scene if self.d else None, c.now, w, drone, seen, fate))

    def _drone_report(self, data: dict) -> None:
        """The probe's gate shuts: what came back is on file, and a surviving drone stays on the world."""
        c = self.c
        w, drone, fate, seen = c.worlds[data["world"]], data["drone"], data["fate"], data["seen"]
        w.last_visit = c.now
        if fate == "destroyed":
            w.seen["env"] = seen["env"]
            w.reports.append((c.now, f"{drone.upper()} destroyed. Last reading: {seen['env']}."))
            self._log(f"{drone.upper()} SIGNAL LOST ON {w.name.upper()} — {seen['env'].upper()}")
            if drone == "uav":
                w.wreck = "shot_down" if w.inhabitants in CAPTURED else "crashed"
            for line in rules.set_world_status(c, w, "probed"):
                self._log(line)
            return
        if fate == "captured":
            w.reports.append((c.now, f"{drone.upper()} captured. Armed humanoids seen before the feed was cut."))
            w.seen["life"] = "armed humanoids"
            self._log(f"{drone.upper()} CAPTURED ON {w.name.upper()}")
            for line in rules.capture_drone(c, w, drone):
                self._log(line)
            return
        w.seen.update(seen)
        w.telemetry = [f"{k.upper()}: {v}" for k, v in seen.items()]
        w.drone = drone
        self._schedule_checkin(w, drone)
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
        if data.get("extended"):                           # it stays on collecting; the SGC dials back for it
            lo, hi = UPLINK_HOURS[drone]
            c.events.push(c.now + self.rng.randint(lo * clock.HOUR, hi * clock.HOUR), "uplink",
                          {"world": w.id, "drone": drone, "from": c.now + lo * clock.HOUR,
                           "to": c.now + hi * clock.HOUR})
            self._log(f"{drone.upper()} COLLECTING ON {w.name.upper()} — UPLINK IN {lo}–{hi} HOURS")
        drawn = self._draw("probe", self._wbind(w))
        if drawn:
            self._start(*drawn)

    def _uplink(self, data: dict) -> None:
        """An extended report has collected long enough: its uplink joins the gate queue as a dial-out. While a
        team is out on the world it waits, and the team brings the drone and its data home."""
        w = self.c.worlds[data["world"]]
        if w.drone != data["drone"]:
            return
        if self._team_on(w):
            self._uplink_later(w, data["drone"])
            return
        self.c.events.push(self.c.now, "dial_out", {"op": "uplink", "world": w.id, "drone": data["drone"]})

    def _team_on(self, w: World) -> bool:
        """A team is out on this world."""
        return any(t.status == "offworld" and t.where == w.id for t in self.c.teams.values())

    def _uplink_later(self, w: World, drone: str) -> None:
        """Ask again in an hour: a team on the world will bring the drone and its data home first."""
        now = self.c.now
        self.c.events.push(now + clock.HOUR, "uplink", {"world": w.id, "drone": drone, "from": now,
                                                        "to": now + clock.HOUR})

    def _uplink_out(self, data: dict) -> None:
        """The uplink dial: the gate holds while the drone sends its extended data. The outcome is rolled now
        and lands when the gate shuts (uplink_report)."""
        c = self.c
        w, drone = c.worlds[data["world"]], data["drone"]
        if w.drone != drone:                                 # a team brought it home first
            return
        if self._team_on(w):                                 # a team got there while the dial waited
            self._uplink_later(w, drone)
            return
        self._occupy("uplink")
        self._log(f"UPLINK TO THE {drone.upper()} ON {w.name.upper()}")
        outcome = uplink_outcome(w, drone, self.rng.random() * 100)
        seen = self._extended_readings(w, drone) if outcome == "full" else {}
        c.events.push(c.gate_until, "uplink_report", {"world": w.id, "drone": drone, "outcome": outcome,
                                                      "seen": seen})
        self._show(visuals.v_uplink(self.d.scene if self.d else None, w, outcome, seen))

    def _extended_readings(self, w: World, drone: str) -> dict[str, str]:
        """An extended report's readings: everything, at full detail, with what lies under the surface."""
        seen = readings(w, drone, "full", self.rng)
        seen["subsurface"] = subsurface(w, drone, "full")
        return seen

    def _uplink_report(self, data: dict) -> None:
        """The uplink's gate shuts: the extended data is on file, garbled, or the drone is gone."""
        c = self.c
        w, drone, outcome = c.worlds[data["world"]], data["drone"], data["outcome"]
        if w.drone != drone:
            return
        name = w.name.upper()
        if outcome == "full":
            for line in self._extended(w, drone, data["seen"]):
                self._log(line)
            return
        if outcome == "partial":
            w.reports.append((c.now, f"{drone.upper()} uplink garbled. Only the live pass is on file."))
            self._log(f"{drone.upper()} UPLINK GARBLED — {name}")
            return
        w.drone = None
        rules.clear_uplink(c, w.id)
        if w.inhabitants in CAPTURED:
            w.reports.append((c.now, f"{drone.upper()} captured before its uplink."))
            self._log(f"{drone.upper()} CAPTURED ON {name}")
            for line in rules.capture_drone(c, w, drone):
                self._log(line)
        elif drone == "malp":
            w.reports.append((c.now, "MALP destroyed before its uplink."))
            self._log(f"MALP LOST ON {name} — NO CARRIER")
        else:
            cause = "out of fuel" if self.rng.random() < 0.5 else "crashed"
            w.wreck = "crashed"
            w.reports.append((c.now, f"UAV {cause} before its uplink. The wreck is on site."))
            self._log(f"UAV DOWN ON {name} — {cause.upper()}")

    def _extended(self, w: World, drone: str, seen: dict[str, str]) -> list[str]:
        """An extended report's data on file, from its uplink or brought home by a team. A UAV's also names an
        inhabited world, finds any drone of ours held there, and gets an intel roll for a new address."""
        c = self.c
        w.seen.update(seen)
        w.telemetry = [f"{k.upper()}: {v}" for k, v in seen.items()]
        w.reports.append((c.now, f"{drone.upper()} extended report: " + "; ".join(f"{k} {v}"
                                                                                   for k, v in seen.items())))
        lines = [f"{drone.upper()} EXTENDED REPORT FROM {w.name.upper()}"]
        if drone == "uav":
            bind = self._wbind(w)
            if w.inhabitants != "none":
                lines += rules.parse_effect("reveal name {world} from comms")(c, bind)
            lines += rules.parse_effect("locate {world}")(c, bind)
            for _ in range(1 + (1 if "database_analysts" in c.upgrades else 0)):
                if self.rng.random() < INTEL_ROLL:
                    lines += rules.parse_effect("reveal address")(c, bind)
        return lines

    def _malp_return(self, data: dict) -> None:
        """Legacy: a report from before probes went live, still pending in an older save."""
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
                self._show(visuals.v_signal_lost(self.d.scene if self.d else None, w, {"env": seen["env"]}))
            return
        if roll < destroyed + captured:
            w.reports.append((c.now, f"{drone.upper()} captured. Armed humanoids seen before the feed was cut."))
            w.seen["life"] = "armed humanoids"
            self._log(f"{drone.upper()} CAPTURED ON {w.name.upper()}")
            for line in rules.capture_drone(c, w, drone):
                self._log(line)
            if drone == "uav":
                self._show(visuals.v_signal_lost(self.d.scene if self.d else None, w, {}))
            return
        w.seen.update(seen)
        w.telemetry = [f"{k.upper()}: {v}" for k, v in seen.items()]
        w.drone = drone
        self._schedule_checkin(w, drone)
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
        self._show(visuals.v_telemetry(self.d.scene if self.d else None, w, seen, drone))
        drawn = self._draw("probe", self._wbind(w))
        if drawn:
            self._start(*drawn)

    def _stow(self, drone: str) -> None:
        for line in rules.stow(self.c, drone):
            self._log(line)

    def _recall_out(self, wid: str, wear: int = 0) -> None:
        """Legacy: a paid recall queued in an older save still dials and brings the drone home."""
        w = self.c.worlds[wid]
        if not w.drone:
            self.c.funding += wear                   # nothing left to fetch: the wear wasn't spent
            return
        self._occupy("recall")
        drone, w.drone = w.drone, None
        rules.clear_uplink(self.c, wid)
        self._log(f"{drone.upper()} RECALLED FROM {w.name.upper()}")
        self._stow(drone)
        self._show(visuals.v_recall(self.d.scene if self.d else None, w, drone))

    def _schedule_checkin(self, w: World, drone: str) -> None:
        """Queue this drone's next check-in, 8 game hours out."""
        self.c.events.push(self.c.now + clock.CHECKIN_HOURS * clock.HOUR, "drone_checkin",
                           {"world": w.id, "drone": drone})

    def _drone_checkin(self, data: dict) -> None:
        """A parked drone's scheduled check-in: no roll, no outcome, just confirmation it's still there. It
        always reschedules itself, 8 hours on. A team already out on the world covers it instead: this one
        neither dials nor shows."""
        c = self.c
        w, drone = c.worlds[data["world"]], data["drone"]
        if w.drone != drone:
            return                               # already gone: its check-in was cleared, this one is stale
        if self._team_on(w):
            self._schedule_checkin(w, drone)
            return
        self._occupy("drone_checkin")
        w.last_visit = c.now
        self._log(f"{drone.upper()} CHECK-IN FROM {w.name.upper()} — ALL READINGS NOMINAL")
        self._schedule_checkin(w, drone)
        self._show(visuals.v_drone_checkin(self.d.scene if self.d else None, w, drone))

    def _drone_home(self, data: dict) -> None:
        """A team dials home the parked drone it found right after it arrived. Nothing happens if the team's
        situation changed before this was due (captured, lost, recalled, the mission ended) or the drone is
        already gone. An extended report still collecting comes home now too, as a full return."""
        c = self.c
        m = c.mission(data["mission"])
        w = c.worlds[data["world"]]
        tm = c.teams.get(data["team"])
        if (m is None or m.state != "active" or tm is None or tm.status != "offworld" or tm.mission != m.id
                or tm.where != w.id or w.drone != data["drone"]):
            return
        drone, team = data["drone"], data["team"]
        self._occupy("drone_home")
        extended = self._extended_waiting(w, drone)
        w.drone = None
        rules.clear_uplink(c, w.id)
        lines = extended + [f"{team} SENT THE {drone.upper()} HOME FROM {w.name.upper()}"] + rules.stow(c, drone)
        for line in lines:
            self._log(line)
        m.findings.extend(lines)
        self._show(visuals.v_drone_home(self.d.scene if self.d else None, w, team, drone))
