"""sgc: the SGC dialing computer. Main loop, keys, adaptive frame rate, resize, exit and cleanup."""
from __future__ import annotations

import argparse
import os
import random
import signal
import sys
import time
import traceback
from collections import deque
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .addresses import AddressPicker, load_canon
from .audio.bank import SoundBank
from .audio.mixer import Mixer, NullMixer
from .config import Config, load_config
from .director import Director
from .events import REGISTRY
from .glyphs import find_font
from .layout import compute_layout
from .panels import draw_panels
from .render.addressbar import AddressBarRenderer
from .render.gate import GateRenderer
from .term.canvas import Canvas
from .term.detect import detect
from .term.graphics import make_backend
from .term.keys import KeyParser
from .term.screen import Terminal

CUE_GAIN = {"wormhole_hum": 0.45, "ring_spin": 0.6, "klaxon": 0.5, "kawoosh": 1.0}
FPS_STEPS = (24, 15, 10)
SCALE_STEPS = (1.0, 0.8, 0.65, 0.5)


class Terminated(Exception):
    pass


class App:
    def __init__(self, term: Terminal, cfg: Config, mixer, rng: random.Random, event: str | None = None,
                 duration: float | None = None, warnings: list[str] | None = None):
        self.term, self.cfg, self.mixer, self.rng = term, cfg, mixer, rng
        self.event, self.duration = event, duration
        self.logs: deque[str] = deque(maxlen=60)
        self._startup_warnings = warnings or []
        self._resized = True
        self._quit = False
        self._paused = False
        self.backend = None
        fps = cfg.fps
        self._fps_steps = [f for f in FPS_STEPS if f <= fps] or [fps]
        if fps not in self._fps_steps:
            self._fps_steps.insert(0, fps)
        self._fps_i = 0
        self._scale_i = 0
        self._costs: deque[float] = deque(maxlen=30)
        self._over_since: float | None = None
        self._under_since: float | None = None
        self._gate_key = None

    # ------------------------------------------------------------------ helpers
    def log(self, line: str) -> None:
        self.logs.append(f"{datetime.now():%H:%M:%S}  {line}")

    @property
    def fps(self) -> int:
        return self._fps_steps[self._fps_i]

    def _relayout(self) -> bytes:
        cols, rows, cw, ch = self.term.size()
        self.caps = replace(self.caps, cell_w=cw, cell_h=ch)
        self.backend.caps = self.caps
        self.layout = compute_layout(cols, rows, cw, ch)
        self.canvas = Canvas(cols, rows)
        self.canvas.set_holes([self.layout.gate, self.layout.bar])
        self.gate = None
        self.bar = None
        self._bar_key = None
        self._gate_key = None
        return self.backend.forget() + b"\x1b[0m\x1b[2J"

    def _handle_keys(self, keys: list[str]) -> None:
        for k in keys:
            if k in ("q", "ctrl-c"):
                if self.director.exiting:
                    self._quit = True
                else:
                    self.director.begin_exit()
            elif k == "m":
                muted = self.mixer.toggle_mute()
                self.log("AUDIO MUTED" if muted else "AUDIO ON")
            elif k in ("+", "-"):
                self.mixer.set_volume(self.mixer.volume + (0.1 if k == "+" else -0.1))
                self.log(f"VOLUME {round(self.mixer.volume * 100)}%")
            elif k == "space":
                self.director.skip()
            elif k == "p":
                self._paused = not self._paused
                self.mixer.pause(self._paused)
                self.log("PAUSED" if self._paused else "RESUMED")

    def _play(self, cues: list[str]) -> None:
        for cue in cues:
            if cue == "stopall":
                self.mixer.stop_all()
            elif cue.startswith("stop:"):
                self.mixer.stop(cue[5:])
            elif cue.startswith("loop:"):
                name = cue[5:]
                self.mixer.play(name, loop=True, gain=CUE_GAIN.get(name, 1.0))
            else:
                self.mixer.play(cue, gain=CUE_GAIN.get(cue, 1.0))

    def _adapt(self, cost: float, now: float) -> bytes:
        """Step the frame rate, then the image scale, down while frames overrun their budget for 2 s;
        step back up (scale first, then frame rate) after 10 s of comfortable headroom."""
        self._costs.append(cost)
        if len(self._costs) < 10:
            return b""
        mean = sum(self._costs) / len(self._costs)
        budget = 1 / self.fps
        if mean > budget * 1.1:
            self._under_since = None
            if self._over_since is None:
                self._over_since = now
            if now - self._over_since < 2.0:
                return b""
            self._over_since = None
            self._costs.clear()
            if self._fps_i < len(self._fps_steps) - 1:
                self._fps_i += 1
                return b""
            if self._scale_i < len(SCALE_STEPS) - 1:
                return self._set_scale(self._scale_i + 1)
            return b""
        self._over_since = None
        if self._scale_i > 0:
            growth = (SCALE_STEPS[self._scale_i - 1] / SCALE_STEPS[self._scale_i]) ** 2
            roomy = mean * growth < 0.6 * budget
        elif self._fps_i > 0:
            roomy = mean < 0.6 / self._fps_steps[self._fps_i - 1]
        else:
            return b""
        if not roomy:
            self._under_since = None
            return b""
        if self._under_since is None:
            self._under_since = now
        if now - self._under_since < 10.0:
            return b""
        self._under_since = None
        self._costs.clear()
        if self._scale_i > 0:
            return self._set_scale(self._scale_i - 1)
        self._fps_i -= 1
        return b""

    def _set_scale(self, i: int) -> bytes:
        self._scale_i = i
        self.backend.scale = SCALE_STEPS[i]
        return self._relayout()

    def _cleanup(self) -> None:
        """Always restore the terminal, even if deleting images or stopping audio fails."""
        saved = {}
        for sig in (signal.SIGTERM, signal.SIGHUP):
            try:
                saved[sig] = signal.signal(sig, signal.SIG_IGN)
            except (ValueError, OSError):
                pass
        try:
            try:
                if self.backend is not None:
                    self.term.write(self.backend.cleanup())
            except Exception:
                pass
            finally:
                try:
                    self.term.leave()
                finally:
                    self.mixer.close()
        finally:
            for sig, handler in saved.items():
                signal.signal(sig, handler)

    # ------------------------------------------------------------------ frame
    def _frame(self, t: float) -> bytes:
        out = bytearray(b"\x1b[?2026h")
        L, scene = self.layout, self.director.scene
        if L.mode != "tiny" and L.gate.w and L.gate.h:
            w, h = self.backend.pixel_size(L.gate)
            size = max(16, min(w, h))
            if self.gate is None or self.gate.S != size:
                self.gate = GateRenderer(size, self.font)
                self._gate_key = None
            key = self.gate.frame_key(scene, t)
            if key != self._gate_key:
                self._gate_key = key
                img = self.gate.render(scene, t)
                if img.size != (w, h):
                    img = img.resize((w, h))
                out += self.backend.show(1, img, L.gate)
            bw, bh = self.backend.pixel_size(L.bar)
            if self.bar is None or (self.bar.W, self.bar.H) != (bw, bh):
                self.bar = AddressBarRenderer(bw, bh, self.font)
                self._bar_key = None
            key = (scene.address, scene.locked, scene.incoming, scene.identified,
                   int(t * 4) if scene.spinning else -1, round(scene.dim, 1))
            if key != self._bar_key:
                self._bar_key = key
                bar = self.bar.render(scene, t)
                if scene.dim > 0:
                    bar = bar.point(lambda v: int(v * max(0.0, 1 - scene.dim)))
                out += self.backend.show(2, bar, L.bar)
        draw_panels(self.canvas, L, scene, list(self.logs), datetime.now(), t)
        out += self.canvas.render(self.caps.truecolor)
        out += b"\x1b[?2026l"
        return bytes(out)

    # ------------------------------------------------------------------ run
    def run(self) -> int:
        signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "_resized", True))
        parser = KeyParser()
        self.term.enter()
        try:
            self.caps = detect(self.term, self.cfg.graphics, os.environ)
            self.backend = make_backend(self.caps)
            self.font = find_font(self.cfg.font_path)
            self.director = Director(self.cfg, self.rng, AddressPicker(load_canon(), self.cfg.canon_ratio, self.rng),
                                     REGISTRY)
            if self.event:
                self.director.queue_event(self.event)
            self.mixer.start()
            self.log("SGC DIALING COMPUTER ONLINE")
            self.log(f"DISPLAY {self.backend.name.upper()} · GLYPHS {'FONT' if self.font else 'NUMBERS (font missing)'}")
            sound = "OFF" if self.mixer.dead else f"{self.cfg.sound_pack.upper()} PACK · VOL {round(self.mixer.volume * 100)}%"
            self.log(f"AUDIO {sound} · KEYS q quit  m mute  +/- vol  space skip  p pause")
            for w in self._startup_warnings:
                self.log(w.upper())
            start = last = time.monotonic()
            while not self._quit:
                frame_start = time.monotonic()
                self._handle_keys(parser.feed(self.term.read_available(0)))
                if self._quit:
                    break
                pre = b""
                if self._resized:
                    self._resized = False
                    pre = self._relayout()
                now = time.monotonic()
                dt = min(0.25, now - last)
                last = now
                if not self._paused:
                    new_logs, cues = self.director.advance(dt * self.cfg.speed)
                    for line in new_logs:
                        self.log(line)
                    self._play(cues)
                if self.director.finished:
                    break
                if self.duration is not None and now - start >= self.duration and not self.director.exiting:
                    self.director.begin_exit()
                self.term.write(pre + self._frame(now - start))
                cost = time.monotonic() - frame_start
                extra = self._adapt(cost, time.monotonic())
                if extra:
                    self.term.write(extra)
                wait = 1 / self.fps - (time.monotonic() - frame_start)
                if wait > 0:
                    self._handle_keys(parser.feed(self.term.read_available(wait)))
            return 0
        finally:
            self._cleanup()


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="sgc", description="Ambient Stargate SG-1 dialing computer for your terminal.")
    p.add_argument("--graphics", choices=["auto", "kitty", "sixel", "iterm", "blocks"])
    p.add_argument("--no-sound", action="store_true", help="run silently")
    p.add_argument("--pack", choices=["synth", "freesound"], help="sound pack")
    p.add_argument("--config", help="config file (default ~/.config/stargate-sgc/config.toml)")
    p.add_argument("--fps", type=int, help="target frame rate (5-60)")
    p.add_argument("--seed", type=int, help="random seed, for repeatable runs")
    p.add_argument("--event", choices=sorted(REGISTRY), help="play this event first")
    p.add_argument("--duration", type=float, help="run this many seconds, then shut down")
    p.add_argument("--exit-duration", type=float, help="length of the animated exit in seconds")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg, warnings = load_config(Path(args.config) if args.config else None)
    changes = {}
    if args.graphics:
        changes["graphics"] = args.graphics
    if args.pack:
        changes["sound_pack"] = args.pack
    if args.fps:
        changes["fps"] = max(5, min(60, args.fps))
    if args.exit_duration is not None:
        changes["exit_duration"] = max(0.0, min(60.0, args.exit_duration))
    if args.no_sound:
        changes["sound"] = False
    cfg = replace(cfg, **changes)
    if not os.isatty(0) or not os.isatty(1):
        print("sgc needs an interactive terminal", file=sys.stderr)
        return 2

    def terminate(*_):
        raise Terminated()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGHUP, terminate)

    mixer = Mixer(SoundBank(cfg.sound_pack), cfg.volume) if cfg.sound else NullMixer()
    app = App(Terminal(), cfg, mixer, random.Random(args.seed), args.event, args.duration, warnings)
    try:
        return app.run()
    except (Terminated, KeyboardInterrupt):
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
