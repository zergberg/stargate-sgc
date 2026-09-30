import fcntl
import os
import pty
import random
import struct
import termios
from dataclasses import replace

from sgc.addresses import AddressPicker, load_canon
from sgc.app import App
from sgc.config import Config
from sgc.director import Director
from sgc.events import REGISTRY
from sgc.game.menu import Menu
from sgc.game.save import Saves
from sgc.model import _teams
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
    (cfg / "config.toml").write_text("transition_seconds = 2\n")
    return tmp_path


def quit_twice(fd, pid):
    os.write(fd, b"q")
    drain(fd, 1.0)
    os.write(fd, b"q")
    assert wait(pid) == 0


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
    out = b""
    for k in (b"1", b"1", b"2"):                  # new game, campaign, officer
        os.write(fd, k)
        out += drain(fd, 0.6)
    out += drain(fd, 4.0)                         # the canvas only redraws cells that change
    assert b"SGC STATUS" in out and b"HEADING DOWN" in out
    quit_twice(fd, pid)
    assert (home / ".local" / "share" / "stargate-sgc" / "campaign.json").is_file()


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
        self.paused = []

    def pause(self, p):
        self.paused.append(p)

    def __getattr__(self, name):
        return lambda *a, **k: None


def make_app(tmp_path, start):
    app = App(None, replace(Config(), transition_seconds=2, exit_duration=1.0), QuietMixer(), random.Random(1),
              start=start, saves=Saves(tmp_path))
    app.director = Director(app.cfg, app.rng, AddressPicker(load_canon(), app.cfg.canon_ratio, app.rng), REGISTRY)
    if start != "ambient":
        app.mode, app.director.scene.view_p = "menu", 0.0
        app.menu = Menu(app.saves.exists(), "missions" if start == "missions" else "main")
    return app


def tick(app, dt=1 / 30):
    """The run loop's per-frame logic, without drawing."""
    if not app._paused:
        logs, cues = app.director.advance(dt)
        for line in logs:
            app.log(line)
        app._play(cues)
        if app.engine is not None and not app.director.exiting:
            app.engine.update(dt)
            if app.engine.finished:
                app._to_menu()
    app._prompt_sounds()


def run_until(app, secs, done, dt=1 / 30):
    for _ in range(int(secs / dt)):
        tick(app, dt)
        if done(app):
            return True
    return False


def test_game_keys_are_ignored_while_shutting_down(tmp_path):
    app = make_app(tmp_path, "missions")
    app._handle_keys(["1", "1", "1"])                 # new game, campaign, recruit
    assert run_until(app, 600, lambda a: a.engine.mode == "decision")
    prompt = app.director.scene.prompt
    app._handle_keys(["q"])
    saved = (tmp_path / "campaign.json").read_text()
    app._handle_keys(["1"])
    assert app.director.scene.prompt is prompt and app.engine.mode == "decision"
    assert run_until(app, 10, lambda a: a.director.finished)
    assert (tmp_path / "campaign.json").read_text() == saved


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


def test_returning_to_the_menu_resets_the_team_labels(tmp_path):
    app = make_app(tmp_path, "missions")
    app._handle_keys(["1", "1", "1"])                 # new game, campaign, recruit
    app.engine.campaign.teams["SG-2"].status = "captured"
    tick(app)
    assert app.director.scene.teams["SG-2"] == "MISSING"
    app._to_menu()
    assert app.director.scene.teams == _teams()
