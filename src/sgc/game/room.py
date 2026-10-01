"""The briefing room's controls: the dialing list with actions on each address, the roster (commissioning
and training), the standing orders, the pace, requisitions and retiring. A pure controller: it reads the campaign and calls the engine; screens.py draws it."""
from __future__ import annotations

from typing import Callable

from . import economy, orders, roster
from .clock import DAY, HOUR, PACE_NAMES, short
from .economy import PRICES, UPGRADES, cost_text
from .engine import PLANNABLE, Engine, team_label
from .orders import SITUATIONS
from .rules import STAND_DOWN, stands_down
from .scoring import score
from .state import RESERVE_MAX, SPECIALTIES, available_teams, rank, team_names

Handler = Callable[[], "str | None"]

MAIN = ("DIALING LIST", "TEAMS", "STANDING ORDERS", "PACE", "REQUISITIONS", "RETIRE FROM COMMAND",
        "BACK TO THE GATE ROOM")
MAIN_SCREENS = {"DIALING LIST": "worlds", "TEAMS": "teams", "STANDING ORDERS": "orders", "PACE": "pace",
                "REQUISITIONS": "requisitions", "RETIRE FROM COMMAND": "retire"}
TITLES = {"main": "BRIEFING ROOM", "worlds": "DIALING LIST", "world": "ADDRESS", "team_pick": "ASSIGN A TEAM",
          "type_pick": "MISSION TYPE", "note": "ADD A NOTE", "teams": "TEAMS", "team": "TEAM",
          "orders": "STANDING ORDERS", "pace": "PACE", "revoke": "REVOKE THE IDC?",
          "requisitions": "REQUISITIONS", "commission": "COMMISSION A TEAM", "train": "TRAINING",
          "retire": "RETIRE FROM COMMAND?"}
PARENT = {"worlds": "main", "world": "worlds", "team_pick": "world", "type_pick": "team_pick", "note": "world",
          "teams": "main", "team": "teams", "orders": "main", "pace": "main", "revoke": "team",
          "requisitions": "main", "commission": "teams", "train": "team", "retire": "main"}
DRONE_ROWS = 4                   # requisitions: two purchases and two reserves, then the upgrades
BUY_TEXT = "Bought now, from funding."
RESERVE_TEXT = "Topped up at midnight from funding, always keeping 100 in hand."
CANCEL_KEYS = ("ctrl-c", "escape")
PACE_LOCKED = "SET IN CONFIG (game_pace)"
# Short situation names for the 38-column panel; the current order goes on the line below.
SHORT = {"unknown_idc": "Unknown or no IDC", "hostiles_following": "Our IDC, hostiles following",
         "bad_idc": "Compromised or revoked IDC", "object": "Object through the gate",
         "missed_checkin": "Missed check-in", "under_fire": "Team under fire",
         "contact_offer": "Contact or trade offer"}
# Part 8: w.seen's long-form readings, down to the dialing list's short words. Only w.seen is ever read here,
# never a world's hidden traits.
ENV_WORDS = {"breathable atmosphere": "AIR OK", "toxic atmosphere": "TOXIC", "high radiation": "RADIATION",
             "extreme temperatures": "EXTREME", "no lock": "NO LOCK"}
LIFE_WORDS = {"none detected": "NO LIFE"}                 # anything else known (not "inconclusive") is LIFE SIGNS
SETTLEMENT_WORDS = {"no settlements": "NO LIFE", "settlement": "HUMANS", "Unas": "UNAS",
                    "Jaffa garrison": "JAFFA", "Goa'uld stronghold": "GOAULD", "outpost": "ALLY"}
FEATURE_WORDS = {"ruins": "RUINS", "energy readings": "TECH", "naquadah traces": "NAQ"}
DETAIL_ROWS = 5                  # the dialing list's address summary: at most this many lines


