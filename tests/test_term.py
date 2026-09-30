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
    assert kp.feed(b"\x1b[A+-=_pQ") == ["up", "+", "-", "+", "-", "p", "q"]


def test_key_parser_game_keys():
    kp = KeyParser()
    assert kp.feed(b"1239rR\r\n") == ["1", "2", "3", "9", "r", "r", "enter", "enter"]
    assert kp.feed(b"\x1b[B\x1bOA\x1b[C\x1b[D\x1b[H") == ["down", "up", "right", "left"]


def test_key_parser_campaign_keys():
    kp = KeyParser()
    assert kp.feed(b"dDbB?/sSfF\t\x7f") == ["d", "d", "b", "b", "?", "/", "s", "s", "f", "f", "tab", "backspace"]


def test_key_parser_queue_keys():
    kp = KeyParser()
    assert kp.feed(b"xX[]") == ["x", "x", "[", "]"]
    kp.text = True
    assert kp.feed(b"x[]") == ["ch:x", "ch:[", "ch:]"]              # typed into a search, they're just text


def test_key_parser_text_mode():
    kp = KeyParser()
    kp.text = True
    assert kp.feed(b"Hi q/1\x7f\r\x03") == ["ch:H", "ch:i", "ch: ", "ch:q", "ch:/", "ch:1", "backspace", "enter",
                                           "ctrl-c"]
    assert kp.feed(b"\x1b[A") == ["up"]
    kp.text = False
    assert kp.feed(b"q") == ["q"]


def test_key_parser_reads_utf8_text():
    kp = KeyParser()
    kp.text = True
    assert kp.feed("é ü Ñ".encode()) == ["ch:é", "ch: ", "ch:ü", "ch: ", "ch:Ñ"]
    assert kp.feed(b"\xc3") == [] and kp.feed(b"\xbc!") == ["ch:ü", "ch:!"]          # split across reads
    assert kp.feed("€".encode()[:1]) == [] and kp.feed("€".encode()[1:2]) == [] and kp.feed("€".encode()[2:]) == ["ch:€"]


def test_key_parser_never_swallows_odd_text_silently():
    kp = KeyParser()
    kp.text = True
    assert kp.feed(b"\xffa") == ["ch:?", "ch:a"]                  # not UTF-8
    assert kp.feed(b"\xc3a") == ["ch:?", "ch:a"]                  # a sequence cut short
    assert kp.feed(b"\xa9") == ["ch:?"]                           # a stray continuation byte
    assert kp.feed("漢😀".encode()) == ["ch:?", "ch:?"]           # too wide for one cell
    assert kp.feed("é".encode()) == ["ch:e", "ch:?"]        # a combining accent on its own
    assert kp.feed(b"\xc2\x85") == ["ch:?"]                       # a C1 control


def test_key_parser_ignores_utf8_outside_text_entry():
    kp = KeyParser()
    assert kp.feed("éq".encode()) == ["q"]


def test_accented_text_renders_on_the_canvas():
    from sgc.term.canvas import Canvas
    cv = Canvas(10, 1)
    cv.put(0, 0, "café", None)
    assert "café" in cv.text() and "café".encode() in cv.render(True)


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
