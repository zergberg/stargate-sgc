"""Turns raw input bytes into key names, discarding escape sequences (terminal replies, arrows)."""
from __future__ import annotations

import time

PENDING_TIMEOUT = 0.1        # seconds an unfinished escape sequence may wait for its end
_ABORT = (0x03, 0x18, 0x1a)  # Ctrl+C, CAN, SUB cancel a pending sequence
_KEYS = {"q": "q", "Q": "q", "m": "m", "M": "m", "p": "p", "P": "p", "+": "+", "=": "+",
         "-": "-", "_": "-", " ": "space", "\x03": "ctrl-c", "r": "r", "R": "r", "\r": "enter", "\n": "enter",
         **{str(d): str(d) for d in range(1, 10)}}
_SEQ_KEYS = {b"\x1b[A": "up", b"\x1b[B": "down", b"\x1bOA": "up", b"\x1bOB": "down"}


class KeyParser:
    def __init__(self) -> None:
        self._buf = b""
        self._pending_since: float | None = None

    def feed(self, data: bytes, now: float | None = None) -> list[str]:
        now = time.monotonic() if now is None else now
        buf = self._buf + data
        keys: list[str] = []
        i = 0
        while i < len(buf):
            b = buf[i]
            if b != 0x1b:
                name = _KEYS.get(chr(b))
                if name:
                    keys.append(name)
                i += 1
                continue
            end = self._sequence_end(buf, i)
            if end is None:          # incomplete: wait for more bytes, but not forever
                expired = self._pending_since is not None and now - self._pending_since > PENDING_TIMEOUT
                if expired or any(c in _ABORT for c in buf[i + 1:]) or len(buf) - i > 4096:
                    i += 2           # give up on it: drop ESC + introducer (an Alt-chord), read the rest as keys
                    self._pending_since = None
                    continue
                if self._pending_since is None:
                    self._pending_since = now
                break
            name = _SEQ_KEYS.get(bytes(buf[i:end]))
            if name:
                keys.append(name)
            i = end
        self._buf = buf[i:]
        if not self._buf:
            self._pending_since = None
        return keys

    @staticmethod
    def _sequence_end(buf: bytes, i: int) -> int | None:
        if i + 1 >= len(buf):
            return None
        kind = buf[i + 1]
        if kind == ord("["):                                  # CSI: params then a final byte
            for j in range(i + 2, len(buf)):
                if 0x40 <= buf[j] <= 0x7E:
                    return j + 1
            return None
        if kind in (ord("]"), ord("_"), ord("P"), ord("^")):  # OSC / APC / DCS / PM: to ST or BEL
            for j in range(i + 2, len(buf)):
                if buf[j] == 0x07 and kind == ord("]"):
                    return j + 1
                if buf[j] == 0x1b and j + 1 < len(buf) and buf[j + 1] == ord("\\"):
                    return j + 2
            return None
        if kind == ord("O"):                                  # SS3: one more byte
            return i + 3 if i + 2 < len(buf) else None
        return i + 2                                          # alt+key: ignore