class Room:
    def __init__(self, engine: Engine):
        self.e = engine
        self.screen, self.sel = "main", 0
        self.world_id: str | None = None
        self.team: str | None = None
        self.note = ""
        self.notice = ""
        self._anchor: str | None = None   # Part 9: the screen back() closes from, for a hosted panel

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
        spec = t.specialty.upper() + (f"/{t.secondary.upper()}" if t.secondary else "")
        return f"{name} · {spec} · {rank(t).upper()}"

    def _status(self, name: str) -> str:
        """The roster's status line: where an offworld team is, or the status with its time left."""
        return team_label(self.c, name)

    def _worlds(self) -> list[str]:
        return list(self.c.worlds)

    def _teams(self) -> list[str]:
        return team_names(self.c)

    def _team_on(self, wid: str) -> str:
        """The team on a world (there or staging to go), joined if more than one; empty if none."""
        return " · ".join(name for name in self._teams()
                          if self.c.teams[name].where == wid and self.c.teams[name].status in ("offworld", "staging"))

    def _types(self) -> list[str]:
        return self.e.mission_types(self.world_id, self.team) if self.world_id and self.team else []

    def _world_table(self) -> list[tuple[str, bool, str, "Handler"]]:
        """The world screen's actions: (label, enabled, reason when disabled, handler). One table drives
        both the item list and the dispatch, so they can't drift apart."""
        c, w = self.c, self.c.worlds[self.world_id]
        teams_available, plannable = bool(available_teams(c)), w.status in PLANNABLE
        program = "uav_program" in c.upgrades
        bound = f"A {w.drone.upper()} IS ALREADY ON {w.name.upper()}" if w.drone else ""
        malp_ok, uav_ok = c.stock["malp"] > 0 and not w.drone, program and c.stock["uav"] > 0 and not w.drone
        no_malp = "NO MALPS LEFT" if c.stock["malp"] <= 0 else bound
        no_uav = "NEEDS THE UAV PROGRAM" if not program else "NO UAVS LEFT" if c.stock["uav"] <= 0 else bound
        return [
            (f"MALP PROBE ({c.stock['malp']} LEFT)", malp_ok, no_malp, lambda: self.e.probe(self.world_id)),
            (f"MALP EXTENDED REPORT ({c.stock['malp']} LEFT)", malp_ok, no_malp,
             lambda: self.e.probe(self.world_id, extended=True)),
            (f"UAV FLIGHT ({c.stock['uav']} LEFT)", uav_ok, no_uav, lambda: self.e.send_uav(self.world_id)),
            (f"UAV EXTENSIVE SURVEY ({c.stock['uav']} LEFT)", uav_ok, no_uav,
             lambda: self.e.send_uav(self.world_id, extended=True)),
            ("ASSIGN TEAM", teams_available and plannable,
             "PROBE IT FIRST" if not plannable else "NO TEAM AVAILABLE", self._start_assign),
            ("ADD NOTE", True, "", self._start_note),
        ]

    def _req_table(self) -> list[tuple[str, bool, str, "Handler"]]:
        """Requisitions: buy a drone, set a reserve, approve an upgrade. Like _world_table, one table drives
        the items, the reasons and the dispatch."""
        c = self.c
        rows: list[tuple[str, bool, str, Handler]] = []
        for drone in ("malp", "uav"):
            why = economy.reason(c, drone)
            rows.append((f"BUY A {drone.upper()} · {PRICES[drone]} ({c.stock[drone]} IN STORES)", why is None,
                         why or "", lambda d=drone: self.e.buy(d)))
        for drone in ("malp", "uav"):
            n = c.reserve[drone]
            rows.append((f"{drone.upper()} RESERVE: {n}", True, "",
                         lambda d=drone, n=n: self.e.set_reserve(d, (n + 1) % (RESERVE_MAX + 1))))
        for uid, u in UPGRADES.items():
            why = economy.reason(c, uid)
            rows.append((f"{u.title} · {'APPROVED' if uid in c.upgrades else cost_text(uid)}", why is None,
                         why or "", lambda uid=uid: self.e.buy(uid)))
        return rows

    def _type_label(self, mtype: str) -> str:
        """The mission, and the tandem task any team there does too: bringing home a drone or a UAV wreck."""
        target = self.e.mission_target(self.world_id, mtype) if mtype in ("rescue", "recover") else None
        if mtype == "rescue" and target:
            label = f"RESCUE {target}"
        elif mtype == "recover" and target:
            label = f"RECOVER THE {target.upper()}"
        else:
            label = mtype.upper()
        w = self.c.worlds[self.world_id]
        tandem = ([f"RECOVER {w.drone.upper()}"] if w.drone else []) + (["SALVAGE UAV WRECK"] if w.wreck else [])
        return " · ".join([label, *tandem])

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
                row = f"{w.name[:16]:<16} {w.status.upper()}"
                team = self._team_on(wid)
                if team:
                    row += f" · {team}"
                if w.drone:
                    row += f" · {w.drone.upper()}"
                elif w.wreck:
                    row += " · WRECK"
                out.append((row, True))
            return out + [("BACK", True)]
        if self.screen == "world":
            return [(label, ok) for label, ok, _, _ in self._world_table()] + [("BACK", True)]
        if self.screen == "team_pick":
            free = available_teams(c)
            return [(self._team_line(t), True) if t in free else (f"{self._team_line(t)}\n{self._status(t)}", False)
                    for t in self._teams()] + [("BACK", True)]
        if self.screen == "type_pick":
            return [(self._type_label(t), True) for t in self._types()] + [("BACK", True)]
        if self.screen == "teams":
            return ([(f"{self._team_line(t)}\n{self._status(t)}", True) for t in self._teams()]
                    + [(f"COMMISSION A NEW TEAM · {roster.COMMISSION}", roster.commission_reason(c) is None),
                       ("BACK", True)])
        if self.screen == "commission":
            return [(s.upper(), True) for s in SPECIALTIES] + [("BACK", True)]
        if self.screen == "team":
            return [("REVOKE AND REISSUE THE IDC", c.teams[self.team].status != "lost"),
                    (f"TRAIN A SECOND SPECIALTY · {roster.TRAINING}", roster.train_reason(c, self.team) is None),
                    ("BACK", True)]
        if self.screen == "train":
            return [(s.upper(), roster.train_reason(c, self.team, s) is None) for s in SPECIALTIES] + [("BACK", True)]
        if self.screen == "requisitions":
            return [(label, ok) for label, ok, _, _ in self._req_table()] + [("BACK", True)]
        if self.screen == "retire":
            return [("RETIRE AND FILE THE RECORD", True), ("BACK", True)]
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

    def open_on_world(self, world_id: str) -> None:
        """Part 9's entry point for the Database's ORDERS panel: position the room directly on this
        address's action screen, as the first step a hosted panel opens on. back() then closes instead
        of returning to the dialing list, which doesn't exist in that context."""
        self.world_id = world_id
        self._anchor = "world"
        self._go("world")

    def back(self) -> tuple | None:
        if self.screen == "main" or self.screen == self._anchor:
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
            return f"{self.team} IS LOST" if i == 0 else roster.train_reason(self.c, self.team) or ""
        if self.screen == "train":
            return roster.train_reason(self.c, self.team, SPECIALTIES[i]) or ""
        if self.screen == "teams":
            return roster.commission_reason(self.c) or ""
        if self.screen == "requisitions":
            return self._req_table()[i][2]
        if self.screen == "team_pick":
            team = self._teams()[i]
            t = self.c.teams[team]
            if t.status == "staging":
                w = self.c.worlds.get(t.where)
                return f"{team} IS STAGING FOR {(w.name if w is not None else t.where).upper()}"
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
            self._go(MAIN_SCREENS[label])
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
            if i < len(self._teams()):
                self.team = self._teams()[i]
                self._go("team")
            else:
                self._go("commission")
        elif self.screen == "team":
            self._go("revoke" if i == 0 else "train")
        elif self.screen == "commission":
            self.notice = self.e.commission(SPECIALTIES[i])     # _go keeps the notice
            self._go("teams")
        elif self.screen == "train":
            self.notice = self.e.train(self.team, SPECIALTIES[i])
            self._go("team")
        elif self.screen == "requisitions":
            self.notice = self._req_table()[i][3]() or ""
        elif self.screen == "retire":
            self.notice = self.e.retire()                       # the app sees the campaign end and takes over
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
        c = self.c
        if self.screen == "requisitions":
            nxt = min((e.due for e in c.events if e.kind == "funding_review"), default=None)
            lines = [f"FUNDING {c.funding} · NAQUADAH {c.naquadah}",
                     f"NEXT REVIEW {short(nxt)}" if nxt is not None else "NO REVIEW SCHEDULED"]
            lines += [f"LAST REVIEW {short(m)}: +{g}" for m, g, _ in c.reviews[-1:]]
            i = self.sel
            if i < 2:
                lines += ["", BUY_TEXT]
            elif i < DRONE_ROWS:
                lines += ["", RESERVE_TEXT]
            elif i < DRONE_ROWS + len(UPGRADES):
                lines += ["", list(UPGRADES.values())[i - DRONE_ROWS].text]
            return lines
        if self.screen == "commission":
            nxt = roster.next_number(c)
            return [f"FUNDING {c.funding}",
                    f"{nxt} FORMS IN {roster.FORMING // DAY} DAYS, GREEN." if nxt else "THE ROSTER IS FULL."]
        if self.screen == "train" and self.team:
            return [self._team_line(self.team), "HALF STRENGTH IN THE NEW SPECIALTY; OFF DUTY FOR A DAY."]
        if self.screen == "retire":
            return [f"SCORE SO FAR: {score(c)}", "THE CAMPAIGN ENDS HERE AND GOES INTO THE HALL OF RECORDS."]
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
        if self.screen == "worlds":
            worlds = self._worlds()
            if self.sel >= len(worlds):                  # BACK selected
                return []
            return self._world_brief(worlds[self.sel])
        if self.screen not in ("world", "note", "team_pick", "type_pick") or not self.world_id:
            return []
        w = self.c.worlds[self.world_id]
        lines = [f"{w.id} · {w.status.upper()} · {w.glyph_text}"]
        lines += [f"{k.upper()}: {v}" for k, v in w.seen.items()]
        return lines

    def _world_brief(self, wid: str) -> list[str]:
        """The dialing list's summary of the selected address (Part 8): name and status, who and what is
        there, known readings in short form, the last visit and anything queued, then a note. Every word
        comes from w.seen, the world's known status or the player's queue -- never a hidden trait."""
        w = self.c.worlds[wid]
        lines = [f"{w.name} · {w.status.upper()}", self._site_words(w)]
        readings = self._reading_words(w)
        if readings:
            lines.append(" · ".join(readings))
        visit = f"LAST VISIT {short(w.last_visit)}" if w.last_visit is not None else "NEVER VISITED"
        lines.append(" · ".join([visit, *self._queue_words(wid)]))
        if w.notes:
            lines.append(f"NOTE: {w.notes[-1][1].splitlines()[0]}")
        return lines[:DETAIL_ROWS]

    def _site_words(self, w) -> str:
        """Who and what is on a world: each team there with its status, a parked drone, a wreck; or
        NOBODY ON SITE."""
        parts = [f"{name} {self.c.teams[name].status.upper()}" for name in self._teams()
                if self.c.teams[name].where == w.id and self.c.teams[name].status in ("offworld", "staging")]
        if w.drone:
            parts.append(f"{w.drone.upper()} ON SITE")
        if w.wreck:
            parts.append("UAV WRECK")
        return " · ".join(parts) if parts else "NOBODY ON SITE"

    def _reading_words(self, w) -> list[str]:
        """Known readings in short form, from w.seen only. NOT YET PROBED for an unexplored address."""
        if w.status == "unexplored":
            return ["NOT YET PROBED"]
        seen = w.seen
        words = []
        if "env" in seen and seen["env"] in ENV_WORDS:
            words.append(ENV_WORDS[seen["env"]])
        life = self._life_word(seen)
        if life:
            words.append(life)
        if "features" in seen:
            words += [FEATURE_WORDS[f] for f in seen["features"].split(", ") if f in FEATURE_WORDS]
        return words

    def _life_word(self, seen: dict) -> str:
        """NO LIFE, LIFE SIGNS, or the UAV's settlement word (e.g. JAFFA, HUMANS) when known."""
        if "inhabitants" in seen:
            return SETTLEMENT_WORDS.get(seen["inhabitants"], "")
        life = seen.get("life")
        if life is None or life == "inconclusive":
            return ""
        return LIFE_WORDS.get(life, "LIFE SIGNS")

    def _queue_words(self, wid: str) -> list[str]:
        """Anything queued or due for this address, read from the engine's schedule_view -- the same
        source as the Queue, so the two never disagree."""
        out = []
        for item in self.e.schedule_view():
            if item.kind == "dial_out" and item.id in (f"dial:malp:{wid}", f"dial:uav:{wid}",
                                                        f"dial:uplink:{wid}"):
                out.append(f"{item.id.split(':')[1].upper()} QUEUED #{item.when}")
            elif item.kind == "uplink" and item.id == f"uplink:{wid}":
                out.append(f"UPLINK {item.status.removeprefix('EXPECTED ')}")
            elif item.kind == "drone_checkin" and item.id == f"checkin:{wid}":
                out.append(f"CHECK-IN {item.when}")
            elif item.kind == "mission":
                m = self.c.mission(int(item.id.split(":")[1]))
                if m is not None and m.world == wid:
                    out.append(item.brief)
        return out
