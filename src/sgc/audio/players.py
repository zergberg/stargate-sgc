"""Find a command-line audio player that can stream PCM from stdin."""
from __future__ import annotations

import shutil
import struct

RATE = 44100

# (executable, command, wants a streaming WAV header before the raw PCM)
_CANDIDATES = (
    ("pw-cat", ["pw-cat", "--playback", "--latency", "60ms", "-"], True),
    ("paplay", ["paplay", "--raw", "--rate=44100", "--channels=1", "--format=s16le", "--latency-msec=60"], False),
    ("aplay", ["aplay", "-q", "-B", "60000", "-"], True),
)


def find_player() -> list[str] | None:
    for exe, cmd, _ in _CANDIDATES:
        if shutil.which(exe):
            return list(cmd)
    return None


def wants_header(cmd: list[str]) -> bool:
    return any(cmd and cmd[0] == exe and hdr for exe, _, hdr in _CANDIDATES)


def stream_header() -> bytes:
    """WAV header with 'unknown' (maximal) sizes, for players that read a pipe via libsndfile."""
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
            + b"data" + struct.pack("<I", 0xFFFFFFFF))
