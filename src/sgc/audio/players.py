"""Find a command-line audio player that can stream PCM from stdin."""
from __future__ import annotations

import functools
import shutil
import struct
import subprocess

RATE = 44100

# pw-cat is told the real format rather than sent a WAV header: PipeWire 1.0.x pw-cat always
# treats stdin as raw PCM (it ignores the header and assumes 48 kHz stereo), while newer
# versions only take raw PCM from stdin when given --raw.
_PWCAT_FORMAT = ["--rate", str(RATE), "--channels", "1", "--format", "s16"]
# Name the stream after sgc: PipeWire/PulseAudio remember volume per application, so a stream
# left as "pw-cat" or "paplay" inherits whatever volume some other tool last set.
_STREAM_PROPS = '{ application.name = "sgc" node.name = "sgc" media.name = "Stargate SGC" }'

# (executable, command, wants a streaming WAV header before the raw PCM)
_CANDIDATES = (
    ("pw-cat", ["pw-cat", "--playback", "--latency", "60ms", *_PWCAT_FORMAT, "-P", _STREAM_PROPS, "-"], False),
    ("paplay", ["paplay", "--raw", "--rate=44100", "--channels=1", "--format=s16le", "--latency-msec=60",
                "--client-name=sgc", "--stream-name=Stargate SGC"], False),
    ("aplay", ["aplay", "-q", "-B", "60000", "-"], True),
)


@functools.cache
def _pwcat_supports_raw() -> bool:
    try:
        out = subprocess.run(["pw-cat", "--help"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return "--raw" in out.stdout + out.stderr


def find_player() -> list[str] | None:
    for exe, cmd, _ in _CANDIDATES:
        if shutil.which(exe):
            cmd = list(cmd)
            if exe == "pw-cat" and _pwcat_supports_raw():
                cmd.insert(-1, "--raw")
            return cmd
    return None


def wants_header(cmd: list[str]) -> bool:
    return any(cmd and cmd[0] == exe and hdr for exe, _, hdr in _CANDIDATES)


def stream_header() -> bytes:
    """WAV header with 'unknown' (maximal) sizes, for players that read a pipe via libsndfile."""
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
            + b"data" + struct.pack("<I", 0xFFFFFFFF))
