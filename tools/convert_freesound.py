#!/usr/bin/env python3
"""Convert the downloaded freesound previews into the `freesound` sound pack.

Usage: python tools/convert_freesound.py
Reads assets/sounds/freesound/pack.json and the .ogg previews next to it,
writes assets/sounds/freesound/wav/<cue>.wav (44.1 kHz mono 16-bit).
Needs the dev extra (soundfile); the app itself only reads the WAVs.
"""
from __future__ import annotations

import json
import sys
import wave
from array import array
from pathlib import Path

import numpy as np
import soundfile as sf

RATE = 44100
ROOT = Path(__file__).resolve().parents[1] / "assets" / "sounds" / "freesound"


def _resample(x: np.ndarray, rate: int) -> np.ndarray:
    if rate == RATE:
        return x
    n_out = int(round(len(x) * RATE / rate))
    return np.interp(np.arange(n_out) * rate / RATE, np.arange(len(x)), x)


def _loopify(x: np.ndarray, k: int) -> np.ndarray:
    head, tail = x[:-k].copy(), x[-k:]
    w = np.arange(k) / k
    head[:k] = head[:k] * w + tail * (1 - w)
    return head


def convert(pack: dict, srcdir: Path, outdir: Path) -> None:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for cue, spec in pack.items():
        data, rate = sf.read(str(Path(srcdir) / spec["file"]), always_2d=True)
        x = _resample(data.mean(axis=1), rate)
        start = int(spec.get("start", 0) * RATE)
        end = int(spec["end"] * RATE) if spec.get("end") else len(x)
        x = x[start:end]
        fi, fo = int(spec.get("fade_in", 0) * RATE), int(spec.get("fade_out", 0) * RATE)
        if fi:
            x[:fi] *= np.linspace(0, 1, fi)
        if fo:
            x[-fo:] *= np.linspace(1, 0, fo)
        if spec.get("loop"):
            x = _loopify(x, int(0.03 * RATE))
        peak = np.max(np.abs(x)) or 1.0
        x = x / peak * 0.89 * spec.get("gain", 1.0)
        pcm = array("h", (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
        with wave.open(str(outdir / f"{cue}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm.tobytes())


if __name__ == "__main__":
    pack = json.loads((ROOT / "pack.json").read_text())
    convert(pack, ROOT, ROOT / "wav")
    print(f"wrote {len(pack)} cues to {ROOT / 'wav'}", file=sys.stderr)
