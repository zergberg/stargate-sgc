"""The auto-planner: what a hands-off commander does. It buys upgrades and teams while keeping funding in hand,
probes the next unexplored address, and sends each free team to the most promising work: rescues first, then
the worlds of active arcs, then trade, aid, study and mining, then surveys and contact. With nothing new to do, a
free team re-surveys the known world visited longest ago. The idle simulation uses it; so could an autopilot."""
from __future__ import annotations

from . import arcs, clock, economy, roster
from .engine import Engine
from .state import available_teams
from .world import World

_ENV_SCORE = {"breathable atmosphere": 2}
_LIFE_SCORE = {"none detected": 1, "humanoid life signs": 1, "armed humanoids": -3}
COOLDOWN = 3 * clock.DAY            # a world whose mission was aborted is left alone this long
RESURVEY = ("surveyed", "contact")
KEEP = 150                          # funding the planner never spends
MAX_TEAMS = 8
UPGRADE_ORDER = ("uav_program", "security_detail", "database_analysts", "iris_reinforcement", "naquadah_generator")
SPECIALTY_ORDER = ("diplomatic", "combat", "medical", "recon", "science")
TYPE_SCORE = {"rescue": 50, "recover": 15, "aid": 10, "study": 9, "mine": 8}
ARC_BONUS = 30                      # an arc world's own work outranks everything but a rescue
ARC_WORK = ("raid", "study", "contact")     # the first of these an arc world offers is its own work


def score(w: World) -> int:
    """How attractive a probed world looks, from what the SGC has seen of it (never the hidden traits)."""
    return _ENV_SCORE.get(w.seen.get("env", ""), 0) + _LIFE_SCORE.get(w.seen.get("life", ""), 0)


def _avoid(e: Engine, arc_worlds: set[str]) -> set[str]:
    """Worlds a team is on now, worlds where a team was captured or lost, and, for a while, worlds where a
    mission was aborted. An active arc's world is avoided only while a team is on it: the arc won't wait."""
    now = e.c.now
    return {m.world for m in e.c.missions if m.state == "active"
            or (m.world not in arc_worlds and (m.state in ("captured", "lost")
                                               or (m.state == "aborted" and now < m.end + COOLDOWN)))}


def _arc_worlds(e: Engine) -> set[str]:
    """The dialable worlds of active arcs."""
    out = set()
    for aid, st in e.c.arcs.items():
        w = arcs.arc_world(e.c, aid)
        if st.state == "active" and w is not None and w.id in e.c.worlds:
            out.add(w.id)
    return out


def _spend(e: Engine) -> list[str]:
    """At most one purchase: the next upgrade, else a new team, never dipping below KEEP."""
    c = e.c
    for uid in UPGRADE_ORDER:
        u = economy.UPGRADES[uid]
        if uid not in c.upgrades and economy.reason(c, uid) is None and c.funding - u.cost >= KEEP:
            return [e.buy(uid)]
    if len(c.teams) < MAX_TEAMS and c.funding >= roster.COMMISSION + KEEP + 100 and roster.next_number(c):
        spec = SPECIALTY_ORDER[(len(c.teams) - 4) % len(SPECIALTY_ORDER)]
        return [e.commission(spec)]
    return []


def _arc_work(w: World) -> str:
    """The work an arc world wants: a raid if it offers one, else study, else contact, else a survey."""
    return next((t for t in ARC_WORK if t in w.options), "survey")


def _option(e: Engine, w: World, team: str, arc_worlds: set[str]) -> tuple[int, str] | None:
    """The best (score, mission type) for this team on this world, or None."""
    c = e.c
    work = _arc_work(w) if w.id in arc_worlds else None
    best: tuple[int, str] | None = None
    for t in e.mission_types(w.id, team):
        if t == "raid" and t != work:
            continue
        if w.status == "hostile" and t not in ("rescue", work):
            continue
        if t in TYPE_SCORE:
            if t == "mine" and c.naquadah >= 15:
                continue
            s = TYPE_SCORE[t]
        elif t == "trade":
            s = 12 if c.factions["locals"].trust >= 20 else 6
        elif t == "survey" and w.status == "probed":
            s = 10 + score(w)
        elif t == "contact" and w.status == "surveyed":
            s = 5 + score(w)
        elif t == work:
            s = 0
        else:
            continue
        if t == work:
            s += ARC_BONUS
        if best is None or s > best[0]:
            best = (s, t)
    return best


def step(e: Engine) -> list[str]:
    """One round of orders; returns the engine's replies."""
    c, out = e.c, []
    out += _spend(e)
    busy = {ev.data.get("world") for ev in c.events if ev.kind in ("dial_out", "malp_return")}
    if c.stock["malp"] > 0:
        nxt = next((w for w in c.worlds.values() if w.status == "unexplored" and w.id not in busy and not w.drone),
                   None)
        if nxt is not None:
            out.append(e.probe(nxt.id))
    arc_worlds = _arc_worlds(e)
    for team in available_teams(c):
        taken = _avoid(e, arc_worlds)
        options, fallback = [], []
        for w in c.worlds.values():
            if w.status in ("unexplored", "lost"):
                continue
            opt = _option(e, w, team, arc_worlds)
            if opt is not None and (w.id not in taken or opt[1] == "rescue"):
                options.append((opt[0], w.id, w, opt[1]))
            if w.id not in taken and w.status in RESURVEY and "survey" in e.mission_types(w.id, team):
                fallback.append((-(w.last_visit or 0), w.id, w, "survey"))
        pick = options or fallback
        if pick:
            _, _, w, mtype = max(pick, key=lambda o: (o[0], o[1]))
            out.append(e.assign(w.id, team, mtype))
    return out
