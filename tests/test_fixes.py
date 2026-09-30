"""Regression tests for the final-review findings."""
import os
import pty
import random
import select
import time
import wave
from array import array

from sgc.audio.bank import SoundBank
from sgc.audio.mixer import Mixer
from sgc.config import Config
from sgc.events import REGISTRY
from sgc.model import Scene
from sgc.render.gate import GateRenderer
from sgc.term.keys import KeyParser
from sgc.term.screen import Terminal
from tests.test_director import run
from tests.test_events import ctx


# 1. a stray Alt-chord must not lock out the quit keys
def test_unterminated_string_times_out():
    kp = KeyParser()
    assert kp.feed(b"\x1bP", now=0.0) == []
    assert kp.feed(b"q", now=0.3) == ["q"]


def test_ctrl_c_aborts_pending_string():
    kp = KeyParser()
    assert kp.feed(b"\x1b]", now=0.0) == []
    assert kp.feed(b"\x03", now=0.01) == ["ctrl-c"]


def test_lone_escape_does_not_eat_next_key():
    kp = KeyParser()
    assert kp.feed(b"\x1b", now=0.0) == []
    assert kp.feed(b"", now=0.2) == ["escape"]
    assert kp.feed(b"m", now=0.21) == ["m"]


def test_a_lone_escape_is_a_key_once_nothing_follows_it():
    kp = KeyParser()
    assert kp.feed(b"\x1b", now=0.0) == [] and kp.feed(b"", now=0.05) == []
    assert kp.feed(b"q", now=0.3) == ["escape", "q"]           # a key long after the ESC is no Alt-chord
    kp.text = True
    assert kp.feed(b"\x1b", now=1.0) == [] and kp.feed(b"", now=1.2) == ["escape"]
    assert kp.feed(b"\x1b[A\x1bx", now=2.0) == ["up"]          # sequences and Alt-chords are still no Esc
    assert kp.feed(b"\x1b", now=3.0) == [] and kp.feed(b"[B", now=3.02) == ["down"]


def _sound_bank(tmp_path):
    p = tmp_path / "assets/synth/wormhole_hum.wav"
    p.parent.mkdir(parents=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(array("h", [1000] * 44100).tobytes())
    return SoundBank("synth", tmp_path / "user", tmp_path / "assets")


# 2. a hung player must not block shutdown
def test_close_with_stalled_player_is_quick(tmp_path):
    m = Mixer(_sound_bank(tmp_path), 0.5, player_cmd=["sleep", "30"])
    m.start()
    m.play("wormhole_hum", loop=True)
    time.sleep(1.2)
    t0 = time.monotonic()
    m.close()
    assert time.monotonic() - t0 < 2.0


def test_cleanup_restores_terminal_even_if_mixer_close_fails():
    from sgc.app import App

    class FakeTerm:
        left = False

        def write(self, b):
            pass

        def leave(self):
            FakeTerm.left = True

    class BadMixer:
        def close(self):
            raise RuntimeError("boom")

    app = App(FakeTerm(), Config(), BadMixer(), random.Random(1))
    try:
        app._cleanup()
    except RuntimeError:
        pass
    assert FakeTerm.left


# 3. adaptive frame rate must recover after a load spike
def test_adapt_steps_down_then_recovers():
    from sgc.app import App

    class B:
        scale = 1.0
    app = App(None, Config(), None, random.Random(1))
    app.backend = B()
    app._relayout = lambda: b"R"
    t = 0.0
    while t < 4:
        app._adapt(0.08, t)          # 80 ms frames: over budget
        t += 0.1
    assert app.fps < 24
    while t < 40:
        app._adapt(0.005, t)         # 5 ms frames: plenty of headroom
        t += 0.1
    assert app.fps == 24 and app.backend.scale == 1.0


# 4. audio pacing follows what the player has consumed, not the wall clock
def test_mixer_paces_by_pipe_fill(tmp_path):
    m = Mixer(_sound_bank(tmp_path), 0.5, player_cmd=["sleep", "30"])
    m.start()
    m.play("wormhole_hum", loop=True)
    time.sleep(1.0)
    written = m.written
    m.close()
    assert written < 20000        # ~100 ms queued, not a wall-clock second (88 KB)


# 5. the data screen is cleared between events
def test_data_panel_cleared_after_event():
    s = Scene()
    run(REGISTRY["science"].build(ctx(s)), s)
    assert s.panel_rows == [] and s.panel_trace == [] and "SCIENCE" not in s.panel_title


# 6. unchanged gate frames are not re-rendered
def test_gate_frame_key_stable_when_idle_and_changes_when_open():
    g = GateRenderer(64, None)
    assert g.frame_key(Scene(), 1.0) == g.frame_key(Scene(), 1.33)
    assert g.frame_key(Scene(horizon="open"), 1.0) != g.frame_key(Scene(horizon="open"), 1.1)
    assert g.frame_key(Scene(ring_angle=10), 1.0) != g.frame_key(Scene(ring_angle=11), 1.0)


# 7. leaving the screen cancels any half-sent sequence and ends synchronized output
def test_leave_cancels_partial_sequences():
    master, slave = pty.openpty()
    t = Terminal(fd_in=slave, fd_out=slave)
    t.enter()
    t.leave()
    out = b""
    while select.select([master], [], [], 0.1)[0]:
        out += os.read(master, 4096)
    tail = out[out.index(b"\x1b[?25l"):]
    assert b"\x18" in tail and b"\x1b[?2026l" in tail
    assert tail.index(b"\x1b[?2026l") < tail.index(b"\x1b[?1049l")
