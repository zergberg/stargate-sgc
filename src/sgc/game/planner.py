"""The auto-planner: what a hands-off commander does. It probes the next unexplored address and sends each
free team to the most promising probed world. With nothing new to do, a free team re-surveys the known world
visited longest ago, since every debrief can turn up a new address. The idle simulation uses it; so could a
future 'autopilot'."""
from __future__ import annotations

from . import clock
from .engine import Engine
from .state import available_teams
from .world import World

_ENV_SCORE = {"breathable atmosphere": 2}
_LIFE_SCORE = {"none detected": 1, "humanoid life signs": 1, "armed humanoids": -3}
COOLDOWN = 3 * clock.DAY            # a world whose mission was aborted is left alone this long
RESURVEY = ("surveyed", "contact")


def score(w: World) -> int:
    """How attractive a probed world looks, from what the SGC has seen of it (never the hidden traits)."""
    return _ENV_SCORE.get(w.seen.get("env", ""), 0) + _LIFE_SCORE.get(w.seen.get("life", ""), 0)


def _avoid(e: Engine) -> set[str]:
    """Worlds a team is on now, worlds where a team was captured or lost, and, for a while, worlds where a
    mission was aborted."""
    now = e.c.now
    return {m.world for m in e.c.missions if m.state in ("active", "captured", "lost")
            or (m.state == "aborted" and now < m.end + COOLDOWN)}


def step(e: Engine) -> list[str]:
    """One round of orders; returns the engine's replies."""
    c, out = e.c, []
    busy = {ev.data.get("world") for ev in c.events if ev.kind in ("dial_out", "malp_return")}
    if c.stock["malp"] > 0:
        nxt = next((w for w in c.worlds.values() if w.status == "unexplored" and w.id not in busy and not w.drone),
                   None)
        if nxt is not None:
            out.append(e.probe(nxt.id))
    for team in available_teams(c):
        taken = _avoid(e)
        options, fallback = [], []
        for w in c.worlds.values():
            if w.id in taken or w.status == "hostile":
                continue
            types = e.mission_types(w.id, team)
            if w.status == "probed":
                options.append((score(w) + 10, w, "survey"))
            elif "contact" in types and w.status == "surveyed":
                options.append((score(w) + 5, w, "contact"))
            if w.status in RESURVEY and "survey" in types:
                fallback.append((-(w.last_visit or 0), w, "survey"))
        pick = options or fallback
        if pick:
            _, w, mtype = max(pick, key=lambda o: o[0])
            out.append(e.assign(w.id, team, mtype))
    return out
