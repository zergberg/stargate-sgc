"""Kitty graphics protocol (Ghostty, Kitty, WezTerm, Konsole)."""
from __future__ import annotations

import base64
import os
import zlib
from pathlib import Path

from PIL import Image

from ...layout import Rect
from ..detect import Caps, shm_dir
from .base import Backend, move

CHUNK = 4096


class KittyBackend(Backend):
    name = "kitty"

    def __init__(self, caps: Caps):
        super().__init__(caps)
        self._dir = shm_dir()
        self._prefix = f"tty-graphics-protocol-sgc-{os.getpid()}-"
        self._n = 0

    def show(self, slot: int, image: Image.Image, rect: Rect) -> bytes:
        im = image.convert("RGB")
        w, h = im.size
        keys = f"a=T,f=24,s={w},v={h},i={slot},p=1,c={rect.w},r={rect.h},C=1,q=2"
        out = b"\x1b7" + move(rect)
        if self.caps.kitty_file:
            self._n += 1
            path = self._dir / f"{self._prefix}{self._n}"
            try:
                path.write_bytes(im.tobytes())
                payload = base64.standard_b64encode(str(path).encode())
                out += f"\x1b_G{keys},t=t;".encode() + payload + b"\x1b\\"
                if self._n % 50 == 0:
                    self._sweep(keep_last=4)
                return out + b"\x1b8"
            except OSError:
                pass
        data = base64.standard_b64encode(zlib.compress(im.tobytes(), 1))
        chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
        for n, chunk in enumerate(chunks):
            more = 1 if n < len(chunks) - 1 else 0
            head = f"{keys},t=d,o=z,m={more}" if n == 0 else f"m={more}"
            out += f"\x1b_G{head};".encode() + chunk + b"\x1b\\"
        return out + b"\x1b8"

    def _sweep(self, keep_last: int = 0) -> None:
        """Remove our temp files the terminal hasn't consumed (it normally deletes them itself)."""
        for p in self._dir.glob(f"{self._prefix}*"):
            try:
                if int(p.name[len(self._prefix):]) <= self._n - keep_last:
                    p.unlink()
            except (ValueError, OSError):
                pass

    def forget(self) -> bytes:
        return b"\x1b_Ga=d,d=A,q=2\x1b\\"

    def cleanup(self) -> bytes:
        self._sweep()
        return b"\x1b_Ga=d,d=A,q=2\x1b\\"
