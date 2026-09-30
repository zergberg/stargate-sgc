"""Worlds: hidden traits, what the SGC knows, names learned from intel, designations, the cartouche list."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from functools import cache

from ..addresses import Address, load_canon

ENVIRONMENTS = ("normal", "toxic", "radiation", "extreme", "no_lock")
INHABITANTS = ("none", "human", "unas", "jaffa", "goauld", "ally")
FEATURES = ("ruins", "technology", "naquadah")
STATUSES = ("unexplored", "probed", "surveyed", "contact", "hostile", "lost")
PROGRESS = ("unexplored", "probed", "surveyed", "contact")
DRONES = ("malp", "uav")
WRECKS = ("crashed", "shot_down")          # how a UAV wreck came down
NAME_SOURCES = ("locals", "ruins", "jaffa", "goauld", "comms", "allies", "records")
SOURCE_TEXT = {"locals": "the locals", "ruins": "inscriptions in the ruins", "jaffa": "the Jaffa",
               "goauld": "a Goa'uld database", "comms": "a UAV comms intercept", "allies": "allied intelligence",
               "records": "SGC records"}
GOAULD = ("Apophis", "Heru'ur", "Sokar", "Cronus", "Ba'al", "Yu", "Nirrti", "Svarog", "Olokun", "Bastet")
GOAULD_IDS = {"Apophis": "apophis", "Heru'ur": "heruur", "Sokar": "sokar", "Cronus": "cronus", "Ba'al": "baal",
              "Yu": "yu", "Nirrti": "nirrti", "Svarog": "svarog", "Olokun": "olokun", "Bastet": "bastet"}
ALLY_NAMES = {"tokra": "the Tok'ra", "asgard": "the Asgard", "tollan": "the Tollan", "nox": "the Nox",
              "jaffa": "the Free Jaffa", "locals": "local peoples"}
FACTION_IDS = (*GOAULD_IDS.values(), *ALLY_NAMES)
FACTION_KIND = {**{f: "goauld" for f in GOAULD_IDS.values()}, **{f: "ally" for f in ALLY_NAMES}}
_GOAULD_NAMES = {fid: name for name, fid in GOAULD_IDS.items()}
CARTOUCHE = "the Abydos cartouche"
CARTOUCHE_SIZE = 20
_LETTERS = "XCJMRWYAB"
_SYLLABLES = ("ka", "tor", "nel", "ab", "ys", "shar", "mi", "dos", "ren", "tal", "vek", "ul", "an", "ce", "ri",
              "khe", "em", "sa", "hol", "ta", "ra", "nu", "bel", "kor")
_DANGER = {"none": 0, "human": 0, "ally": 0, "unas": 2, "jaffa": 2, "goauld": 3}

# Canon worlds: environment, inhabitants, features, owner, and the names intel can reveal (by source).
CANON = {
    "Abydos": ("normal", "human", ("ruins",), None, {"locals": "Abydos"}),
    "Chulak": ("normal", "jaffa", ("ruins",), "Apophis", {"locals": "Chulak", "jaffa": "Chulak"}),
    "Cimmeria": ("normal", "human", ("ruins", "technology"), None, {"locals": "Cimmeria"}),
    "Kheb": ("normal", "none", ("ruins",), None, {"locals": "Kheb", "jaffa": "Kheb"}),
    "K'tau": ("normal", "human", ("technology",), None, {"locals": "K'tau"}),
    "Langara": ("normal", "human", ("naquadah",), None, {"locals": "Langara", "comms": "Kelowna"}),
    "Tollana": ("normal", "ally", ("technology",), None, {"locals": "Tollana", "allies": "Tollana"}),
    "Juna": ("normal", "human", (), None, {"locals": "Juna"}),
}
CAMPAIGN_CANON = ("Chulak", "Cimmeria", "Kheb", "K'tau", "Langara", "Tollana", "Juna")
# Worlds the arcs need that data/addresses.json doesn't have: glyphs, environment, inhabitants, features,
# owner and hidden names. The glyphs are placeholders until the SG-1 content pack supplies canon ones. Their
# designations are P1 (arc_designation), which designation() never gives, so no other world can already hold one.
ARC_WORLDS = {
    "Vorash": ((11, 27, 3, 19, 36, 8), "normal", "ally", (), None, {"allies": "Vorash"}),
}
PLACES = (*CANON, *ARC_WORLDS)


@dataclass
class World:
    id: str                                  # the SGC designation, e.g. "P3X-866"; unique
    glyphs: tuple[int, ...]
    env: str = "normal"
    inhabitants: str = "none"
    features: tuple[str, ...] = ()
    owner: str | None = None                 # a Goa'uld, if one holds the world
    hidden_names: dict[str, str] = field(default_factory=dict)    # source -> name, until intel reveals it
    canon: bool = False
    status: str = "unexplored"
    surveyed: bool = False                   # a survey has counted for this world, even if it's since gone hostile
    names: list[tuple[str, str, int]] = field(default_factory=list)   # (name, source text, game minute)
    seen: dict[str, str] = field(default_factory=dict)            # traits learned: env, life, features, ...
    telemetry: list[str] = field(default_factory=list)            # the latest drone readings
    reports: list[tuple[int, str]] = field(default_factory=list)  # (game minute, text), oldest first
    notes: list[tuple[int, str]] = field(default_factory=list)
    drone: str | None = None                 # an intact MALP or UAV left on the world
    wreck: str | None = None                 # a UAV wreck on the world, a WRECK, until a team brings it home
    options: list[str] = field(default_factory=lambda: ["survey"])   # mission types unlocked here
    found: str = CARTOUCHE                   # where the address came from
    last_visit: int | None = None

    @property
    def name(self) -> str:
        """The most recently learned name, or the designation until intel gives one."""
        return self.names[-1][0] if self.names else self.id

    @property
    def danger(self) -> int:
        """0 (quiet) to 3 (Goa'uld stronghold), from the hidden traits."""
        d = _DANGER[self.inhabitants] + (1 if self.env in ("toxic", "radiation", "extreme") else 0)
        return min(3, d)

    @property
    def glyph_text(self) -> str:
        return "-".join(f"{g:02d}" for g in self.glyphs)

    def address(self) -> Address:
        return Address(self.name, self.id, tuple(self.glyphs), self.canon)


def designation(glyphs: tuple[int, ...]) -> str:
    """A catalogue number like 'P3X-866', always the same for the same glyphs."""
    h = 0
    for g in glyphs:
        h = (h * 41 + g) % 1_000_003
    return f"P{2 + h % 8}{_LETTERS[(h // 8) % len(_LETTERS)]}-{100 + (h // 72) % 900}"


def arc_designation(glyphs: tuple[int, ...]) -> str:
    """An arc world's placeholder designation: P1X-866 where designation() would give P3X-866. designation()
    only gives P2 to P9, so a generated or Stage 1 world can never hold it."""
    return "P1" + designation(glyphs)[2:]


def set_status(w: World, status: str) -> bool:
    """Move a world's status; returns False (and changes nothing) if the move isn't allowed.

    Progress only goes forward (UNEXPLORED, PROBED, SURVEYED, CONTACT). Any world can become HOSTILE or
    LOST; a LOST world can be PROBED again, and a HOSTILE one can reach CONTACT.
    """
    if status not in STATUSES:
        raise ValueError(f"unknown world status {status!r}")
    cur = w.status
    if status == cur:
        return False
    ok = (status in ("hostile", "lost")
          or (cur in PROGRESS and PROGRESS.index(status) > PROGRESS.index(cur))
          or (cur == "lost" and status == "probed")
          or (cur == "hostile" and status == "contact"))
    if ok:
        w.status = status
    return ok


def learn_name(w: World, name: str, source: str, minute: int) -> bool:
    """Record a name and where it came from; False if that exact name and source are already known."""
    if any(n == name and s == source for n, s, _ in w.names):
        return False
    w.names.append((name, source, minute))
    return True


def reveal_name(w: World, source: str, minute: int) -> str | None:
    """Reveal the name this source knows the world by (the locals' name if it has none of its own)."""
    name = w.hidden_names.get(source) or w.hidden_names.get("locals")
    if name is None:
        return None
    return name if learn_name(w, name, SOURCE_TEXT.get(source, source), minute) else None


def gen_name(rng: random.Random) -> str:
    parts = [rng.choice(_SYLLABLES) for _ in range(rng.choice((2, 2, 3)))]
    if len(parts) == 3 and rng.random() < 0.5:
        return (parts[0] + parts[1]).capitalize() + "'" + parts[2]
    return "".join(parts).capitalize()


def generate(rng: random.Random, taken: set[str], found: str = CARTOUCHE, owner_odds: float = 1.0) -> World:
    """A new world with canon-style traits and a designation not in `taken`."""
    while True:
        glyphs = tuple(rng.sample(range(2, 40), 6))
        wid = designation(glyphs)
        if wid not in taken:
            break
    env = rng.choices(ENVIRONMENTS, (60, 12, 10, 10, 8))[0]
    inhabitants = rng.choices(INHABITANTS, (40, 25, 8, 15, 7, 5))[0]
    if inhabitants in ("jaffa", "goauld") and rng.random() > owner_odds:
        inhabitants = "human"
    features = tuple(f for f, p in zip(FEATURES, (0.3, 0.15, 0.15)) if rng.random() < p)
    owner = rng.choice(GOAULD) if inhabitants in ("jaffa", "goauld") else None
    names = {"locals": gen_name(rng)}
    if owner:
        names["jaffa"] = gen_name(rng)
    return World(wid, glyphs, env, inhabitants, features, owner, names, found=found)


def canon_world(name: str) -> World:
    env, inhabitants, features, owner, names = CANON[name]
    glyphs = next(a.glyphs for a in load_canon() if a.name == name)
    return World(designation(glyphs), glyphs, env, inhabitants, features, owner, dict(names), canon=True)


def faction_name(fid: str) -> str:
    """'Apophis', "the Tok'ra"."""
    return _GOAULD_NAMES.get(fid) or ALLY_NAMES[fid]


def faction_id(owner: str | None) -> str | None:
    """The faction id of a world's owner ("Heru'ur" -> "heruur"); None for an unowned world."""
    return GOAULD_IDS.get(owner) if owner else None


def place(name: str) -> World:
    """A named canon or arc world, with its traits and hidden names."""
    if name in CANON:
        return canon_world(name)
    glyphs, env, inhabitants, features, owner, names = ARC_WORLDS[name]
    return World(arc_designation(glyphs), glyphs, env, inhabitants, features, owner, dict(names), canon=True)


@cache                                  # rules resolve @Name tokens often; load_canon reads a file
def place_id(name: str) -> str:
    """The designation of a named canon or arc world, e.g. place_id("Chulak")."""
    if name in CANON:
        return designation(next(a.glyphs for a in load_canon() if a.name == name))
    return arc_designation(ARC_WORLDS[name][0])


def cartouche(mode: str, seed: int, minute: int, reserved: set[str] = frozenset()) -> dict[str, World]:
    """The starting dialing list: 20 addresses, Abydos first.

    Campaign places the canon worlds; Sandbox is Abydos plus generated worlds, with Goa'uld-held
    worlds rarer.
    """
    rng = random.Random(seed)
    abydos = canon_world("Abydos")
    abydos.status, abydos.found, abydos.options = "contact", "the Abydos expedition", ["survey", "contact"]
    abydos.seen = {"env": "breathable atmosphere", "life": "humanoid life signs", "inhabitants": "settlement"}
    learn_name(abydos, "Abydos", "the Abydos expedition", minute)
    worlds = [abydos]
    if mode == "campaign":
        worlds += [canon_world(n) for n in CAMPAIGN_CANON]
    taken = {w.id for w in worlds} | set(reserved)
    while len(worlds) < CARTOUCHE_SIZE:
        w = generate(rng, taken, owner_odds=1.0 if mode == "campaign" else 0.3)
        taken.add(w.id)
        worlds.append(w)
    rest = worlds[1:]
    rng.shuffle(rest)
    return {w.id: w for w in [abydos, *rest]}


_ENV_TEXT = {"normal": "breathable atmosphere", "toxic": "toxic atmosphere", "radiation": "high radiation",
             "extreme": "extreme temperatures", "no_lock": "no lock"}
_LIFE = {"none": "none detected", "human": "humanoid life signs", "unas": "large reptilian life signs",
         "jaffa": "humanoid life signs", "goauld": "humanoid life signs", "ally": "humanoid life signs"}
_FEATURE_TEXT = {"ruins": "ruins", "technology": "energy readings", "naquadah": "naquadah traces"}
_SUBSURFACE_TEXT = {"naquadah": "naquadah deposit", "ruins": "buried structures",
                    "technology": "shielded power source"}
_SETTLEMENT = {"none": "no settlements", "human": "settlement", "unas": "Unas", "jaffa": "Jaffa garrison",
               "goauld": "Goa'uld stronghold", "ally": "outpost"}
_COUNT = {"none": (0, 0), "human": (40, 900), "unas": (3, 30), "jaffa": (50, 400), "goauld": (200, 2000),
          "ally": (10, 80)}


def readings(w: World, drone: str, detail: str, rng: random.Random) -> dict[str, str]:
    """What a MALP (or a UAV, which sees more) learns about a world, at this difficulty's level of detail."""
    out = {"env": _ENV_TEXT[w.env]}
    if detail == "minimal" and drone == "malp":
        out["life"] = "inconclusive"
        return out
    life = _LIFE[w.inhabitants]
    out["life"] = life if detail == "full" or life == "none detected" else "life signs"
    shown = [_FEATURE_TEXT[f] for f in w.features]
    if drone == "malp" and detail != "full":
        shown = shown[:1]
    if shown:
        out["features"] = ", ".join(shown)
    if drone == "uav":
        out["inhabitants"] = _SETTLEMENT[w.inhabitants]
        lo, hi = _COUNT[w.inhabitants]
        if hi:
            n = rng.randint(lo, hi)
            out["count"] = f"about {round(n, -1) or n}" if detail == "full" else ("many" if n > 150 else "some")
        out["subsurface"] = subsurface(w, drone, detail)
    return out


def subsurface(w: World, drone: str, detail: str) -> str:
    """What ground-penetrating radar (a UAV) or deep soil samples (a MALP's extended report) find under a world,
    from its features, at the same level of detail as FEATURES: a MALP below full detail names only one."""
    shown = [_SUBSURFACE_TEXT[f] for f in w.features]
    if drone == "malp" and detail != "full":
        shown = shown[:1]
    return ", ".join(shown) or "no anomalies"
