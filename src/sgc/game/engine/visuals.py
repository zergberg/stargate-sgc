"""The visual builders: the gate scenes queued onto the director for every event."""
from __future__ import annotations

import math
import random

from ... import sequences as sq
from ...events import REGISTRY, EventContext
from ...events.common import cleanup, start_outgoing
from ...model import Figure, Scene, Step
from .. import uav
from ..world import World

MALP_FEED_S = 20.0                   # real seconds the side panel takes to fill with a MALP's readings
UAV_FEED_S = 90.0                    # real seconds of a UAV's aerial feed
UPLINK_FEED_S = 10.0                 # real seconds an uplink's extended data takes to fill the side panel
UAV_RAIL = 0.12                      # the UAV's launch rail stands here, at the foot of the ramp
UAV_CRUISE = 0.8                     # the UAV's altitude as it reaches the horizon (0..1)
LANES = (-0.45, -0.15, 0.15, 0.45)
CHECKIN_FEED_S = 10.0                # real seconds a routine drone check-in's panel takes to read NOMINAL


def _shown(n: int, p: float) -> int:
    """How many of n rows a feed has sent back at progress p: one at a time, the last just before the end."""
    return min(n, math.floor(p * (n + 1)))


class VisualsMixin:
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
            return v_departure(self.d.scene, w, bind.get("team", "SG TEAM")) if w else []
        if name == "iris_hold":
            return iris_hold(self.c.now)
        if name == "arrival":
            return arrival(3, "TRAVELLERS ARRIVING")
        if name == "team_return":
            return v_team_return(self.d.scene, bind.get("team", "SG TEAM"))
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


def outgoing(scene: Scene, w: World) -> list[Step]:
    dial, _ = sq.dial(w.address(), scene.ring_angle)
    return [start_outgoing(w.address()), *dial, *sq.kawoosh()]


def v_drone(scene: Scene | None, w: World, drone: str) -> list[Step]:
    """A drone through the gate and the gate shut behind it (a search MALP; a probe plays v_probe)."""
    if scene is None:
        return []
    if drone == "uav":
        return [*outgoing(scene, w), *uav_launch(), *sq.shutdown(), cleanup()]

    def roll(s, p):
        s.figures = [Figure("malp", p, 0.0)] if p < 1 else []
    return [*outgoing(scene, w), Step(5.0, roll, f"{drone.upper()} IN TRANSIT"), *sq.shutdown(), cleanup()]


def v_recall(scene: Scene | None, w: World, drone: str) -> list[Step]:
    """Legacy: an older save's recall, the drone coming home through the gate."""
    if scene is None:
        return []
    if drone == "uav":
        return [*outgoing(scene, w), *uav_home(), *sq.shutdown(), cleanup()]

    def roll(s, p):
        s.figures = [Figure("malp", 1 - p, 0.0)] if p < 1 else []
    return [*outgoing(scene, w), Step(5.0, roll, "MALP RETURNING THROUGH THE GATE"), *sq.shutdown(), cleanup()]


def v_telemetry(scene: Scene | None, w: World, seen: dict[str, str], drone: str = "malp") -> list[Step]:
    if scene is None:
        return []
    if drone == "uav":
        def feed(s, p):
            s.feed = uav.make(w, seen, p) if p < 1 else None
            s.panel_title = f"TELEMETRY · {w.name.upper()}"
            s.panel_rows = [*uav.rows(uav.seed(w), p), *((k.upper(), v) for k, v in seen.items())]
        return [*outgoing(scene, w), Step(6.0, feed, "TELEMETRY RECEIVED"), *sq.shutdown(), cleanup()]

    def show(s, p):
        s.panel_title = f"TELEMETRY · {w.name.upper()}"
        s.panel_rows = [(k.upper(), v) for k, v in seen.items()]
    return [*outgoing(scene, w), Step(6.0, show, "TELEMETRY RECEIVED"), *sq.shutdown(), cleanup()]


