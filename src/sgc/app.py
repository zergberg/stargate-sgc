"""sgc: the SGC dialing computer. Main loop, keys, adaptive frame rate, resize, exit and cleanup."""
from __future__ import annotations

import argparse
import math
import os
import random
import shutil
import signal
import subprocess
import sys
import time
import traceback
from collections import deque
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PIL import Image

from . import sequences as sq
from .addresses import AddressPicker, load_canon
from .audio.bank import SoundBank
from .audio.mixer import Mixer, NullMixer
from .config import Config, load_config, save_setting
from .director import Director
from .events import REGISTRY
from .game import clock, scoring, screens
from .game import content as game_content
from .game.database import Database
from .game.engine import Engine
from .game.menu import Menu
from .game.room import CANCEL_KEYS, Room
from .game.save import Saves
from .game.state import Campaign, new_campaign
from .glyphs import find_font, install_font
from .layout import compute_layout
from .model import Step, _teams
from .panels import draw_panels
from .render.addressbar import AddressBarRenderer
from .render.briefing import BriefingRenderer, render_transition
from .render.gate import GateRenderer
from .term.canvas import Canvas
from .term.detect import detect
from .term.graphics import make_backend
from .term.keys import KeyParser
from .term.screen import Terminal

CUE_GAIN = {"wormhole_hum": 0.45, "ring_spin": 0.6, "klaxon": 0.5, "kawoosh": 1.0}
FPS_STEPS = (24, 15, 10)
SCALE_STEPS = (1.0, 0.8, 0.65, 0.5)
GATE_KEYS = [("b", "BRIEFING"), ("d", "DATABASE"), ("1-9", "ORDERS"), ("?", "HELP"), ("q", "SAVE & QUIT")]
GRACE = 1.0                   # after an alarm interrupts typing, keys are held until this long without one
ROOM_KEYS = [("↑↓ ⏎", "CHOOSE"), ("b", "GATE ROOM"), ("d", "DATABASE"), ("?", "HELP"), ("q", "BACK")]


class Terminated(Exception):
    pass


