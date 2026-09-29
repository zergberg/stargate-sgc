"""Sound bank: resolves each cue to 44.1 kHz mono 16-bit samples.

Source order for a cue: the user's folder, then the selected pack, then the
synth pack (which has every cue).
"""
from __future__ import annotations

import shutil
import subprocess
import wave
from array import array
from pathlib import Path

RATE = 44100
ASSETS = Path(__file__).resolve().parents[3] / "assets" / "sounds"
USER_DIR = Path.home() / ".config" / "stargate-sgc" / "sounds"
CACHE_DIR = Path.home() / ".cache" / "stargate-sgc"
_OTHER_FORMATS = (".ogg", ".mp3", ".flac")


def read_wav(path: Path) -> array | None:
    try:
        with wave.open(str(path)) as w:
            ch, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            raw = w.readframes(w.getnframes())
    except (OSError, EOFError, wave.Error):
        return None
    if width == 2:
        s = array("h")
        s.frombytes(raw)
    elif width == 1:
        s = array("h", ((b - 128) << 8 for b in raw))
    else:
        return None
    if ch > 1:
        s = array("h", (sum(s[i:i + ch]) // ch for i in range(0, len(s) - ch + 1, ch)))
    if rate != RATE and len(s) > 1:
        n_out = int(round(len(s) * RATE / rate))
        step = rate / RATE
        out = array("h")
        for i in range(n_out):
            x = i * step
            j = min(int(x), len(s) - 2)
            f = x - j
            out.append(int(s[j] * (1 - f) + s[j + 1] * f))
        s = out
    return s


class SoundBank:
    def __init__(self, pack: str, user_dir: Path = USER_DIR, assets: Path = ASSETS, cache_dir: Path = CACHE_DIR):
        self.pack = pack
        self.user_dir = Path(user_dir)
        self.assets = Path(assets)
        self.cache_dir = Path(cache_dir)
        self.warnings: list[str] = []
        self._cache: dict[str, array | None] = {}

    def _pack_dir(self) -> Path:
        return self.assets / ("freesound/wav" if self.pack == "freesound" else "synth")

    def _user_file(self, cue: str) -> array | None:
        wav = self.user_dir / f"{cue}.wav"
        if wav.is_file():
            return read_wav(wav)
        for ext in _OTHER_FORMATS:
            src = self.user_dir / f"{cue}{ext}"
            if not src.is_file():
                continue
            if not shutil.which("ffmpeg"):
                self.warnings.append(f"sound {src.name}: install ffmpeg to use non-WAV files; skipped")
                return None
            out = self.cache_dir / f"user-{cue}-{int(src.stat().st_mtime)}.wav"
            if not out.exists():
                out.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", str(RATE),
                                "-sample_fmt", "s16", str(out)], check=False)
            return read_wav(out)
        return None

    def get(self, cue: str) -> array | None:
        if cue not in self._cache:
            s = self._user_file(cue)
            if s is None:
                s = read_wav(self._pack_dir() / f"{cue}.wav")
            if s is None:
                s = read_wav(self.assets / "synth" / f"{cue}.wav")
            self._cache[cue] = s
        return self._cache[cue]