def v_probe(scene: Scene | None, now: int, w: World, drone: str, seen: dict[str, str], fate: str) -> list[Step]:
    """A live probe: out through the gate, its readings streaming back a row at a time while the gate stays
    open, then shutdown. A drone that's lost stops partway (the visual RNG picks where) on what the report
    keeps of it, and SIGNAL LOST."""
    if scene is None:
        return []
    if fate == "ok":
        got, cut = dict(seen), 1.0
    else:
        got = {"env": seen["env"]} if fate == "destroyed" else {}
        cut = random.Random(now).uniform(0.2, 0.7)
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
        steps = [*outgoing(scene, w), *uav_launch(), Step(UAV_FEED_S * cut, feed, "TELEMETRY RECEIVED")]
        if fate != "ok":
            steps += [Step(1.5, static, "UAV SIGNAL LOST"), sq.hold(1.0)]
        return [*steps, *sq.shutdown(), cleanup()]

    def roll(s, p):
        s.figures = [Figure("malp", p, 0.0)] if p < 1 else []

    def show(s, p):
        s.panel_title = title
        s.panel_rows = [*upto(p), *([("SIGNAL", "LOST")] if fate != "ok" and p >= 1 else [])]
    steps = [*outgoing(scene, w), Step(5.0, roll, "MALP IN TRANSIT"),
             Step(MALP_FEED_S * cut, show, "TELEMETRY RECEIVED")]
    if fate != "ok":
        steps.append(sq.hold(1.0))
    return [*steps, *sq.shutdown(), cleanup()]


def v_uplink(scene: Scene | None, w: World, outcome: str, seen: dict[str, str]) -> list[Step]:
    """The uplink: the SGC dials the drone, and the side panel fills with its extended data. Garbled data
    says so; a drone that's gone answers with no carrier."""
    if scene is None:
        return []
    rows = [(k.upper(), v) for k, v in seen.items()] if outcome == "full" else \
        [("DATA", "GARBLED")] if outcome == "partial" else []

    def show(s, p):
        s.panel_title = f"UPLINK · {w.name.upper()}"
        s.panel_rows = rows[:_shown(len(rows), p)] if rows else [("SIGNAL", "LOST")]
    if not rows:
        return [*outgoing(scene, w), Step(3.0, show, "NO CARRIER"), *sq.shutdown(), cleanup()]
    return [*outgoing(scene, w), Step(UPLINK_FEED_S, show, "EXTENDED DATA RECEIVED"), *sq.shutdown(), cleanup()]


def v_drone_checkin(scene: Scene | None, w: World, drone: str) -> list[Step]:
    """A parked drone's routine check-in: its known readings play back, then ALL READINGS NOMINAL."""
    if scene is None:
        return []
    rows = [(k.upper(), v) for k, v in w.seen.items()]
    title = f"{drone.upper()} CHECK-IN · {w.name.upper()}"

    def show(s, p):
        s.panel_title = title
        s.panel_rows = rows[:_shown(len(rows), p)] if p < 1 else [*rows, ("ALL READINGS", "NOMINAL")]
    return [*outgoing(scene, w), Step(CHECKIN_FEED_S, show, "ALL READINGS NOMINAL"), *sq.shutdown(), cleanup()]


def v_drone_home(scene: Scene | None, w: World, team: str, drone: str) -> list[Step]:
    """A parked drone goes home right behind the team: an incoming wormhole, IDC accepted, the drone coming
    through (the UAV's landing, or the MALP rolling in — the same visuals a recall once played, reversed),
    then shutdown."""
    if scene is None:
        return []

    def idc(s, p):
        s.alert, s.identified, s.status = "normal", True, f"IDC: {team}"
    if drone == "uav":
        through = uav_home()
    else:
        def roll(s, p):
            s.figures = [Figure("malp", 1 - p, 0.0)] if p < 1 else []
        through = [Step(5.0, roll, f"{drone.upper()} COMING HOME")]
    return [*sq.incoming(7), *sq.kawoosh(),
            Step(0, idc, f"IDC RECEIVED — {team}", ("idc_accept", "stop:klaxon")),
            *through, *sq.shutdown(), cleanup()]