class App:
    def __init__(self, term: Terminal, cfg: Config, mixer, rng: random.Random, event: str | None = None,
                 duration: float | None = None, warnings: list[str] | None = None, start: str = "ambient",
                 saves: Saves | None = None, config_path: Path | None = None):
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
        self.start = start                    # "menu" | "missions" | "ambient"
        self.saves = saves or Saves()
        self.mode = "ambient"                 # "menu" | "ambient" | "game"
        self.menu: Menu | None = None
        self.engine: Engine | None = None
        self.view = "gate"                    # in a game: "gate" | "briefing" | "database"
        self._db_from = "gate"
        self.db: Database | None = None
        self._kept_db: Database | None = None  # an alarm closed the Database: d reopens it just as it was
        self.room: Room | None = None
        self.legend = cfg.legend
        self.config_path = config_path
        self.parser = KeyParser()
        self.briefing: BriefingRenderer | None = None
        self._records: list[dict] = []
        self._prompt_seen = None
        self._last_tick: int | None = None
        self._grace_until = 0.0               # swallow keys until then (an alarm cut into typing)
        self._hold_told = False               # "typing held" was logged for this hold
        self._open_told = None                # the alarm prompt whose "give an order first" was logged
        self._walking = False                 # a walk between the rooms is queued or playing
        self._walk_behind = False             # ... and it waits behind a gate scene
        self._walk_seq = 0
        self._walk_told = 0                   # the walk whose "q again on arrival" was logged
        self._resume_room = False             # an alarm pulled the player out of the briefing room
        self._children: list = []             # notify-send processes still to reap

    # ------------------------------------------------------------------ helpers
    def log(self, line: str) -> None:
        when = clock.short(self.engine.c.minutes) if self.engine is not None else f"{datetime.now():%H:%M:%S}"
        self.logs.append(f"{when}  {line}")

    @property
    def fps(self) -> int:
        return self._fps_steps[self._fps_i]

    def _relayout(self) -> bytes:
        cols, rows, cw, ch = self.term.size()
        self.caps = replace(self.caps, cell_w=cw, cell_h=ch)
        self.backend.caps = self.caps
        self.layout = compute_layout(cols, rows, cw, ch)
        if self.db is not None:               # a resize into compact closes a half-open ORDERS panel: it
            self.db.set_compact(self.layout.mode == "compact")   # needs a full-size pane
        self.canvas = Canvas(cols, rows)
        if not (self.mode == "game" and self.view == "database"):     # the Database has the whole screen
            self.canvas.set_holes([self.layout.gate, self.layout.bar])
        self.gate = None
        self.bar = None
        self._bar_key = None
        self._gate_key = None
        return self.backend.forget() + b"\x1b[0m\x1b[2J"

    def _text_mode(self) -> bool:
        if self.mode != "game":
            return False
        if self.view == "database" and self.db is not None:
            return self.db.text_mode
        return self.view == "briefing" and self.room is not None and self.room.text_mode

    def _typing(self) -> bool:
        """Read keys as text: a note or a search is open, or typing is held after an alarm cut into one
        (so every letter counts as typing, not only the ones that are also commands)."""
        return self._text_mode() or (self.mode == "game" and time.monotonic() < self._grace_until)

    def _handle_keys(self, keys: list[str]) -> None:
        for k in keys:
            if self.mode == "game" and self._held(k):
                continue                      # keys typed for a note or a search, not for the alarm
            if self.mode == "game" and not self.director.exiting and (k != "ctrl-c" or self._text_mode()) \
                    and self._game_key(k):
                continue                      # Ctrl+C reaches the game only to cancel a note or a search
            if k in ("q", "ctrl-c"):
                if k == "q" and self.mode == "menu" and self.menu.screen != "main" and not self.director.exiting:
                    self.menu.back()
                    continue
                if self.engine is not None:
                    self.engine.save_now()
                if self.director.exiting:
                    self._quit = True
                else:
                    if self._paused:                  # let the exit animation play
                        self._paused = False
                        self.mixer.pause(False)
                    self.director.begin_exit()
            elif k == "m":
                muted = self.mixer.toggle_mute()
                self.log("AUDIO MUTED" if muted else "AUDIO ON")
            elif k in ("+", "-"):
                self.mixer.set_volume(self.mixer.volume + (0.1 if k == "+" else -0.1))
                self.log(f"VOLUME {round(self.mixer.volume * 100)}%")
            elif self.mode == "menu":
                if not self.director.exiting:
                    self._menu_action(self.menu.key(k))
            elif k == "p" and (self.mode == "ambient" or self.engine.c.difficulty == "recruit"):
                if not self.director.exiting:
                    self._paused = not self._paused
                    self.mixer.pause(self._paused)
                    self.log("PAUSED" if self._paused else "RESUMED")
            elif k == "space" and self.mode == "ambient":
                self.director.skip()

    def _held(self, k: str) -> bool:
        """An alarm cut into typing: swallow keys until the player stops typing for GRACE seconds (each
        key restarts it) or presses Enter. Ctrl+C or Esc ends the hold too, cancelling the note."""
        now = time.monotonic()
        if now >= self._grace_until:
            return False
        if k in ("enter", *CANCEL_KEYS):
            self._grace_until = 0.0
            if k in CANCEL_KEYS and self._resume_room and self.room is not None and self.room.text_mode:
                self.room.key(k)
            if k in CANCEL_KEYS and self._kept_db is not None and self._kept_db.text_mode:
                self._kept_db.key(k)
        else:
            self._grace_until = now + GRACE
        if not self._hold_told:
            self._hold_told = True
            self.log("ALARM — TYPING HELD TILL YOU STOP")
        return True

    def _game_key(self, k: str) -> bool:
        """Keys in a campaign; returns True if used (False lets q quit, m mute and so on)."""
        e = self.engine
        if e.ended:
            return e.key(k)
        if self.view == "database":
            if k in ("?", "m", "+", "-") and not self.db.text_mode:
                self.db.disarm()                  # any key but x disarms a cancel, even one the app handles
                if k != "?":
                    return False
                self._cycle_legend()
                return True
            act = self.db.key(k)
            if act == ("close",):
                self._close_database()
            elif act is not None:
                self._queue_action(act)
            return True
        if self._text_mode():
            self.room.key(k)
            return True
        if k == "?":
            self._cycle_legend()
            return True
        if k in ("d", "b"):
            if self.view == "gate" and (prompt := e.prompt) is not None:
                if self._open_told is not prompt:           # answer the alarm before leaving it
                    self._open_told = prompt
                    self.log("ALARM OPEN — GIVE AN ORDER FIRST")
                return True
            if k == "d":
                self._open_database()
            else:
                self._walk("gate" if self.view == "briefing" else "briefing")
            return True
        if self.view == "briefing":
            if k in ("m", "+", "-"):
                return False
            if self.room.key(k) == ("close",):
                self._walk("gate")
            return True
        if k == "q" and self._walking:            # still walking down: not yet time to quit
            if self._walk_told != self._walk_seq:
                self._walk_told = self._walk_seq
                self.log("WALKING — Q AGAIN ON ARRIVAL")
            return True
        return e.key(k)

    def _gate_busy(self) -> bool:
        """The engine owns the gate: an alarm is pending, or real traffic is queued or playing (a walk, the
        ambient scene or its cut don't count)."""
        return bool(self.engine.c.alarms) or self.engine.prompt is not None or self.engine.showing

    def _legend_busy(self) -> bool:
        """The side panel holds something the full legend mustn't cover."""
        return self.view == "briefing" or (self.engine is not None and self.engine.prompt is not None)

    def _cycle_legend(self) -> None:
        self.legend = screens.next_legend(self.legend)
        save_setting("legend", self.legend, self.config_path)

    def _open_database(self) -> None:
        self.db, self._kept_db = self._kept_db or Database(self.engine.c, self.engine.schedule_view, self.engine), None
        self.db.set_compact(self.layout.mode == "compact")  # the layout may have changed while kept
        self.db.disarm()                          # a cancel is confirmed in one sitting
        self._db_from, self.view = self.view, "database"
        self.canvas.set_holes([])
        self.canvas.invalidate()
        self.term.write(self.backend.forget() + b"\x1b[0m\x1b[2J")

    def _close_database(self) -> None:
        self.view, self.db = self._db_from, None
        self._resized = True                      # relayout: the gate and the address bar come back

    def _queue_action(self, act: tuple) -> None:
        """The QUEUE tab asked to cancel or move a dial-out: the engine answers, and the reply shows on the tab."""
        kind, item = act[0], act[1]
        if kind == "cancel":
            self.db.message = self.engine.cancel(item, confirm=act[2])
        elif kind == "move":
            self.db.message = self.engine.move(item, act[2])
        self.db.select(item)                      # the selection follows a moved row

    def _walk(self, to: str) -> None:
        """Walk between the rooms. Only an idle gate is interrupted; otherwise the walk waits for
        what the gate is showing, so live traffic and alarms play out."""
        t = self.cfg.transition_seconds
        if to == "briefing":
            if not (self._resume_room and self.room is not None):
                self.room = Room(self.engine)
            self.view = "briefing"
            steps = sq.to_briefing(t)
        else:
            self.view = "gate"
            steps = sq.to_gateroom(t)
        self._resume_room = False
        s = self.director.scene
        up = s.horizon != "off" or bool(s.locked)      # a live wormhole is never shut by a walk
        self._set_off(steps, self._gate_busy() or up)

    def _set_off(self, steps: list[Step], behind: bool) -> None:
        """Play a walk: queued behind what the gate is showing, or at once, cutting an idle gate."""
        self._walk_seq += 1
        seq = self._walk_seq

        def arrived(scene, p):
            if seq == self._walk_seq:
                self._walking = False
        steps = [*steps, Step(0, arrived)]
        if behind:
            self.director.run_steps(steps)
        else:
            self.director.cut_to(steps, auto=False)
        self._walking, self._walk_behind = True, behind

    def _alarm(self, title: str, text: str) -> None:
        """An urgent event: the bell, a desktop notification, the klaxon, and back to the gate room."""
        if self._text_mode():
            self._grace_until = time.monotonic() + GRACE
            self._hold_told = False
        self.term.write(b"\x07")
        self.mixer.play("klaxon", gain=CUE_GAIN["klaxon"])
        self._reap()
        if self.cfg.notify and shutil.which("notify-send"):
            try:
                self._children.append(subprocess.Popen(
                    ["notify-send", "-a", "sgc", f"SGC: {title}", text],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            except OSError:
                pass
        self._back_to_the_gate_room()

    def _reap(self) -> None:
        """Collect finished notify-send processes, so none is left a zombie."""
        if self._children:
            self._children = [p for p in self._children if p is not None and p.poll() is None]

    def _back_to_the_gate_room(self) -> None:
        if self.view == "database":
            kept = self.db
            kept.close_orders()               # Part 9: a half-finished order is dropped
            self._close_database()
            self._kept_db = kept              # d reopens it: tab, search and scroll as they were
        if self.view == "briefing":
            self._walk("gate")
            self._resume_room = True          # b goes back to the same room, a half-typed note and all

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

    # ------------------------------------------------------------------ modes
    def _menu_action(self, action: tuple | None) -> None:
        if not action:
            return
        if action[0] == "quit":
            self.director.begin_exit()
        elif action[0] == "ambient":
            self.mode = "ambient"
            self.log("AMBIENCE · KEYS q quit  m mute  +/- vol  space skip  p pause")
            self.director.cut_to(sq.to_gateroom(self.cfg.transition_seconds), auto=True)
        elif action[0] == "continue":
            c, notice = self.saves.load()
            if c is None:
                self.menu.has_save, self.menu.sel = False, 0
                self.menu.notice = notice or "NO SAVED GAME"
            else:
                self._start_game(c)
                if notice:
                    self.log(notice)
        elif action[0] == "new":
            _, mode, difficulty, pace = action
            self._start_game(new_campaign(mode, difficulty, self.rng.randrange(2 ** 31), pace))

    def _start_game(self, c: Campaign) -> None:
        try:
            scenarios, warnings = game_content.load()
        except game_content.ContentError as e:
            self.menu.notice = f"SCENARIO ERROR: {e}"
            return
        for w in warnings:
            self.log(w.upper())
        engine = Engine(c, scenarios, self.director, self.cfg.game_pace,
                        save=self.saves.save, on_end=self._game_ended, on_victory=self._game_won, log=self.log, on_alarm=self._alarm)
        self.engine, self.mode, self.view = engine, "game", "gate"
        engine.save_now()
        pace = (f"PACE {self.cfg.game_pace} S/HOUR (CONFIG)" if self.cfg.game_pace is not None
                else f"{c.pace.upper()} PACE")
        self.log(f"{c.mode.upper()} · {c.difficulty.upper()} · {pace}")
        self.log("KEYS b briefing  d database  ? help  q save & quit")
        self._resume_room = False
        self._set_off(sq.to_gateroom(self.cfg.transition_seconds), behind=False)

    def _file_record(self, c: Campaign) -> None:
        try:
            self.saves.add_record(scoring.record(c))
        except OSError as e:
            self.log(f"RECORD NOT SAVED — {(e.strerror or type(e).__name__).upper()[:40]}")

    def _game_won(self, c: Campaign) -> None:
        """Victory goes into the hall of records at once; the campaign may carry on."""
        self._file_record(c)
        self.log("VICTORY — FILED IN THE HALL OF RECORDS")

    def _game_ended(self, c: Campaign) -> None:
        self._back_to_the_gate_room()
        if c.won is None:                                   # a won campaign was filed when it was won
            self._file_record(c)
        try:
            self.saves.delete()
        except OSError as e:
            self.log(f"OLD SAVE NOT REMOVED — {(e.strerror or type(e).__name__).upper()[:40]}")

    def _to_menu(self) -> None:
        self.engine, self.db, self._kept_db, self.room, self.view = None, None, None, None, "gate"
        self._walking = self._resume_room = False
        self.director.scene.prompt = None
        self.director.scene.teams = _teams()
        self.mode = "menu"
        self.menu = Menu(self.saves.exists())
        self._records = self.saves.records()
        self.director.cut_to(sq.to_briefing(self.cfg.transition_seconds), auto=True)

    def _prompt_sounds(self) -> None:
        p = self.director.scene.prompt
        if p is not self._prompt_seen:
            self._prompt_seen, self._last_tick = p, None
            if p is not None:
                self.mixer.play("decision")
        if p is not None and p.total and p.remaining <= 5:
            sec = math.ceil(p.remaining)
            if sec > 0 and sec != self._last_tick:
                self._last_tick = sec
                self.mixer.play("countdown_tick")

    def _step(self, dt: float) -> None:
        """One frame's worth of time: the animation (at the ambient speed) and the SGC clock (never scaled)."""
        if not self._paused:
            new_logs, cues = self.director.advance(dt * self.cfg.speed)
            for line in new_logs:
                self.log(line)
            self._play(cues)
            if self._walking and self.director.idle:  # the walk was thrown away: arrive anyway
                self._walking = self._walk_behind = False
                self.director.scene.view_p = 1.0 if self.view == "gate" else 0.0
            if self.engine is not None and not self.director.exiting:
                self.engine.update(dt)
                if self.engine.finished:
                    self._to_menu()
        self._prompt_sounds()
        self._reap()

    def _room(self, gate_img, p: float):
        size = gate_img.size[0]
        if self.briefing is None or self.briefing.S != size:
            self.briefing = BriefingRenderer(size)
        room = self.briefing.render(gate_img)
        if p <= 0.001:
            return room
        if self.backend.name == "blocks" or self.layout.mode != "full":
            return Image.blend(room, gate_img, p)
        return render_transition(room, gate_img, p, self.briefing.zoom_box)

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
        if self.mode == "game" and self.view == "database" and L.mode != "tiny":
            screens.draw_database(self.canvas, L, self.db, self.legend)
            return bytes(out + self.canvas.render(self.caps.truecolor) + b"\x1b[?2026l")
        if L.mode != "tiny" and L.gate.w and L.gate.h:
            w, h = self.backend.pixel_size(L.gate)
            size = max(16, min(w, h))
            if self.gate is None or self.gate.S != size:
                self.gate = GateRenderer(size, self.font)
                self._gate_key = None
            key = (self.gate.frame_key(scene, t), round(scene.view_p, 3))
            if key != self._gate_key:
                self._gate_key = key
                img = self.gate.render(scene, t)
                if scene.view_p < 0.999:
                    img = self._room(img, scene.view_p)
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
        if self.mode == "menu" and self.menu is not None:
            screens.draw_menu(self.canvas, L, self.menu, self._records)
        elif self.mode == "game" and self.engine is not None:
            c = self.engine.c
            if self.view == "briefing" and not self.engine.ended:
                screens.draw_room(self.canvas, L, self.room, self.engine.schedule_view(), scene)
                screens.draw_legend(self.canvas, L, self.legend, ROOM_KEYS, busy=True)
            else:
                queue = self.engine.schedule_view() if not self.engine.ended else None
                screens.draw_game(self.canvas, L, scene, c, t, queue)
                screens.draw_legend(self.canvas, L, self.legend, GATE_KEYS, busy=self._legend_busy())
            screens.draw_header(self.canvas, L, c, self.engine.alarm_title, t)
        screens.draw_room_label(self.canvas, L, scene)
        out += self.canvas.render(self.caps.truecolor)
        out += b"\x1b[?2026l"
        return bytes(out)

    # ------------------------------------------------------------------ run
    def run(self) -> int:
        signal.signal(signal.SIGWINCH, lambda *_: setattr(self, "_resized", True))
        parser = self.parser
        self.term.enter()
        try:
            self.caps = detect(self.term, self.cfg.graphics, os.environ)
            self.backend = make_backend(self.caps)
            self.font = find_font(self.cfg.font_path)
            self.director = Director(self.cfg, self.rng, AddressPicker(load_canon(), self.cfg.canon_ratio, self.rng),
                                     REGISTRY)
            if self.event:
                self.director.queue_event(self.event)
            if self.start != "ambient":
                self.mode = "menu"
                self.director.scene.view_p = 0.0
                self.menu = Menu(self.saves.exists(), "missions" if self.start == "missions" else "main")
                self._records = self.saves.records()
            self.mixer.start()
            self.log("SGC DIALING COMPUTER ONLINE")
            self.log(f"DISPLAY {self.backend.name.upper()} · GLYPHS {'FONT' if self.font else 'NUMBERS (font missing)'}")
            sound = "OFF" if self.mixer.dead else f"{self.cfg.sound_pack.upper()} PACK · VOL {round(self.mixer.volume * 100)}%"
            keys = ("KEYS q quit  m mute  +/- vol  space skip  p pause" if self.mode == "ambient"
                    else "KEYS 1-9 or ↑↓ enter  q back/quit  m mute")
            self.log(f"AUDIO {sound} · {keys}")
            for w in self._startup_warnings:
                self.log(w.upper())
            start = last = time.monotonic()
            while not self._quit:
                frame_start = time.monotonic()
                parser.text = self._typing()
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
                self._step(dt)
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
                    parser.text = self._typing()
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
    p.add_argument("--install-font", nargs="?", const="", metavar="PATH",
                   help="install the downloaded glyph font (.ttf or .zip; default: look in ~/Downloads)")
    start = p.add_mutually_exclusive_group()
    start.add_argument("--ambient", action="store_true", help="skip the menu and run the ambient dialing computer")
    start.add_argument("--missions", action="store_true", help="skip to the missions menu")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.install_font is not None:
        try:
            dest = install_font(Path(args.install_font) if args.install_font else None)
        except ValueError as e:
            print(f"sgc: {e}", file=sys.stderr)
            return 1
        print(f"Glyph font installed at {dest}")
        return 0
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
    start = "ambient" if args.ambient or args.event else "missions" if args.missions else "menu"
    app = App(Terminal(), cfg, mixer, random.Random(args.seed), args.event, args.duration, warnings,
              start=start, config_path=Path(args.config) if args.config else None)
    try:
        return app.run()
    except (Terminated, KeyboardInterrupt):
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
