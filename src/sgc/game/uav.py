"""A UAV's camera feed as data: how the ground it flies over looks, and readouts that drift as the report plays.
It uses only what the player has been told, never a world's hidden traits."""
from __future__ import annotations

import math
import zlib

from ..model import Feed
from .world import World

TINT_BY_ENV = {"breathable atmosphere": ("forest", "ocean"), "toxic atmosphere": ("toxic", "toxic"),
               "high radiation": ("desert", "desert"), "extreme temperatures": ("ice", "volcanic")}


def seed(w: World) -> int:
    """The world's terrain seed: the same world always looks the same (never Python's salted hash)."""
    return zlib.crc32(w.id.encode())


def tint(w: World, seen: dict[str, str]) -> str:
    """The ground's colours, from the env reading in this report or in earlier intel; neutral if none."""
    pair = TINT_BY_ENV.get(seen.get("env") or w.seen.get("env", ""))
    return pair[seed(w) % 2] if pair else "neutral"


def contact(seen: dict[str, str]) -> bool:
    """The readings name a settlement or a structure, so the feed boxes a contact."""
    return seen.get("inhabitants", "no settlements") != "no settlements" or "features" in seen


def readouts(sd: int, p: float) -> tuple[int, int, int, int]:
    """(altitude in metres, heading, speed in km/h, fuel %), drifting slowly over the report."""
    ph = (sd % 628) / 100
    alt = int(round(1200 + 90 * math.sin(ph + p * 2.1), -1))
    hdg = (sd % 360 + round(14 * math.sin(ph * 1.3 + p * 1.7))) % 360
    speed = round(140 + 8 * math.sin(ph * 0.7 + p * 3.0))
    fuel = round(88 - 6 * p - sd % 9)
    return alt, hdg, speed, fuel


def hud(sd: int, p: float) -> str:
    alt, hdg, _, _ = readouts(sd, p)
    return f"UAV  ALT {alt}M  HDG {hdg:03d}"


def rows(sd: int, p: float) -> list[tuple[str, str]]:
    """The side panel's flight readouts."""
    alt, hdg, speed, fuel = readouts(sd, p)
    return [("ALT", f"{alt} M"), ("HDG", f"{hdg:03d}"), ("SPEED", f"{speed} KM/H"), ("FUEL", f"{fuel} %")]


def make(w: World, seen: dict[str, str], p: float, lost: float = 0.0) -> Feed:
    sd = seed(w)
    return Feed(sd, tint(w, seen), p, lost, hud(sd, p), contact(seen))
