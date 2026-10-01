"""Raising, showing, answering and timing out alarms; standing orders; the check-in open line."""
from __future__ import annotations

import math

from ...model import Prompt
from .. import clock, orders, rules
from ..orders import SITUATIONS
from ..state import Mission, available_teams

CHECKIN_LINE_MINUTES = 38             # game minutes a check-in's open prompt keeps its wormhole up


class AlarmsMixin:
    def _hold_line(self, team: str, mission: str) -> None:
        """A check-in just raised a prompt: its wormhole stays up and the gate stays held for it, up to
        CHECKIN_LINE_MINUTES, unless the player answers first (_close_line) or it times out (_checkin_timeout)."""
        until = self.c.now + CHECKIN_LINE_MINUTES
        self.c.gate_until = max(self.c.gate_until, until)
        self.c.events.push(until, "checkin_timeout", {"mission": int(mission), "team": team})

    def _checkin_timeout(self, data: dict) -> None:
        """A check-in's open line went unanswered for CHECKIN_LINE_MINUTES: the wormhole drops and the gate
        frees up, but the order still reaches the team eventually, so the prompt itself stays open."""
        team = data.get("team", "THE TEAM")
        self._log(f"WORMHOLE LOST — {team} WILL RECEIVE ORDERS AT NEXT CONTACT")
        self._free_gate()
        self._show(self._visual("close", {}), urgent=True)   # a lost line always shuts, even over other traffic

    def _drop_lines(self) -> None:
        """A check-in's open line can't be resumed across a save: it's simplest lost on load, as if its
        CHECKIN_LINE_MINUTES had already run out. The alarm itself is unaffected: it still waits for an order."""
        for ev in self.c.events.remove(lambda e: e.kind == "checkin_timeout"):
            self._checkin_timeout(ev.data)

    def _close_line(self, a: dict) -> None:
        """Answering a check-in's node alarm: if its line is still open, the order goes out live and the gate
        is free to shut (the scenario's own finished chain appends "close" to actually shut it). Answered after
        the line already dropped, nothing is cancelled and no order-sent line is logged."""
        bind = a.get("bind") or {}
        team = bind.get("team")
        try:
            mission = int(bind.get("mission"))
        except (TypeError, ValueError):
            return
        if self.c.events.cancel(lambda e: e.kind == "checkin_timeout" and e.data.get("mission") == mission):
            self._log(f"ORDERS SENT TO {team}")
            self._free_gate()

    def _deadline(self) -> float:
        a = self.c.alarms[0] if self.c.alarms else None
        return a["deadline"] if a and a.get("deadline") is not None else math.inf

    def _raise(self, alarm: dict, front: bool = False) -> None:
        """Queue an alarm and ring; a follow-up to the answered alarm goes first, and doesn't ring again."""
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
        if a["type"] == "victory":
            return [("stay", "Stay in command", True), ("retire", "Retire in victory", True)]
        return []

    def _situation(self, a: dict) -> tuple[str | None, str]:
        if a["type"] == "victory":
            return None, "stay"
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
            if sc.kind == "checkin":
                self._close_line(a)
            choice = next(ch for ch in sc.nodes[a["node"]].choices if ch.key == key)
            self._resolve(sc, a["bind"], choice.outcome, follow_up=True)
        elif a["type"] == "missed_checkin":
            self._missed_order(c.mission(a["mission"]), key)
        elif a["type"] == "victory" and key == "retire":
            self.retire()
            return
        if c.over:
            self._game_over()
            return
        self._show_alarm()
        self.save_now()
