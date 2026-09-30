import fcntl
import os
import pty
import random
import struct
import termios
from dataclasses import replace

import pytest

from sgc import app as app_mod
from sgc.addresses import AddressPicker, load_canon
from sgc.app import App
from sgc.config import Config, load_config
from sgc.director import Director
from sgc.events import REGISTRY
from sgc.game import clock
from sgc.game import engine as eng
from sgc.game import save as save_mod
from sgc.game.menu import Menu
from sgc.game.save import Saves
from sgc.layout import compute_layout
from sgc.model import _teams
from sgc.term.canvas import Canvas
from tests.test_app import PY, drain, wait


def spawn_home(args, home, cols=100, rows=30):
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["HOME"] = str(home)
        os.execv(PY, [PY, "-m", "sgc.app", "--no-sound", "--seed", "1", "--graphics", "blocks", *args])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, cols * 9, rows * 18))
    return pid, fd


def fast_walks(tmp_path):
    cfg = tmp_path / ".config" / "stargate-sgc"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text("transition_seconds = 2\nnotify = false\n")
    return tmp_path


def quit_twice(fd, pid):
    os.write(fd, b"q")
    drain(fd, 1.0)
    os.write(fd, b"q")
    assert wait(pid) == 0


def new_game(fd):
    out = b""
    for k in (b"1", b"1", b"2", b"2"):            # new game, campaign, officer, standard pace
        os.write(fd, k)
        out += drain(fd, 0.6)
    return out + drain(fd, 4.0)                   # the canvas only redraws cells that change


def test_menu_then_ambience(tmp_path):
    home = fast_walks(tmp_path)
    pid, fd = spawn_home([], home)
    out = drain(fd, 2.5)
    assert b"AMBIENCE" in out and b"MISSIONS" in out and b"BRIEFING ROOM" in out
    os.write(fd, b"1")
    out = drain(fd, 3.5)
    assert b"HEADING DOWN" in out
    quit_twice(fd, pid)


def test_new_campaign_starts_and_saves_on_quit(tmp_path):
    home = fast_walks(tmp_path)
    pid, fd = spawn_home(["--missions"], home)
    assert b"NEW GAME" in drain(fd, 2.5)
    out = new_game(fd)
    assert b"SGC STATUS" in out and b"HEADING DOWN" in out and b"DAY 1" in out and b"GATE QUEUE" in out
    quit_twice(fd, pid)
    assert (home / ".local" / "share" / "stargate-sgc" / "campaign.json").is_file()


def test_database_and_help_through_a_pty(tmp_path):
    home = fast_walks(tmp_path)
    pid, fd = spawn_home(["--missions"], home)
    drain(fd, 2.5)
    new_game(fd)
    os.write(fd, b"d")
    out = drain(fd, 1.5)
    assert b"SGC DATABASE" in out and b"Abydos" in out and b"FILTER" in out
    os.write(fd, b"q")
    out = drain(fd, 1.5)
    assert b"SGC STATUS" in out
    os.write(fd, b"?")
    out = drain(fd, 1.5)
    assert b"give an order" in out and b"mute, volume" in out
    quit_twice(fd, pid)
    cfg, _ = load_config(home / ".config" / "stargate-sgc" / "config.toml")
    assert cfg.legend == "full"


def test_ambient_flag_skips_the_menu(tmp_path):
    pid, fd = spawn_home(["--ambient", "--duration", "2", "--exit-duration", "1"], tmp_path)
    out = drain(fd, 6)
    assert wait(pid) == 0 and b"AMBIENCE" not in out and b"SGC" in out


def test_event_flag_implies_ambient(tmp_path):
    pid, fd = spawn_home(["--event", "code_red", "--duration", "2", "--exit-duration", "1"], tmp_path)
    out = drain(fd, 6)
    assert wait(pid) == 0 and b"AMBIENCE" not in out


# ---------------------------------------------------------------- in-process
class QuietMixer:
    volume, dead = 0.5, False

    def __init__(self):
        self.paused, self.played = [], []

    def pause(self, p):
        self.paused.append(p)

    def play(self, name, **kw):
        self.played.append(name)

    def __getattr__(self, name):
        return lambda *a, **k: None


class FakeTerm:
    def __init__(self):
        self.out = bytearray()

    def write(self, data):
        self.out += data


class FakeBackend:
    def forget(self):
        return b""


