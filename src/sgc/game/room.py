"""The briefing room's controls: the dialing list with actions on each address, the roster, the standing
orders and the pace. A pure controller: it reads the campaign and calls the engine; screens.py draws it."""
from __future__ import annotations

from typing import Callable

from . import orders
from .clock import HOUR, PACE_NAMES
from .engine import PLANNABLE, Engine, team_label
from .orders import SITUATIONS
from .rules import STAND_DOWN, stands_down
from .state import available_teams, rank, team_names

Handler = Callable[[], "str | None"]

MAIN = ("DIALING LIST", "TEAMS", "STANDING ORDERS", "PACE", "BACK TO THE GATE ROOM")
TITLES = {"main": "BRIEFING ROOM", "worlds": "DIALING LIST", "world": "ADDRESS", "team_pick": "ASSIGN A TEAM",
          "type_pick": "MISSION TYPE", "note": "ADD A NOTE", "teams": "TEAMS", "team": "TEAM",
          "orders": "STANDING ORDERS", "pace": "PACE", "revoke": "REVOKE THE IDC?"}
PARENT = {"worlds": "main", "world": "worlds", "team_pick": "world", "type_pick": "team_pick", "note": "world",
          "teams": "main", "team": "teams", "orders": "main", "pace": "main", "revoke": "team"}
CANCEL_KEYS = ("ctrl-c", "escape")
PACE_LOCKED = "SET IN CONFIG (game_pace)"
# Short situation names for the 38-column panel; the current order goes on the line below.
SHORT = {"unknown_idc": "Unknown or no IDC", "hostiles_following": "Our IDC, hostiles following",
         "bad_idc": "Compromised or revoked IDC", "object": "Object through the gate",
         "missed_checkin": "Missed check-in", "under_fire": "Team under fire",
         "contact_offer": "Contact or trade offer"}


