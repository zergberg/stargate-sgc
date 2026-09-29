"""Gate glyph numbering, names and the glyph font.

Glyph numbers follow the standard gate order: 1 is Earth's point of origin,
2-39 run clockwise from Crater to Leo. The fan font "Stargate SG-1 Address
Glyphs" (Joy Anne Baker) maps glyph n to A-Z for 1-26 and a-m for 27-39.
"""
from __future__ import annotations

from pathlib import Path

from PIL import ImageFont

GLYPH_NAMES: tuple[str, ...] = (
    "Earth", "Crater", "Virgo", "Bootes", "Centaurus", "Libra", "Serpens Caput",
    "Norma", "Scorpius", "Corona Australis", "Scutum", "Sagittarius", "Aquila",
    "Microscopium", "Capricornus", "Piscis Austrinus", "Equuleus", "Aquarius",
    "Pegasus", "Sculptor", "Pisces", "Andromeda", "Triangulum", "Aries", "Perseus",
    "Cetus", "Taurus", "Auriga", "Eridanus", "Orion", "Canis Minor", "Monoceros",
    "Gemini", "Hydra", "Lynx", "Cancer", "Sextans", "Leo Minor", "Leo",
)

FONT_FILE = "stargate_sg1_adress_glyphs.ttf"
_FONT_DIRS = (Path.home() / ".local/share/fonts", Path("/usr/local/share/fonts"), Path("/usr/share/fonts"))


def glyph_char(n: int) -> str:
    """Font character for glyph number n (1-39)."""
    if not 1 <= n <= 39:
        raise ValueError(f"glyph {n} out of range")
    return chr(ord("A") + n - 1) if n <= 26 else chr(ord("a") + n - 27)


def find_font(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    for d in _FONT_DIRS:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.name.lower() == FONT_FILE:
                return p
    return None


def load_glyph_font(path: Path | None, size: int) -> ImageFont.FreeTypeFont | None:
    if path is None:
        return None
    try:
        return ImageFont.truetype(str(path), max(1, int(size)))
    except OSError:
        return None