def make_app(tmp_path, start, **changes):
    cfg = replace(Config(), transition_seconds=2, exit_duration=1.0, **changes)
    app = App(FakeTerm(), cfg, QuietMixer(), random.Random(1), start=start, saves=Saves(tmp_path),
              config_path=tmp_path / "config.toml")
    app.director = Director(app.cfg, app.rng, AddressPicker(load_canon(), app.cfg.canon_ratio, app.rng), REGISTRY)
    app.backend, app.layout, app.canvas = FakeBackend(), compute_layout(100, 30, 9, 18), Canvas(100, 30)
    if start != "ambient":
        app.mode, app.director.scene.view_p = "menu", 0.0
        app.menu = Menu(app.saves.exists(), "missions" if start == "missions" else "main")
    return app


def tick(app, dt=1 / 30):
    """The run loop's per-frame logic, without drawing."""
    app._step(dt)


def run_until(app, secs, done, dt=1 / 30):
    for _ in range(int(secs / dt)):
        tick(app, dt)
        if done(app):
            return True
    return False


def start(tmp_path, **changes):
    app = make_app(tmp_path, "missions", **changes)
    app._handle_keys(["1", "1", "1", "2"])            # new game, campaign, recruit, standard
    assert app.mode == "game" and app.engine is not None
    return app


def raise_alarm(app):
    c = app.engine.c
    c.events.push(c.now, "incoming")
    assert run_until(app, 5, lambda a: a.engine.prompt is not None)


class FakeProc:
    def __init__(self, args):
        self.args, self.done = args, False

    def poll(self):
        return 0 if self.done else None


def fake_popen(calls):
    def popen(args, **kw):
        calls.append(args)
        return FakeProc(args)
    return popen


def typed(app, s):
    for ch in s:
        app.parser.text = app._typing()
        app._handle_keys(app.parser.feed(ch.encode()))


def test_a_new_game_saves_and_logs_game_time(tmp_path):
    app = start(tmp_path)
    assert (tmp_path / "campaign.json").is_file() and app.engine.c.pace == "standard"
    assert any(line.startswith("D1 08:00  CAMPAIGN · RECRUIT · STANDARD PACE") for line in app.logs)
    assert all(len(line) <= 76 for line in app.logs)


def test_the_start_line_shows_a_pace_set_in_the_config(tmp_path):
    app = start(tmp_path, game_pace=5)
    assert any(line.startswith("D1 08:00  CAMPAIGN · RECRUIT · PACE 5 S/HOUR (CONFIG)") for line in app.logs)


def test_the_game_clock_ignores_the_animation_speed(tmp_path):
    app = start(tmp_path, speed=4.0)
    m0 = app.engine.c.minutes
    tick(app, 0.25)
    assert abs(app.engine.c.minutes - m0 - clock.to_minutes(0.25, app.engine.sph)) < 1e-6


def test_game_keys_are_ignored_while_shutting_down(tmp_path):
    app = start(tmp_path)
    assert run_until(app, 10, lambda a: not a._walking)     # down in the gate room: q quits
    raise_alarm(app)
    prompt = app.engine.prompt
    app._handle_keys(["q"])
    saved = (tmp_path / "campaign.json").read_text()
    app._handle_keys(["1"])
    assert app.engine.prompt is prompt and app.engine.c.alarms
    assert run_until(app, 10, lambda a: a.director.finished)
    assert (tmp_path / "campaign.json").read_text() == saved


