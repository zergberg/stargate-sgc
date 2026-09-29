"""Low-level terminal control: raw mode, alternate screen, size queries, reads and writes."""
from __future__ import annotations

import fcntl
import os
import re
import select
import struct
import termios
import time


class Terminal:
    def __init__(self, fd_in: int = 0, fd_out: int = 1):
        self.fd_in, self.fd_out = fd_in, fd_out
        self._saved: list | None = None
        self._entered = False
        self.cell_px: tuple[float, float] | None = None   # from a CSI 16 t reply, if any

    # -- modes
    def _raw(self) -> list | None:
        try:
            saved = termios.tcgetattr(self.fd_in)
        except termios.error:
            return None
        mode = termios.tcgetattr(self.fd_in)
        mode[0] &= ~(termios.IXON | termios.ICRNL)
        mode[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN)
        mode[6][termios.VMIN] = 0
        mode[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd_in, termios.TCSANOW, mode)
        return saved

    def _restore(self, saved: list | None) -> None:
        if saved is not None:
            try:
                termios.tcsetattr(self.fd_in, termios.TCSANOW, saved)
            except termios.error:
                pass

    def enter(self) -> None:
        if self._entered:
            return
        self._saved = self._raw()
        self._entered = True
        # alt screen, hide cursor, no autowrap (writing the last cell must not scroll), clear
        self.write(b"\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[2J")

    def leave(self) -> None:
        if not self._entered:
            return
        self._entered = False
        try:
            # CAN + ST abort any image/escape string a signal cut off mid-frame
            self.write(b"\x18\x1b\\\x1b[?2026l\x1b[0m\x1b[?7h\x1b[?25h\x1b[?1049l")
        finally:
            self._restore(self._saved)

    # -- io
    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            try:
                n = os.write(self.fd_out, view)
            except BlockingIOError:
                select.select([], [self.fd_out], [], 0.05)
                continue
            view = view[n:]

    def read_available(self, timeout: float) -> bytes:
        out = b""
        r, _, _ = select.select([self.fd_in], [], [], max(0.0, timeout))
        while r:
            try:
                chunk = os.read(self.fd_in, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            r, _, _ = select.select([self.fd_in], [], [], 0)
        return out

    def query(self, seq: bytes, until: re.Pattern, timeout: float) -> bytes:
        """Send `seq` and collect replies until `until` matches or the timeout passes."""
        saved = None if self._entered else self._raw()
        try:
            self.write(seq)
            buf, end = b"", time.monotonic() + timeout
            while time.monotonic() < end:
                buf += self.read_available(end - time.monotonic())
                if until.search(buf):
                    break
            return buf
        finally:
            if saved is not None:
                self._restore(saved)

    def size(self) -> tuple[int, int, float, float]:
        """(cols, rows, cell_w_px, cell_h_px)."""
        try:
            rows, cols, xp, yp = struct.unpack("HHHH", fcntl.ioctl(self.fd_out, termios.TIOCGWINSZ, b"\0" * 8))
        except OSError:
            rows, cols, xp, yp = 24, 80, 0, 0
        cols, rows = cols or 80, rows or 24
        if xp and yp:
            return cols, rows, xp / cols, yp / rows
        if self.cell_px:
            return cols, rows, *self.cell_px
        return cols, rows, 10.0, 20.0
