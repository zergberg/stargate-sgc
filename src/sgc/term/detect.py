"""Work out what the terminal can do: image protocol, colour depth, cell pixel size."""
from __future__ import annotations

import base64
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .screen import Terminal

_DA1 = re.compile(rb"\x1b\[\?[\d;]*c")
_TRUECOLOR_PROGRAMS = ("ghostty", "wezterm", "iterm.app", "kitty", "vscode")


@dataclass(frozen=True)
class Caps:
    graphics: str          # kitty | sixel | iterm | blocks
    truecolor: bool
    cell_w: float
    cell_h: float
    kitty_file: bool       # kitty t=t temp-file transfer works (same machine)
    in_tmux: bool


def shm_dir() -> Path:
    d = Path("/dev/shm")
    return d if d.is_dir() and os.access(d, os.W_OK) else Path(tempfile.gettempdir())


def detect(term: Terminal, forced: str, env: Mapping[str, str]) -> Caps:
    in_tmux = "TMUX" in env
    ask_kitty = forced in ("auto", "kitty") and (not in_tmux or forced == "kitty")
    seq = b""
    probe = None
    if ask_kitty:
        seq += b"\x1b_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\x1b\\"
        probe = shm_dir() / f"tty-graphics-protocol-sgc-probe-{os.getpid()}"
        try:
            probe.write_bytes(b"\0\0\0")
            path = base64.standard_b64encode(str(probe).encode()).decode()
            seq += f"\x1b_Gi=32,s=1,v=1,a=q,t=t,f=24;{path}\x1b\\".encode()
        except OSError:
            probe = None
    seq += b"\x1b[16t\x1b[c"
    resp = term.query(seq, _DA1, 0.5)
    if probe is not None:
        try:
            probe.unlink()
        except OSError:
            pass

    kitty = b"_Gi=31;OK" in resp
    kitty_file = b"_Gi=32;OK" in resp
    m = re.search(rb"\x1b\[6;(\d+);(\d+)t", resp)
    if m and int(m.group(1)) and int(m.group(2)):
        term.cell_px = (float(m.group(2)), float(m.group(1)))
    da1 = _DA1.search(resp)
    sixel = bool(da1) and b"4" in da1.group(0)[3:-1].split(b";")[1:]

    program = env.get("TERM_PROGRAM", "").lower()
    if forced != "auto":
        graphics = forced
    elif kitty and not in_tmux:
        graphics = "kitty"
    elif sixel:
        graphics = "sixel"
    elif program == "iterm.app":
        graphics = "iterm"
    else:
        graphics = "blocks"
    truecolor = (env.get("COLORTERM", "").lower() in ("truecolor", "24bit") or kitty
                 or program in _TRUECOLOR_PROGRAMS or any(k in env.get("TERM", "") for k in ("kitty", "ghostty")))
    _, _, cw, ch = term.size()
    return Caps(graphics, truecolor, cw, ch, kitty_file, in_tmux)
