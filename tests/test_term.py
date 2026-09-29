import pty
import time

from sgc.term.detect import detect
from sgc.term.keys import KeyParser
from sgc.term.screen import Terminal


def test_key_parser_ignores_responses():
    kp = KeyParser()
    assert kp.feed(b"\x1b_Gi=31;OK\x1b\\q\x1b[?62;4c m\x1b[6;20;10t\x03") == ["q", "space", "m", "ctrl-c"]


def test_key_parser_split_sequences():
    kp = KeyParser()
    assert kp.feed(b"\x1b[?62") == [] and kp.feed(b";4cq") == ["q"]


def test_key_parser_arrows_and_plus_minus():
    kp = KeyParser()
    assert kp.feed(b"\x1b[A+-=_pQ") == ["+", "-", "+", "-", "p", "q"]


def test_detect_timeout_falls_back():
    master, slave = pty.openpty()
    t = Terminal(fd_in=slave, fd_out=slave)
    t0 = time.monotonic()
    caps = detect(t, "auto", {})
    assert caps.graphics == "blocks" and time.monotonic() - t0 < 1.5


def test_forced_backend():
    master, slave = pty.openpty()
    assert detect(Terminal(fd_in=slave, fd_out=slave), "sixel", {}).graphics == "sixel"


def test_detect_reads_kitty_and_cell_size_replies():
    import os
    import threading
    master, slave = pty.openpty()
    t = Terminal(fd_in=slave, fd_out=slave)

    def answer():
        buf = b""
        while b"\x1b[c" not in buf:
            buf += os.read(master, 4096)
        os.write(master, b"\x1b_Gi=31;OK\x1b\\\x1b[6;18;9t\x1b[?62;22c")
    threading.Thread(target=answer, daemon=True).start()
    caps = detect(t, "auto", {"COLORTERM": "truecolor"})
    assert caps.graphics == "kitty" and caps.truecolor and (caps.cell_w, caps.cell_h) == (9, 18)


def test_detect_sixel_from_da1_and_tmux_skips_kitty():
    import os
    import threading
    master, slave = pty.openpty()
    t = Terminal(fd_in=slave, fd_out=slave)

    def answer():
        buf = b""
        while b"\x1b[c" not in buf:
            buf += os.read(master, 4096)
        os.write(master, b"\x1b_Gi=31;OK\x1b\\\x1b[?62;4;22c")
    threading.Thread(target=answer, daemon=True).start()
    caps = detect(t, "auto", {"TMUX": "/tmp/x"})
    assert caps.graphics == "sixel" and caps.in_tmux
