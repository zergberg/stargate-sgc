"""Turns raw input bytes into key names, discarding escape sequences (terminal replies, arrows)."""
from __future__ import annotations

import time
import unicodedata

PENDING_TIMEOUT = 0.1        # seconds an unfinished escape sequence may wait for its end (a lone ESC: the Esc key)
_ABORT = (0x03, 0x18, 0x1a)  # Ctrl+C, CAN, SUB cancel a pending sequence
_KEYS = {"q": "q", "Q": "q", "m": "m", "M": "m", "p": "p", "P": "p", "+": "+", "=": "+",
         "-": "-", "_": "-", " ": "space", "\x03": "ctrl-c", "r": "r", "R": "r", "\r": "enter", "\n": "enter",
         "d": "d", "D": "d", "b": "b", "B": "b", "?": "?", "/": "/", "s": "s", "S": "s", "f": "f", "F": "f",
         "\t": "tab", "\x7f": "backspace", "\x08": "backspace",
         **{str(d): str(d) for d in range(1, 10)}}
_TEXT_KEYS = {"\x03": "ctrl-c", "\r": "enter", "\n": "enter", "\x7f": "backspace", "\x08": "backspace"}
_BAD = "?"                   # typed text that can't take one cell: not UTF-8, wide, combining, a control
_SEQ_KEYS = {b"\x1b[A": "up", b"\x1b[B": "down", b"\x1bOA": "up", b"\x1bOB": "down",
             b"\x1b[C": "right", b"\x1b[D": "left", b"\x1bOC": "right", b"\x1bOD": "left"}


class KeyParser:
    def __init__(self) -> None:
        self._buf = b""
        self._pending_since: float | None = None
        self.text = False            # typing into a field: printable bytes come back as "ch:<c>"

    def feed(self, data: bytes, now: float | None = None) -> list[str]:
        now = time.monotonic() if now is None else now
        keys: list[str] = []
        if self._buf == b"\x1b" and self._expired(now):     # nothing followed the ESC in time: the Esc key
            keys.append("escape")
            self._buf, self._pending_since = b"", None
        buf = self._buf + data
        i = 0
        while i < len(buf):
            b = buf[i]
            if b >= 0x80 and self.text:              # UTF-8 text: é, ü, ...
                end = _utf8_end(buf, i)
                if end is None:                      # the rest of it is still on its way
                    break
                keys.append(f"ch:{_cell(buf[i:end])}")
                i = end
                continue
            if b != 0x1b:
                if self.text:
                    name = _TEXT_KEYS.get(chr(b)) or (f"ch:{chr(b)}" if 0x20 <= b < 0x7f else None)
                else:
                    name = _KEYS.get(chr(b))
                if name:
                    keys.append(name)
                i += 1
                continue
            end = self._sequence_end(buf, i)
            if end is None:          # incomplete: wait for more bytes, but not forever
                expired = self._expired(now)
                if expired and i + 1 == len(buf):
                    keys.append("escape")
                    i += 1
                    self._pending_since = None
                    continue
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
            self._pending_since = None   # a later unfinished sequence gets its own full wait
        self._buf = buf[i:]
        if not self._buf:
            self._pending_since = None
        return keys

    def _expired(self, now: float) -> bool:
        return self._pending_since is not None and now - self._pending_since > PENDING_TIMEOUT

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


def _utf8_end(buf: bytes, i: int) -> int | None:
    """Where the UTF-8 character starting at buf[i] ends, or None if the rest of it hasn't arrived yet.
    A byte that can't start one, or a sequence cut short, ends at once (and comes out as _BAD)."""
    b = buf[i]
    n = 2 if 0xC2 <= b <= 0xDF else 3 if 0xE0 <= b <= 0xEF else 4 if 0xF0 <= b <= 0xF4 else 1
    for j in range(i + 1, i + n):
        if j >= len(buf):
            return None
        if not 0x80 <= buf[j] <= 0xBF:
            return j                                 # cut short: this byte starts something new
    return i + n


def _cell(raw: bytes) -> str:
    """A typed character that takes exactly one terminal cell, or _BAD."""
    try:
        ch = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _BAD
    if unicodedata.category(ch)[0] in "CM" or unicodedata.east_asian_width(ch) in "WF":
        return _BAD
    return ch
