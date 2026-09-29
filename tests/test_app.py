import fcntl
import glob
import os
import pty
import select
import signal
import struct
import sys
import termios
import time

PY = sys.executable


def spawn(args, cols=100, rows=30):
    pid, fd = pty.fork()
    if pid == 0:
        os.execv(PY, [PY, "-m", "sgc.app", "--no-sound", "--seed", "1", *args])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, cols * 9, rows * 18))
    return pid, fd


def resize(fd, pid, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, cols * 9, rows * 18))
    os.kill(pid, signal.SIGWINCH)


def drain(fd, secs):
    out = b""
    end = time.time() + secs
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    return out


def wait(pid, secs=15):
    end = time.time() + secs
    while time.time() < end:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.05)
    os.kill(pid, signal.SIGKILL)
    raise AssertionError("process did not exit")


def test_runs_and_exits_cleanly_blocks():
    pid, fd = spawn(["--graphics", "blocks", "--duration", "3", "--exit-duration", "2"])
    out = drain(fd, 9)
    assert wait(pid) == 0
    assert b"\x1b[?1049h" in out and b"\x1b[?1049l" in out[-400:] and "▀".encode() in out
    assert b"SGC" in out


def test_resize_cycle():
    pid, fd = spawn(["--graphics", "blocks", "--duration", "5", "--exit-duration", "2"])
    drain(fd, 1)
    for cols, rows in ((30, 10), (70, 20), (140, 40), (100, 30)):
        resize(fd, pid, cols, rows)
        drain(fd, 0.6)
    out = drain(fd, 8)
    assert wait(pid) == 0 and b"\x1b[?1049l" in out


def test_tiny_pane_shows_enlarge_message():
    pid, fd = spawn(["--graphics", "blocks", "--duration", "1", "--exit-duration", "1"], cols=30, rows=10)
    out = drain(fd, 5)
    assert wait(pid) == 0 and b"ENLARGE PANE" in out


def test_crash_restores_terminal():
    pid, fd = spawn(["--graphics", "kitty", "--duration", "30"])
    drain(fd, 1.5)
    os.kill(pid, signal.SIGTERM)
    out = drain(fd, 3)
    wait(pid)
    assert b"\x1b[?1049l" in out and b"\x1b[?25h" in out and b"a=d,d=A" in out
    assert not glob.glob(f"/dev/shm/tty-graphics-protocol-sgc-{pid}-*")


def test_q_starts_animated_exit_and_second_q_is_immediate():
    pid, fd = spawn(["--graphics", "blocks"])
    drain(fd, 1.5)
    os.write(fd, b"q")
    out = drain(fd, 0.7)
    assert b"SHUTTING DOWN" in out or b"DIAL ABORTED" in out or b"DISENGAGED" in out
    os.write(fd, b"q")
    t0 = time.time()
    drain(fd, 1.5)
    assert wait(pid, 5) == 0 and time.time() - t0 < 3


def test_kitty_frames_use_placement_and_sync():
    pid, fd = spawn(["--graphics", "kitty", "--duration", "1", "--exit-duration", "1"])
    out = drain(fd, 6)
    wait(pid)
    assert b"\x1b_Ga=T" in out and b"i=1" in out and b"i=2" in out and b"\x1b[?2026h" in out