class Room:
    def __init__(self, engine: Engine):
        self.e = engine
        self.screen, self.sel = "main", 0
        self.world_id: str | None = None
        self.team: str | None = None
        self.note = ""
        self.notice = ""

    @property
    def c(self):
        return self.e.c

    @property
    def title(self) -> str:
        if self.screen in ("world", "note") and self.world_id:
            return self.c.worlds[self.world_id].name.upper()
        if self.screen == "team" and self.team:
            return self.team
        return TITLES[self.screen]

    @property
    def text_mode(self) -> bool:
        return self.screen == "note"

    def _team_line(self, name: str) -> str:
        t = self.c.teams[name]
        return f"{name} · {t.specialty.upper()} · {rank(t).upper()}"

    def _status(self, name: str) -> str:
        """The roster's status line: where an offworld team is, or the status with its time left."""
        return team_label(self.c, name)

    def _worlds(self) -> list[str]:
        return list(self.c.worlds)

    def _teams(self) -> list[str]:
        return team_names(self.c)

    def _types(self) -> list[str]:
        return self.e.mission_types(self.world_id, self.team) if self.world_id and self.team else []

    def _world_table(self) -> list[tuple[str, bool, str, "Handler"]]:
        """The world screen's actions: (label, enabled, reason when disabled, handler). One table drives
        both the item list and the dispatch, so they can't drift apart."""
        c, w = self.c, self.c.worlds[self.world_id]
        teams_available, plannable = bool(available_teams(c)), w.status in PLANNABLE
        bound = f"A {w.drone.upper()} IS ALREADY ON {w.name.upper()}" if w.drone else ""
        return [
            (f"MALP PROBE ({c.stock['malp']} LEFT)", c.stock["malp"] > 0 and not w.drone,
             "NO MALPS LEFT" if c.stock["malp"] <= 0 else bound, lambda: self.e.probe(self.world_id)),
            (f"UAV FLIGHT ({c.stock['uav']} LEFT)", c.stock["uav"] > 0 and not w.drone,
             "NO UAVS LEFT" if c.stock["uav"] <= 0 else bound, lambda: self.e.send_uav(self.world_id)),
            ("RECALL DRONE", bool(w.drone), f"NO DRONE ON {w.name.upper()}",
             lambda: self.e.recall_drone(self.world_id)),
            ("ASSIGN TEAM", teams_available and plannable,
             "PROBE IT FIRST" if not plannable else "NO TEAM AVAILABLE", self._start_assign),
            ("ADD NOTE", True, "", self._start_note),
        ]

    def _start_assign(self) -> None:
        self._go("team_pick")
        return None

    def _start_note(self) -> None:
        self.note = ""
        self._go("note")
        return None

    def items(self) -> list[tuple[str, bool]]:
        c = self.c
        if self.screen == "main":
            return [("PACE · LOCKED", False) if label == "PACE" and self.e.pace_override is not None
                    else (label, True) for label in MAIN]
        if self.screen == "worlds":
            out = []
            for wid in self._worlds():
                w = c.worlds[wid]
                drone = f" · {w.drone.upper()}" if w.drone else ""
                out.append((f"{w.name[:16]:<16} {w.status.upper()}{drone}", True))
            return out + [("BACK", True)]
        if self.screen == "world":
            return [(label, ok) for label, ok, _, _ in self._world_table()] + [("BACK", True)]
        if self.screen == "team_pick":
            free = available_teams(c)
            return [(self._team_line(t), True) if t in free else (f"{self._team_line(t)}\n{self._status(t)}", False)
                    for t in self._teams()] + [("BACK", True)]
        if self.screen == "type_pick":
            return [(t.upper(), True) for t in self._types()] + [("BACK", True)]
        if self.screen == "teams":
            return [(f"{self._team_line(t)}\n{self._status(t)}", True) for t in self._teams()] + [("BACK", True)]
        if self.screen == "team":
            return [("REVOKE AND REISSUE THE IDC", c.teams[self.team].status != "lost"), ("BACK", True)]
        if self.screen == "revoke":
            down = self._extends(self.team)
            return [(f"REVOKE, STAND DOWN {STAND_DOWN // HOUR}H" if down else "REVOKE AND REISSUE", True),
                    ("BACK", True)]
        if self.screen == "orders":
            return [(f"{SHORT.get(sid, s.label)}\n{orders.label(sid, c.orders[sid])}", True)
                    for sid, s in SITUATIONS.items()] + [("BACK", True)]
        if self.screen == "pace":
            ok = self.e.pace_override is None
            return [(("• " if c.pace == p else "  ") + p.upper(), ok) for p in PACE_NAMES] + [("BACK", True)]
        return []

    def _go(self, screen: str) -> None:
        self.screen, self.sel = screen, 0

    def back(self) -> tuple | None:
        if self.screen == "main":
            return ("close",)
        self._go(PARENT[self.screen])
        return None

    def key(self, k: str) -> tuple | None:
        """Handle a key; returns ("close",) when the player leaves the briefing room."""
        if self.screen == "note":
            if k in CANCEL_KEYS:
                self.note = ""
                self._go("world")
            elif k.startswith("ch:"):
                self.note += k[3:]
            elif k == "backspace":
                self.note = self.note[:-1]
            elif k == "enter":
                if self.note.strip():
                    self.e.add_note(self.world_id, self.note)
                    self.notice = "NOTE ADDED"
                self.note = ""
                self._go("world")
            return None
        items = self.items()
        if k == "q":
            return self.back()
        if k == "up":
            self.sel = (self.sel - 1) % len(items)
        elif k == "down":
            self.sel = (self.sel + 1) % len(items)
        elif k == "enter":
            return self._activate(self.sel)
        elif len(k) == 1 and k.isdigit() and 1 <= int(k) <= len(items):
            self.sel = int(k) - 1
            return self._activate(self.sel)
        return None

    def _reason(self, i: int) -> str:
        """Why a disabled item can't be activated right now."""
        if self.screen == "world":
            return self._world_table()[i][2]
        if self.screen == "team":
            return f"{self.team} IS LOST"
        if self.screen == "team_pick":
            team = self._teams()[i]
            return f"{team}: {self._status(team)}"
        if self.screen in ("main", "pace"):
            return PACE_LOCKED
        return ""

    def _activate(self, i: int) -> tuple | None:
        items = self.items()
        label, ok = items[i]
        if not ok:
            self.notice = self._reason(i)
            return None
        if label == "BACK":
            return self.back()
        self.notice = ""
        c = self.c
        if self.screen == "main":
            if label == MAIN[-1]:
                return ("close",)
            self._go({"DIALING LIST": "worlds", "TEAMS": "teams", "STANDING ORDERS": "orders", "PACE": "pace"}[label])
        elif self.screen == "worlds":
            self.world_id = self._worlds()[i]
            self._go("world")
        elif self.screen == "world":
            _, _, _, handler = self._world_table()[i]
            result = handler()
            if result is not None:
                self.notice = result
        elif self.screen == "team_pick":
            self.team = self._teams()[i]
            self._go("type_pick")
        elif self.screen == "type_pick":
            self.notice = self.e.assign(self.world_id, self.team, self._types()[i])
            self._go("world")
        elif self.screen == "teams":
            self.team = self._teams()[i]
            self._go("team")
        elif self.screen == "team":
            self._go("revoke")
        elif self.screen == "revoke":
            self.e.revoke_idc(self.team)
            self._go("team")
            self.notice = f"{self.team} HAS A NEW IDC · {team_label(c, self.team)}"
        elif self.screen == "orders":
            sid = list(SITUATIONS)[i]
            self.e.set_order(sid, orders.cycle(dict(c.orders), sid))
        elif self.screen == "pace":
            self.e.set_pace(PACE_NAMES[i])
        return None

    def _extends(self, name: str) -> bool:
        """Would revoking this team's IDC keep it out longer than it already is?"""
        t = self.c.teams[name]
        return stands_down(t) and t.until < self.c.now + STAND_DOWN

    def detail(self) -> list[str]:
        """Lines about the selected address or team, for the screen above the actions."""
        if self.screen in ("team", "revoke") and self.team:
            t = self.c.teams[self.team]
            lines = [self._team_line(self.team), self._status(self.team), f"IDC {t.idc.upper()}"]
            if self.screen == "revoke" and self._extends(self.team):
                lines += ["", f"A NEW CODE IS ISSUED AND {self.team} STANDS DOWN FOR {STAND_DOWN // HOUR} HOURS."]
            elif self.screen == "revoke" and stands_down(t):
                lines += ["", f"A NEW CODE IS ISSUED; {self.team} STAYS OUT AS BEFORE."]
            elif self.screen == "revoke":
                lines += ["", f"A NEW CODE IS ISSUED; {self.team} DOES NOT STAND DOWN."]
            return lines
        if self.screen not in ("world", "note", "team_pick", "type_pick") or not self.world_id:
            return []
        w = self.c.worlds[self.world_id]
        lines = [f"{w.id} · {w.status.upper()} · {w.glyph_text}"]
        lines += [f"{k.upper()}: {v}" for k, v in w.seen.items()]
        return lines