def test_an_alarm_rings_notifies_and_returns_to_the_gate_room(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(app_mod.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(app_mod.subprocess, "Popen", fake_popen(calls))
    app = start(tmp_path)
    app._handle_keys(["b"])
    assert app.view == "briefing"
    raise_alarm(app)
    assert b"\x07" in app.term.out and "klaxon" in app.mixer.played
    assert calls and calls[0][:3] == ["notify-send", "-a", "sgc"] and calls[0][3].startswith("SGC: INCOMING")
    assert app.view == "gate"


def test_finished_notifications_are_reaped(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(app_mod.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(app_mod.subprocess, "Popen", fake_popen(calls))
    app = start(tmp_path)
    raise_alarm(app)
    assert len(app._children) == 1
    tick(app)
    assert len(app._children) == 1
    app._children[0].done = True
    tick(app)
    assert app._children == []


def test_no_desktop_notification_when_turned_off(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(app_mod.shutil, "which", lambda name: "/usr/bin/notify-send")
    monkeypatch.setattr(app_mod.subprocess, "Popen", fake_popen(calls))
    app = start(tmp_path, notify=False)
    raise_alarm(app)
    assert b"\x07" in app.term.out and calls == []


def test_the_database_opens_over_everything_and_q_closes_it(tmp_path):
    app = start(tmp_path)
    app._handle_keys(["d"])
    assert app.view == "database" and app.db is not None and app.canvas._holes == []
    app._handle_keys(["right", "q"])
    assert app.view == "gate" and app.db is None and app._resized and app.mode == "game"
    app._handle_keys(["d", "/"])
    assert app._text_mode()
    app._handle_keys(["ch:q", "enter", "q"])
    assert app.view == "gate" and not app.director.exiting


def test_the_legend_cycles_and_is_remembered(tmp_path):
    app = start(tmp_path)
    assert app.legend == "bar"
    app._handle_keys(["?"])
    assert app.legend == "full" and load_config(tmp_path / "config.toml")[0].legend == "full"
    app._handle_keys(["?", "?"])
    assert app.legend == "bar"


def test_the_briefing_room_orders_a_probe_and_q_walks_back(tmp_path):
    app = start(tmp_path)
    app._handle_keys(["b"])
    assert app.view == "briefing" and app.room is not None
    app._handle_keys(["1", "2", "1"])                 # dialing list, second address, MALP probe
    assert app.room.notice.startswith("MALP QUEUED") and app.engine.c.stock["malp"] == 3
    app._handle_keys(["5"])
    assert app._text_mode()
    app._handle_keys(["ch:h", "ch:i", "enter"])
    assert list(app.engine.c.worlds.values())[1].notes[-1][1] == "hi"
    for k in ("q", "q", "q"):
        app._handle_keys([k])
    assert app.view == "gate" and not app.director.exiting


def test_the_fall_of_the_base_closes_the_database_and_records_the_run(tmp_path):
    app = start(tmp_path)
    app._handle_keys(["d"])
    app.engine.c.over = "The SGC was overrun."
    app.engine._game_over()
    assert app.view == "gate" and not (tmp_path / "campaign.json").exists()
    assert app.saves.records()[0]["result"] == "overrun"
    app._handle_keys(["1"])
    tick(app)
    assert app.mode == "menu" and app.engine is None


def test_returning_to_the_menu_resets_the_team_labels(tmp_path):
    app = start(tmp_path)
    app.engine.c.teams["SG-2"].status = "captured"
    tick(app)
    assert app.director.scene.teams["SG-2"] == "CAPTURED"
    app._to_menu()
    assert app.director.scene.teams == _teams() and app.engine is None


def test_quit_while_paused_unpauses_so_the_exit_plays(tmp_path):
    app = make_app(tmp_path, "ambient")
    tick(app)
    app._handle_keys(["p"])
    assert app._paused
    app._handle_keys(["q"])
    assert not app._paused and app.mixer.paused == [True, False]
    assert run_until(app, 10, lambda a: a.director.finished)


def test_p_is_ignored_during_the_exit_animation(tmp_path):
    app = make_app(tmp_path, "ambient")
    tick(app)
    app._handle_keys(["q"])
    assert app.director.exiting
    app._handle_keys(["p"])
    assert not app._paused and app.mixer.paused == []
    assert run_until(app, 10, lambda a: a.director.finished)


def test_keys_typed_into_a_note_or_search_never_answer_an_alarm(tmp_path):
    for where in ("note", "search"):
        app = start(tmp_path / where)
        app._handle_keys(["b", "1", "2", "5"] if where == "note" else ["d", "/"])
        typed(app, "abc")
        assert app._text_mode()
        raise_alarm(app)
        assert app.view == "gate" and not app._text_mode()
        typed(app, "1 quiet")
        assert app.engine.c.alarms and not app.director.exiting and app.view == "gate"
        app._grace_until = 0.0                    # the grace period is over: keys work again
        app._handle_keys(["1"])
        assert not app.engine.c.alarms


def test_a_half_typed_note_survives_an_alarm(tmp_path):
    app = start(tmp_path)
    app._handle_keys(["b", "1", "2", "5"])
    typed(app, "hi")
    room = app.room
    raise_alarm(app)
    app._grace_until = 0.0
    app._handle_keys(["b"])
    assert app.view == "gate" and room.note == "hi"          # answer the alarm first
    app._handle_keys(["1", "b"])
    assert app.room is room and app._text_mode() and room.note == "hi"


def test_walking_while_an_answered_alarm_plays_out_never_cuts_the_gate_scene(tmp_path):
    app = start(tmp_path)
    run_until(app, 15, lambda a: a.director.idle)
    raise_alarm(app)
    app._handle_keys(["1"])                            # answered: the gate scene plays on, no prompt is open
    run_until(app, 1, lambda a: False)
    assert app.engine.prompt is None and app.director._cur is not None and app._gate_busy()
    queued = list(app.director._queue)
    n = len(app.logs)
    app._handle_keys(["b"])
    assert list(app.director._queue)[:len(queued)] == queued and app.view == "briefing" and app._walk_behind
    run_until(app, 30, lambda a: a.director.idle)
    later = list(app.logs)[n:]
    assert not any("OVERRIDDEN" in line for line in later)
    assert sum("DISENGAGED" in line for line in later) <= 1    # only the gate closing itself once its scene is over
    assert app.director.scene.view_p < 0.01            # the walk up played after the gate scene


@pytest.mark.parametrize("answer", ["1", "2"])
def test_an_answered_incoming_wormhole_shuts_down_on_its_own(tmp_path, answer):
    app = start(tmp_path)
    run_until(app, 15, lambda a: a.director.idle)
    raise_alarm(app)
    app._handle_keys([answer])
    assert run_until(app, 60, lambda a: a.director.idle and a.director.scene.horizon == "off")
    assert app.director.scene.locked == 0 and any("DISENGAGED" in line for line in app.logs)


def test_a_walk_never_shuts_a_wormhole_that_is_up(tmp_path):
    app = start(tmp_path)
    run_until(app, 15, lambda a: a.director.idle)
    s = app.director.scene
    s.horizon, s.locked = "open", 7                     # a wormhole up on a gate with nothing queued
    app._handle_keys(["b"])
    run_until(app, 0.5, lambda a: False)
    assert app.view == "briefing" and s.horizon == "open" and s.locked == 7


def test_b_with_an_alarm_open_stays_in_the_gate_room(tmp_path):
    app = start(tmp_path)
    run_until(app, 15, lambda a: a.director.idle)
    raise_alarm(app)
    app._handle_keys(["b", "b", "b"])
    assert app.view == "gate" and app.engine.prompt is not None
    assert sum("ALARM OPEN — GIVE AN ORDER FIRST" in line for line in app.logs) == 1
    app._handle_keys(["1"])
    assert any("ORDER:" in line for line in app.logs)
    assert not app.engine.c.alarms or app.engine.prompt is not None     # answered (a follow-up may be up)


def test_walking_on_an_idle_gate_starts_at_once(tmp_path):
    app = start(tmp_path)
    run_until(app, 15, lambda a: a.director.idle)
    app._handle_keys(["b"])
    assert app.view == "briefing" and app._walking
    run_until(app, 5, lambda a: a.director.idle)
    assert not app._walking and app.director.scene.view_p < 0.01


def test_walking_cuts_the_ambient_gate_scene(tmp_path):
    app = start(tmp_path)
    run_until(app, 15, lambda a: a.director.idle)
    list(app.engine.c.worlds.values())[0].drone = "malp"    # a parked drone to uplink from
    app.engine._quiet = eng.IDLE_SCENE                  # the quiet gate plays its ambient scene now
    tick(app)
    assert app.engine.ambient and not app.director.idle
    assert run_until(app, 30, lambda a: a.director.scene.horizon == "open")    # the uplink's wormhole is up
    assert app.engine.ambient and not app.director.idle
    app._handle_keys(["b"])
    assert app.view == "briefing" and app._walking and not app._walk_behind
    assert not app.engine.ambient                       # the walk is no ambient scene: real traffic may cut it
    assert run_until(app, app.cfg.transition_seconds + 4, lambda a: not a._walking)
    assert app.director.scene.view_p < 0.01 and app.director.scene.horizon == "off"
    assert not app.engine.ambient


def test_q_does_not_quit_while_walking_down(tmp_path):
    app = start(tmp_path)
    run_until(app, 15, lambda a: a.director.idle)
    app._handle_keys(["b"])
    run_until(app, 5, lambda a: a.director.idle)
    app._handle_keys(["q"])                       # back out of the room: the walk down starts
    assert app.view == "gate" and app._walking
    app._handle_keys(["q"])
    assert not app.director.exiting
    run_until(app, 5, lambda a: not a._walking)
    app._handle_keys(["q"])
    assert app.director.exiting


def test_mute_and_volume_work_in_the_database(tmp_path):
    app = start(tmp_path)
    app._handle_keys(["d", "m"])
    assert app.view == "database" and app.logs[-1].endswith(("AUDIO MUTED", "AUDIO ON"))
    app._handle_keys(["+"])
    assert "VOLUME" in app.logs[-1]
    app._handle_keys(["/", "m"])
    assert app.db.text_mode and "VOLUME" in app.logs[-1]


def test_the_full_legend_steps_aside_for_an_alarm_or_the_briefing_room(tmp_path):
    app = start(tmp_path)
    assert not app._legend_busy()
    raise_alarm(app)
    assert app._legend_busy()
    app._handle_keys(["1"])
    run_until(app, 15, lambda a: a.director.idle and not a.engine.c.alarms)
    assert not app._legend_busy()
    app._handle_keys(["b"])
    assert app._legend_busy()


def test_keys_are_held_for_as_long_as_the_player_keeps_typing(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: now[0])
    app = start(tmp_path)
    run_until(app, 15, lambda a: not a._walking)
    app._handle_keys(["b", "1", "2", "5"])
    typed(app, "abc")
    raise_alarm(app)
    run_until(app, 5, lambda a: not a._walking)
    prompt, text = app.engine.prompt, b"hold position bq2d 1 dq"
    for i in range(30):                               # 6 s of typing, a key every 0.2 s
        now[0] = 100.0 + 0.2 * (i + 1)
        typed(app, chr(text[i % len(text)]))
    assert app.engine.prompt is prompt and app.engine.c.alarms and not app.director.exiting
    assert app.view == "gate" and not app._walking and app.db is None
    assert not any("ORDER:" in line for line in app.logs)
    assert sum("ALARM — TYPING HELD" in line for line in app.logs) == 1
    now[0] += 1.05                                    # a second of quiet: keys work again
    app._handle_keys(["2"])
    assert any("ORDER:" in line for line in app.logs)


def test_enter_ends_the_typing_hold_and_is_swallowed(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: now[0])
    app = start(tmp_path)
    app._handle_keys(["d", "/"])
    typed(app, "ab")
    raise_alarm(app)
    now[0] = 100.5
    typed(app, "c\r")
    assert app.engine.c.alarms and not any("ORDER:" in line for line in app.logs)
    now[0] = 100.6
    app._handle_keys(["1"])
    assert any("ORDER:" in line for line in app.logs)


def test_ctrl_c_cancels_a_note_and_does_not_quit(tmp_path):
    app = start(tmp_path)
    app._handle_keys(["b", "1", "2", "5"])
    typed(app, "half a note")
    w = list(app.engine.c.worlds.values())[1]
    before = list(w.notes)
    typed(app, "\x03")
    assert not app.director.exiting and not app._quit and app.view == "briefing"
    assert not app._text_mode() and app.room.screen == "world" and app.room.note == "" and w.notes == before


def test_escape_cancels_a_note(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: now[0])
    app = start(tmp_path)
    app._handle_keys(["b", "1", "2", "5"])
    typed(app, "hi")
    app._handle_keys(app.parser.feed(b"\x1b", now=10.0))
    app.parser.text = app._typing()
    app._handle_keys(app.parser.feed(b"", now=10.3))
    assert not app._text_mode() and app.room.note == "" and not app.director.exiting


def test_ctrl_c_and_escape_clear_a_database_search(tmp_path):
    app = start(tmp_path)
    for cancel in ("ctrl-c", "escape"):
        app._handle_keys(["d", "/"])
        typed(app, "abyd")
        assert app.db.query == "abyd"
        app._handle_keys([cancel])
        assert app.view == "database" and not app.db.text_mode and app.db.query == ""
        assert not app.director.exiting and not app._quit
        app._handle_keys(["q"])
        assert app.view == "gate"


def test_ctrl_c_in_the_gate_room_still_shuts_down(tmp_path):
    app = start(tmp_path)
    run_until(app, 15, lambda a: not a._walking)
    app._handle_keys(["ctrl-c"])
    assert app.director.exiting
    app._handle_keys(["ctrl-c"])
    assert app._quit


def test_ctrl_c_during_the_typing_hold_cancels_the_note_not_the_game(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: now[0])
    app = start(tmp_path)
    app._handle_keys(["b", "1", "2", "5"])
    typed(app, "hi")
    room = app.room
    raise_alarm(app)
    now[0] = 100.3
    typed(app, "\x03")
    assert not app.director.exiting and room.note == "" and not room.text_mode
    app._handle_keys(["1"])                            # the hold is over
    assert any("ORDER:" in line for line in app.logs)


def test_the_typing_grace_ends_a_second_after_the_last_key(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: now[0])
    app = start(tmp_path)
    app._handle_keys(["d", "/"])
    typed(app, "ab")
    raise_alarm(app)
    now[0] = 101.1
    app._handle_keys(["1"])
    assert not app.engine.c.alarms


def test_the_opening_walk_is_a_walk(tmp_path):
    app = start(tmp_path)
    assert app._walking and not app._walk_behind
    app._handle_keys(["b"])                           # turns straight round: nothing to wait for
    assert app.view == "briefing" and not app._walk_behind


def test_q_mid_walk_says_why_once(tmp_path):
    app = start(tmp_path)
    app._handle_keys(["q", "q", "q"])
    assert not app.director.exiting
    assert sum("WALKING — Q AGAIN ON ARRIVAL" in line for line in app.logs) == 1
    run_until(app, 15, lambda a: not a._walking)
    app._handle_keys(["q"])
    assert app.director.exiting


# ---------------------------------------------------------------- walks, searches, saves
def ambient_on(app):
    """A quiet gate plays its ambient science scene; returns once its wormhole is up."""
    list(app.engine.c.worlds.values())[0].drone = "malp"    # a parked drone to uplink from
    app.engine._quiet = eng.IDLE_SCENE
    tick(app)
    assert app.engine.ambient and not app.director.idle
    assert run_until(app, 30, lambda a: a.director.scene.horizon == "open")


def test_a_walk_queued_behind_the_ambient_scene_survives_the_traffic_that_cuts_it(tmp_path, monkeypatch):
    monkeypatch.setattr(eng, "MISS", (101, 101, 101, 101))          # the next check-in is missed
    app = start(tmp_path)
    e, c = app.engine, app.engine.c
    worlds = list(c.worlds.values())
    worlds[1].status = "probed"
    assert "ASSIGNED" in e.assign(worlds[1].id, "SG-1", "survey")
    assert run_until(app, 60, lambda a: a.director.idle and c.gate_until <= c.now)
    app._handle_keys(["b"])
    assert run_until(app, 10, lambda a: not a._walking) and app.view == "briefing"
    ambient_on(app)
    c.events.push(c.now, "checkin", {"mission": c.missions[0].id})
    tick(app)                                          # a missed check-in: an alarm with nothing to show
    assert e.prompt is not None and e.prompt.title == "MISSED CHECK-IN"
    assert app.view == "gate" and app._walking
    assert e.probe(worlds[2].id).startswith("MALP QUEUED")      # the next real traffic
    assert run_until(app, 60, lambda a: not a._walking)
    assert app.view == "gate" and app.director.scene.view_p > 0.999
    app._handle_keys(["q"])
    assert app.director.exiting


def test_a_lost_walk_heals_itself(tmp_path):
    app = start(tmp_path)
    assert run_until(app, 15, lambda a: not a._walking)
    app._handle_keys(["b"])
    assert app._walking
    app.director.skip(log=None)                        # something threw the walk away
    assert run_until(app, 10, lambda a: not a._walking)
    assert app.view == "briefing" and app.director.scene.view_p < 0.001 and not app._walk_behind


def test_an_alarm_cuts_the_ambient_scene(tmp_path):
    app = start(tmp_path)
    assert run_until(app, 15, lambda a: a.director.idle)
    ambient_on(app)
    app.engine._raise({"type": "missed_checkin", "mission": 99, "deadline": None, "title": "MISSED CHECK-IN",
                       "text": "Nobody."})
    assert not app.engine.ambient


def test_walking_does_not_stop_the_game_clock(tmp_path):
    app = start(tmp_path)
    e, c = app.engine, app.engine.c
    assert run_until(app, 15, lambda a: a.director.idle and c.gate_until <= c.now)
    app._handle_keys(["b"])
    assert app._walking and not app._walk_behind
    w = list(c.worlds.values())[2]
    e.probe(w.id)
    m0 = c.minutes
    tick(app, 0.25)
    assert abs(c.minutes - m0 - clock.to_minutes(0.25, e.sph)) < 1e-6    # not held at the drone's launch
    assert any("MALP SENT TO" in line for line in app.logs)
    n = len(app.logs)
    assert run_until(app, 40, lambda a: a.director.idle)
    assert any("MALP IN TRANSIT" in line for line in list(app.logs)[n:])     # queued behind the walk, not dropped


def test_a_half_typed_search_survives_an_alarm(tmp_path):
    app = start(tmp_path)
    app._handle_keys(["d", "/"])
    typed(app, "aby")
    db = app.db
    raise_alarm(app)
    assert app.view == "gate" and app.db is None
    app._grace_until = 0.0
    app._handle_keys(["d"])                            # the alarm first: d doesn't hide it
    assert app.view == "gate" and app.engine.prompt is not None
    app._handle_keys(["2"])
    app._handle_keys(["d"])
    assert app.view == "database" and app.db is db and app._text_mode() and db.query == "aby"
    app._handle_keys(["enter", "right", "down", "down", "down"])
    tab, scroll = db.tab, db.scroll
    assert (tab, scroll) == ("world", 3)
    app.engine.c.alarms.clear()
    c = app.engine.c
    for _ in range(10):                                # not every incoming wormhole needs an order
        assert run_until(app, 60, lambda a: a.director.idle)
        c.events.push(c.now, "incoming")
        if run_until(app, 5, lambda a: a.engine.prompt is not None):
            break
    assert app.engine.prompt is not None
    app._grace_until = 0.0
    app._handle_keys(["2", "d"])
    assert app.db is db and (db.tab, db.scroll, db.query) == (tab, scroll, "aby")
    app._handle_keys(["q", "d"])                       # closed by the player: the next visit starts afresh
    assert app.db is not db and app.db.query == ""


def test_ctrl_c_during_the_typing_hold_cancels_a_kept_search(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(app_mod.time, "monotonic", lambda: now[0])
    app = start(tmp_path)
    app._handle_keys(["d", "/"])
    typed(app, "ab")
    db = app.db
    raise_alarm(app)
    now[0] = 100.3
    typed(app, "\x03")
    assert not app.director.exiting and db.query == "" and not db.text_mode


def test_a_failed_save_is_logged_once_and_the_game_goes_on(tmp_path, monkeypatch):
    app = start(tmp_path)
    broken = [True]
    real = save_mod._write_atomic

    def write(path, data):
        if broken[0]:
            raise OSError(28, "No space left on device")
        real(path, data)
    monkeypatch.setattr(save_mod, "_write_atomic", write)
    for _ in range(3):
        app.engine.save_now()
    run_until(app, 30, lambda a: False)                # the clock runs on, trying again every 5 game minutes
    fails = [line for line in app.logs if "SAVE FAILED" in line]
    assert len(fails) == 1 and fails[0].endswith("SAVE FAILED — NO SPACE LEFT ON DEVICE")
    assert all(len(line) <= 76 for line in app.logs)
    broken[0] = False
    app.engine.save_now()
    app.engine.save_now()
    assert sum("SAVE OK" in line for line in app.logs) == 1
    broken[0] = True
    app.engine.save_now()
    assert sum("SAVE FAILED" in line for line in app.logs) == 2
    assert run_until(app, 30, lambda a: not a._walking)
    app._handle_keys(["q"])                            # quitting with a broken disk still quits
    assert app.director.exiting


def test_a_read_only_save_directory_does_not_crash_the_game(tmp_path):
    if os.geteuid() == 0:
        return                                         # root writes anywhere
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        app = make_app(ro, "missions")
        app._handle_keys(["1", "1", "1", "2"])
        assert app.mode == "game" and any("SAVE FAILED" in line for line in app.logs)
        app.engine.c.over = "The SGC was overrun."
        app.engine._game_over()                        # the hall of records can't be written either
        assert app.engine.ended and any("RECORD NOT SAVED" in line for line in app.logs)
    finally:
        ro.chmod(0o700)


def test_accented_letters_can_be_typed_into_a_note_and_a_search(tmp_path):
    app = start(tmp_path)
    app._handle_keys(["b", "1", "2", "5"])
    typed(app, "café ")
    app.parser.text = app._typing()
    for part in (b"\xc3", b"\xbc", b"ber"):            # ü split across two reads
        app._handle_keys(app.parser.feed(part))
    assert app.room.note == "café über"
    app._handle_keys(["enter"])
    assert list(app.engine.c.worlds.values())[1].notes[-1][1] == "café über"
    app._handle_keys(["q", "q", "d", "/"])
    typed(app, "é")
    assert app.db.query == "é"


def test_the_cancel_keys_come_from_the_room():
    from sgc.game import room
    assert app_mod.CANCEL_KEYS is room.CANCEL_KEYS


def test_the_full_legend_names_every_gate_room_key():
    from sgc.game import screens
    keys = " ".join(k for k, _ in screens.KEYS_HELP)
    for k in ("b", "d", "1-9", "↑↓", "⏎", "←→", "/", "s", "f", "?", "m", "+", "-", "q", "p", "^C", "Esc"):
        assert k in keys, k
    labels = " ".join(label for _, label in screens.KEYS_HELP).lower()
    assert "pause" in labels and "recruit" in labels and "cancel" in labels


def queue_tab(app):
    app._handle_keys(["d", "left"])                   # QUEUE is the last tab: left from Addresses wraps to it
    assert app.view == "database" and app.db.tab == "queue"


def test_the_queue_tab_cancels_a_dial_out_after_x_twice(tmp_path):
    app = start(tmp_path)
    c = app.engine.c
    w = list(c.worlds.values())[1]
    app.engine.probe(w.id)
    queue_tab(app)
    assert [r.cells[1] for r in app.db.rows()] == [f"MALP → {w.name.upper()}"]
    app._handle_keys(["x"])
    assert app.db.message == f"CANCEL THE MALP TO {w.name.upper()}?  x AGAIN TO CONFIRM" and c.stock["malp"] == 3
    app._handle_keys(["x"])
    assert app.db.message == f"CANCELLED: MALP TO {w.name.upper()}" and c.stock["malp"] == 4
    assert app.db.rows() == [] and not c.events.find(lambda e: e.kind == "dial_out")
    assert app.logs[-1].endswith(f"CANCELLED: MALP TO {w.name.upper()}")


def test_the_queue_tab_moves_a_dial_out_and_the_selection_follows_it(tmp_path):
    app = start(tmp_path)
    c = app.engine.c
    a, b = list(c.worlds.values())[1:3]
    app.engine.probe(a.id)
    app.engine.probe(b.id)
    queue_tab(app)
    app._handle_keys(["down", "["])
    assert app.db.message == f"MALP TO {b.name.upper()}: NOW 1 IN THE GATE QUEUE"
    assert [i.what for i in app.engine.schedule_view()] == [f"MALP → {b.name.upper()}", f"MALP → {a.name.upper()}"]
    assert app.db.sel == 0 and app.db.selected().key == f"dial:malp:{b.id}"
    app._handle_keys(["["])
    assert app.db.message == "ALREADY FIRST IN THE GATE QUEUE"


def test_x_on_a_row_that_cannot_be_cancelled_shows_why(tmp_path):
    app = start(tmp_path)
    c = app.engine.c
    t = c.teams["SG-3"]
    t.status, t.until = "injured", c.now + 600
    queue_tab(app)
    app._handle_keys(["x"])
    assert app.db.message == "NOTHING TO CANCEL: SG-3 IS INJURED" and t.status == "injured"
    app_mod.screens.draw_database(app.canvas, app.layout, app.db, app.legend)   # what _frame draws
    assert "NOTHING TO CANCEL: SG-3 IS INJURED" in app.canvas.text()


def test_the_database_keys_the_app_handles_disarm_a_cancel(tmp_path):
    app = start(tmp_path)
    c = app.engine.c
    app.engine.probe(list(c.worlds.values())[1].id)
    queue_tab(app)
    for k in ("?", "m", "+", "-"):
        app._handle_keys(["x"])
        assert app.db.armed is not None and app.db.message.startswith("CANCEL THE MALP")
        app._handle_keys([k])
        assert app.db.armed is None and app.db.message == ""
    app._handle_keys(["x"])
    assert app.db.message.startswith("CANCEL THE MALP") and c.stock["malp"] == 3


def test_reopening_the_database_disarms_a_half_made_cancel(tmp_path):
    app = start(tmp_path)
    c = app.engine.c
    app.engine.probe(list(c.worlds.values())[1].id)
    queue_tab(app)
    app._handle_keys(["x"])
    kept = app.db
    app._back_to_the_gate_room()                      # as an alarm does: the Database is kept
    app._handle_keys(["d"])
    assert app.db is kept and app.db.armed is None and app.db.tab == "queue"
    assert [r.cells[1] for r in app.db.rows()]        # the kept Database still reads the engine's schedule
    app._handle_keys(["x"])
    assert app.db.message.startswith("CANCEL THE MALP") and c.stock["malp"] == 3
    app._handle_keys(["x"])
    assert app.db.message.startswith("CANCELLED: MALP") and c.stock["malp"] == 4


def test_a_stage_1_save_continues_with_a_notice(tmp_path):
    import shutil
    shutil.copy("tests/data/stage1_save.json", tmp_path / "campaign.json")
    app = make_app(tmp_path, "missions")
    app._handle_keys(["1"])                                          # continue
    assert app.mode == "game" and app.engine.c.funding == 500
    assert any(line.endswith("STAGE 1 SAVE UPGRADED") for line in app.logs)


def test_retiring_files_the_record_and_deletes_the_save(tmp_path):
    app = start(tmp_path)
    app._handle_keys(["b"])
    assert run_until(app, 5, lambda a: not a._walking)
    app._handle_keys(["6", "1"])                                     # retire, and confirm
    assert app.engine.ended and not (tmp_path / "campaign.json").exists()
    [rec] = app.saves.records()
    assert rec["result"] == "retired" and "score" in rec
    app._handle_keys(["1"])
    tick(app)
    assert app.mode == "menu"


def test_victory_is_filed_at_once_and_only_once(tmp_path):
    app = start(tmp_path)
    c = app.engine.c
    c.won = c.now
    app._game_won(c)
    assert app.saves.records()[0]["result"] == "victory"
    c.over = "You handed over command of the SGC."
    c.ending = "retired"
    app.engine._game_over()
    assert len(app.saves.records()) == 1 and not (tmp_path / "campaign.json").exists()
