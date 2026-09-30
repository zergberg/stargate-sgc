"""Funding, naquadah, drone purchases, upgrades, requisitions and the weekly funding review."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .clock import START
from .state import DRONES, RESERVE_MAX, REVIEW_EVERY, STOCK, UPGRADE_IDS, Campaign

PRICES = {"malp": 20, "uav": 60}
FLOOR = 100                     # a requisition never spends the last of this
GRANT = 300                     # a funding review's base grant
MIN_GRANT = 50
SCALE = {"recruit": 1.25, "officer": 1.0, "commander": 0.8}
PERF = {"intel": 10, "tech": 25, "allies": 40, "missions": 5, "arcs": 60, "lost": -40, "captured": -20,
        "breaches": -15, "incidents": -30}
CAPS = {"intel": 100, "missions": 60}
LOG_WIDTH = 66                  # the log box's width: longer lines are clipped
PERF_WORDS = {"intel": "INTEL", "tech": "TECH", "allies": "ALLIES", "missions": "MISSIONS", "arcs": "ARCS",
              "lost": "TEAMS LOST", "captured": "CAPTURED", "breaches": "BREACHES", "incidents": "INCIDENTS"}


@dataclass(frozen=True)
class Upgrade:
    id: str
    title: str
    cost: int
    naquadah: int
    text: str


UPGRADES: dict[str, Upgrade] = {u.id: u for u in (
    Upgrade("uav_program", "UAV PROGRAM", 150, 0, "Unlocks UAV flights. One UAV comes with it."),
    Upgrade("security_detail", "SECURITY DETAIL", 120, 0, "Security recovers twice as fast."),
    Upgrade("iris_reinforcement", "IRIS REINFORCEMENT", 100, 5, "Cuts damage from impacts and breaches by a quarter."),
    Upgrade("database_analysts", "DATABASE ANALYSTS", 150, 0, "One more intel roll in every debrief."),
    Upgrade("infirmary", "INFIRMARY", 200, 0, "Unlocks the infirmary. It opens in a later build."),
    Upgrade("research_lab", "RESEARCH LAB", 200, 0, "Unlocks the research lab. It opens in a later build."),
    Upgrade("naquadah_generator", "NAQUADAH GENERATOR", 0, 10, "The iris and defenses hold longer."),
)}
assert set(UPGRADES) == set(UPGRADE_IDS)


def cost_text(uid: str) -> str:
    """'150', '100 + 5 NQ', '10 NQ'."""
    u = UPGRADES[uid]
    parts = ([str(u.cost)] if u.cost else []) + ([f"{u.naquadah} NQ"] if u.naquadah else [])
    return " + ".join(parts)


def note(c: Campaign, key: str, n: int = 1) -> None:
    """Count something for the next funding review."""
    c.ledger[key] += n


def reason(c: Campaign, item: str) -> str | None:
    """Why a drone ("malp", "uav") or an upgrade can't be bought right now, or None."""
    if item in PRICES:
        if item == "uav" and "uav_program" not in c.upgrades:
            return "NEEDS THE UAV PROGRAM"
        if c.stock[item] >= STOCK[item][1]:
            return f"STORES FULL ({STOCK[item][1]})"
        if c.funding < PRICES[item]:
            return f"NOT ENOUGH FUNDING ({PRICES[item]})"
        return None
    u = UPGRADES[item]
    if item in c.upgrades:
        return "ALREADY APPROVED"
    if c.funding < u.cost:
        return f"NOT ENOUGH FUNDING ({u.cost})"
    if c.naquadah < u.naquadah:
        return f"NOT ENOUGH NAQUADAH ({u.naquadah})"
    return None


def buy(c: Campaign, item: str) -> str:
    """Buy a drone or an upgrade; the reply says what happened, or why not."""
    why = reason(c, item)
    if why:
        return why
    if item in PRICES:
        c.funding -= PRICES[item]
        c.stock[item] += 1
        return f"{item.upper()} PURCHASED — {c.stock[item]} IN STORES, FUNDING {c.funding}"
    u = UPGRADES[item]
    c.funding -= u.cost
    c.naquadah -= u.naquadah
    c.upgrades.add(item)
    if item == "uav_program":
        c.stock["uav"] = min(STOCK["uav"][1], c.stock["uav"] + 1)
    if item == "naquadah_generator":
        c.inventory.add("tech.naquadah_generator")
    return f"{u.title} APPROVED"


def set_reserve(c: Campaign, drone: str, n: int) -> str:
    c.reserve[drone] = max(0, min(RESERVE_MAX, n))
    return f"{drone.upper()} RESERVE: {c.reserve[drone]}"


def requisition(c: Campaign) -> list[str]:
    """Midnight: buy drones up to the reserve, never spending the last FLOOR of funding."""
    out = []
    for drone in DRONES:
        n = 0
        while (c.stock[drone] < c.reserve[drone] and reason(c, drone) is None
               and c.funding - PRICES[drone] >= FLOOR):
            c.funding -= PRICES[drone]
            c.stock[drone] += 1
            n += 1
        if n:
            out.append(f"REQUISITION: {n} {drone.upper()}{'S' if n > 1 else ''} ({n * PRICES[drone]})")
    return out


def defense(c: Campaign) -> float:
    """The share of Security damage that gets through: iris reinforcement and the generator each take a quarter."""
    f = 1.0
    if "iris_reinforcement" in c.upgrades:
        f *= 0.75
    if "naquadah_generator" in c.upgrades:
        f *= 0.75
    return f


def performance(c: Campaign) -> tuple[int, list[str]]:
    total, parts = 0, []
    for key, per in PERF.items():
        n = c.ledger[key]
        if not n:
            continue
        amount = min(n * per, CAPS[key]) if key in CAPS else n * per
        total += amount
        parts.append(f"{PERF_WORDS[key]} {amount:+d}")
    return total, parts


def review(c: Campaign) -> list[str]:
    """The weekly funding review: base grant plus performance, scaled by difficulty; the next one is scheduled."""
    perf, parts = performance(c)
    scale = SCALE[c.difficulty]
    # Half-up rounding on purpose (412.5 -> 413), not Python's round(), which rounds halves to even.
    grant = max(MIN_GRANT, math.floor((GRANT + perf) * scale + 0.5))
    c.funding += grant
    items = [f"BASE {GRANT}", *parts, *([f"×{scale:g}"] if scale != 1 else [])]
    summary = " · ".join(items)
    c.reviews = [*c.reviews, (c.now, grant, summary)][-3:]
    c.ledger = dict.fromkeys(c.ledger, 0)
    c.events.push(next_review(c.now), "funding_review")
    return [f"FUNDING REVIEW: +{grant} — FUNDING {c.funding}", *_wrap(items)]


def next_review(now: int) -> int:
    """The first review after now on the fixed grid START + k × REVIEW_EVERY (day 8 at 08:00, day 15, ...). The
    same grid state.new_campaign and the save migration use; engine.py has no grid of its own."""
    return START + (max(0, now - START) // REVIEW_EVERY + 1) * REVIEW_EVERY


def _wrap(items: list[str]) -> list[str]:
    """The review's items joined by ' · ' into log lines of at most LOG_WIDTH characters."""
    lines: list[str] = []
    for item in items:
        if lines and len(lines[-1]) + 3 + len(item) <= LOG_WIDTH:
            lines[-1] += " · " + item
        else:
            lines.append(item)
    return lines
