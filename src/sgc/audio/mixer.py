"""Pure-Python mixer: sums active voices and streams 44.1 kHz mono PCM to one player process."""
from __future__ import annotations

import fcntl
import subprocess
import termios
import threading
import time
from array import array

from .bank import RATE, SoundBank
from .players import find_player, stream_header, wants_header

CHUNK = 882            # 20 ms
LEAD = 0.1             # seconds of audio kept queued in the pipe to the player
RUNAWAY = 2.0          # never get more than this far ahead of the wall clock (+1 %/s slack)
MAX_PER_CUE = 4


class Voice:
    def __init__(self, samples: array, loop: bool = False, gain: float = 1.0, name: str = ""):
        self.samples, self.loop, self.gain, self.name = samples, loop, gain, name
        self.pos = 0
        self.done = False
        self._fade_left: int | None = None
        self._fade_total = 1

    @property
    def fading(self) -> bool:
        return self._fade_left is not None

    def fade_out(self, n_samples: int) -> None:
        if self._fade_left is None:
            self._fade_total = self._fade_left = max(1, n_samples)


def mix_chunk(voices: list[Voice], n: int, master: float) -> bytes:
    acc = [0.0] * n
    for v in voices:
        if v.done:
            continue
        s, length, g = v.samples, len(v.samples), v.gain * master
        for i in range(n):
            if v.pos >= length:
                if v.loop and length:
                    v.pos = 0
                else:
                    v.done = True
                    break
            amp = g
            if v._fade_left is not None:
                if v._fade_left <= 0:
                    v.done = True
                    break
                amp *= v._fade_left / v._fade_total
                v._fade_left -= 1
            acc[i] += s[v.pos] * amp
            v.pos += 1
    return array("h", (32767 if x > 32767 else -32768 if x < -32768 else int(x) for x in acc)).tobytes()


class NullMixer:
    """Same API as Mixer; used when sound is off or no player exists."""
    dead = True
    volume = 0.0
    muted = True

    def start(self): pass
    def play(self, cue, loop=False, gain=1.0): pass
    def stop(self, cue, fade=0.25): pass
    def stop_all(self, fade=0.3): pass
    def set_volume(self, v): pass
    def toggle_mute(self): return True
    def pause(self, paused): pass
    def close(self): pass


class Mixer:
    def __init__(self, bank: SoundBank, volume: float, player_cmd: list[str] | None = None):
        self.bank = bank
        self._volume = max(0.0, min(1.0, volume))
        self.muted = False
        self._paused = False
        self._cmd = player_cmd if player_cmd is not None else find_player()
        self._voices: list[Voice] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self.dead = False
        self.written = 0       # bytes of PCM handed to the player

    @property
    def volume(self) -> float:
        return self._volume

    def start(self) -> None:
        if not self._cmd:
            self.dead = True
            return
        try:
            self._proc = subprocess.Popen(self._cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
            if wants_header(self._cmd):
                self._proc.stdin.write(stream_header())
        except OSError:
            self.dead = True
            return
        self._thread = threading.Thread(target=self._run, name="sgc-audio", daemon=True)
        self._thread.start()

    def _queued(self) -> int | None:
        """Bytes sitting in the pipe, not yet read by the player (Linux FIONREAD)."""
        try:
            buf = fcntl.ioctl(self._proc.stdin.fileno(), termios.FIONREAD, b"\0\0\0\0")
            return int.from_bytes(buf, "little")
        except (OSError, ValueError):
            return None

    def _run(self) -> None:
        # Pace by what the player has consumed (the sound card's clock), so audio never drifts
        # away from the picture; fall back to the wall clock where FIONREAD isn't available.
        t0 = time.monotonic()
        target = int(LEAD * RATE) * 2
        while not self._stop.is_set():
            elapsed = time.monotonic() - t0
            ahead = self.written / 2 / RATE - elapsed
            queued = self._queued()
            full = (queued >= target) if queued is not None else (ahead >= LEAD)
            if full or ahead > RUNAWAY + 0.01 * elapsed:
                time.sleep(0.005)
                continue
            with self._lock:
                voices = list(self._voices)
            if self._paused:
                data = bytes(CHUNK * 2)
            else:
                data = mix_chunk(voices, CHUNK, 0.0 if self.muted else self._volume)
                with self._lock:
                    self._voices = [v for v in self._voices if not v.done]
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                self.dead = True
                return
            self.written += len(data)

    def play(self, cue: str, loop: bool = False, gain: float = 1.0) -> None:
        if self.dead:
            return
        samples = self.bank.get(cue)
        if samples is None:
            return
        with self._lock:
            same = [v for v in self._voices if v.name == cue and not v.done]
            if loop and any(v.loop and not v.fading for v in same):
                return
            if len(same) >= MAX_PER_CUE:
                return
            self._voices.append(Voice(samples, loop, gain, cue))

    def stop(self, cue: str, fade: float = 0.25) -> None:
        with self._lock:
            for v in self._voices:
                if v.name == cue:
                    v.fade_out(int(fade * RATE))

    def stop_all(self, fade: float = 0.3) -> None:
        with self._lock:
            for v in self._voices:
                v.fade_out(int(fade * RATE))

    def set_volume(self, v: float) -> None:
        self._volume = max(0.0, min(1.0, v))

    def toggle_mute(self) -> bool:
        self.muted = not self.muted
        return self.muted

    def pause(self, paused: bool) -> None:
        self._paused = paused

    def close(self) -> None:
        """Stop the player first, so a mixer thread blocked on a full pipe gets EPIPE and exits."""
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._proc and not (self._thread and self._thread.is_alive()):
            try:
                self._proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
