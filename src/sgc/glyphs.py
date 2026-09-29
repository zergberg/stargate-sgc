"""Gate glyph numbering, names and the glyph font.

Glyph numbers follow the standard gate order: 1 is Earth's point of origin,
2-39 run clockwise from Crater to Leo. The fan font "Stargate SG-1 Address
Glyphs" (Joy Anne Baker) maps glyph n to A-Z for 1-26 and a-m for 27-39.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import zipfile
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
# The font may not be redistributed or direct-linked, so users download it themselves from this page.
FONT_PAGE = "https://www.thescifiworld.net/fonts.htm"


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


def _font_bytes(src: Path) -> bytes:
    """The .ttf inside src (a .ttf itself, or a .zip holding one)."""
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            names = [n for n in z.namelist() if n.lower().endswith((".ttf", ".otf"))]
            if not names:
                raise ValueError(f"{src} has no .ttf inside")
            return z.read(names[0])
    return src.read_bytes()


def _looks_like_glyph_download(p: Path) -> bool:
    name = p.name.lower()
    return p.suffix.lower() in (".ttf", ".otf", ".zip") and "stargate" in name and "glyph" in name


def install_font(src: Path | None, dest_dir: Path = Path.home() / ".local/share/fonts",
                 search_dirs: tuple[Path, ...] = (Path.home() / "Downloads",)) -> Path:
    """Copy a downloaded glyph font (.ttf or .zip) into dest_dir under the name find_font expects."""
    if src is None:
        found = [p for d in search_dirs if d.is_dir() for p in d.iterdir() if _looks_like_glyph_download(p)]
        if not found:
            raise ValueError("no Stargate glyph font found in " + ", ".join(map(str, search_dirs))
                             + f". Download \"Stargate SG-1 Address Glyphs\" from {FONT_PAGE}, then run "
                             "sgc --install-font again (or pass the file's path).")
        src = max(found, key=lambda p: p.stat().st_mtime)
    src = Path(src).expanduser()
    if not src.is_file():
        raise ValueError(f"{src} is not a file")
    data = _font_bytes(src)
    try:
        ImageFont.truetype(io.BytesIO(data), 24)
    except OSError:
        raise ValueError(f"{src} is not a usable font file") from None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / FONT_FILE
    dest.write_bytes(data)
    if shutil.which("fc-cache"):
        subprocess.run(["fc-cache", "-f", str(dest_dir)], capture_output=True, check=False)
    return dest