def uav_launch() -> list[Step]:
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


def uav_home() -> list[Step]:
    """The UAV comes out of the horizon nose first, descends toward us, lands and rolls out. 5 seconds."""
    def descend(s, p):
        s.figures = [Figure("uav", 0.95 - 0.9 * p, alt=UAV_CRUISE * (1 - p * p), facing="toward")]
        s.splashes = [[0.0, 0.14, p / 0.2]] if p < 0.2 else []

    def roll_out(s, p):                  # it rolls on toward us and off the bottom of the picture
        s.figures = [] if p >= 1 else [Figure("uav", 0.05 - 0.97 * p, facing="toward")]
    return [Step(4.2, descend, "UAV RETURNING THROUGH THE GATE"), Step(0.8, roll_out, "UAV RECOVERED")]


def v_signal_lost(scene: Scene | None, w: World, seen: dict[str, str]) -> list[Step]:
    """A UAV shot down or captured: its feed goes to static, SIGNAL LOST flashes, the wormhole disengages."""
    if scene is None:
        return []

    def live(s, p):
        s.feed = uav.make(w, seen, 0.3 * p)
        s.panel_title = f"TELEMETRY · {w.name.upper()}"
        s.panel_rows = uav.rows(uav.seed(w), 0.3 * p)

    def static(s, p):
        s.feed = uav.make(w, seen, 0.3, lost=p)
        s.panel_rows = [("SIGNAL", "LOST")]
    return [*outgoing(scene, w), Step(1.0, live), Step(1.5, static, "UAV SIGNAL LOST"), sq.hold(1.0),
            *sq.shutdown(), cleanup()]


def v_checkin(scene: Scene | None, team: str, keep_open: bool = False) -> list[Step]:
    """A routine check-in shuts down as always. One that's about to raise a prompt (keep_open) leaves the
    wormhole up: the line stays open until the player answers or it times out (_checkin_timeout), and
    either way it's `_visual("close")`, reused from the scenario's own resolution, that shuts it."""
    if scene is None:
        return []

    def idc(s, p):
        s.alert, s.identified, s.status = "normal", True, f"IDC: {team}"
    steps = [*sq.incoming(7), *sq.kawoosh(), Step(0, idc, f"IDC RECEIVED — {team}", ("idc_accept", "stop:klaxon")),
             sq.hold(3.0, log=f"{team} CHECKING IN")]
    if keep_open:
        return steps
    return [*steps, *sq.shutdown(), cleanup()]


def v_team_return(scene: Scene | None, team: str) -> list[Step]:
    if scene is None:
        return []

    def idc(s, p):
        s.alert, s.identified, s.status = "normal", True, f"IDC: {team}"
    return [*sq.incoming(7), *sq.kawoosh(),
            Step(0, idc, f"IDC RECEIVED — {team}", ("idc_accept", "stop:klaxon")),
            *arrival(4, f"{team} COMING HOME"), *sq.shutdown(), cleanup()]


def v_delivery(scene: Scene | None, w: World) -> list[Step]:
    """A trade delivery: a friendly wormhole, the iris opens, a crate rolls down the ramp."""
    if scene is None:
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


def v_departure(scene: Scene | None, w: World, team: str) -> list[Step]:
    if scene is None:
        return []

    def through(s, p):
        t = p * 5.0
        s.figures = [Figure("person", min(1.0, (t - i * 0.6) / 3.0), lane) for i, lane in enumerate(LANES)
                     if 0 <= t - i * 0.6 < 3.0]
        if p >= 1:
            s.figures = []
    return [*outgoing(scene, w), Step(5.0, through, f"{team} STEPPING THROUGH"), *sq.shutdown(), cleanup()]


def iris_hold(now: int, seconds: float = 6.0) -> list[Step]:
    rng = random.Random(now)
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


def arrival(n: int, log: str) -> list[Step]:
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
