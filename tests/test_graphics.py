import base64
import glob
import os
import re

from PIL import Image

from sgc.layout import Rect
from sgc.term.detect import Caps
from sgc.term.graphics import make_backend


def caps(g, kf=True):
    return Caps(graphics=g, truecolor=True, cell_w=10, cell_h=20, kitty_file=kf, in_tmux=False)


def img(w, h, c=(10, 20, 200)):
    return Image.new("RGB", (w, h), c)


def test_kitty_file_mode():
    b = make_backend(caps("kitty"))
    r = Rect(2, 1, 30, 15)
    w, h = b.pixel_size(r)
    out = b.show(1, img(w, h), r)
    assert b"a=T" in out and b"t=t" in out and b"i=1" in out and b"c=30" in out and b"r=15" in out
    path = base64.b64decode(re.search(rb";([A-Za-z0-9+/=]+)\x1b\\", out).group(1)).decode()
    assert "tty-graphics-protocol" in path and os.path.getsize(path) == w * h * 3
    b.cleanup()


def test_kitty_pixel_size_capped_and_scaled():
    b = make_backend(caps("kitty"))
    assert b.pixel_size(Rect(0, 0, 200, 100)) == (720, 720)
    b.scale = 0.5
    assert b.pixel_size(Rect(0, 0, 30, 15)) == (150, 150)


def test_kitty_cleanup_removes_tempfiles():
    b = make_backend(caps("kitty"))
    r = Rect(0, 0, 10, 5)
    for _ in range(3):
        b.show(1, img(*b.pixel_size(r)), r)
    out = b.cleanup()
    assert b"a=d,d=A" in out and not glob.glob(f"/dev/shm/tty-graphics-protocol-sgc-{os.getpid()}-*")


def test_kitty_direct_mode_chunks():
    b = make_backend(caps("kitty", kf=False))
    r = Rect(0, 0, 40, 20)
    out = b.show(2, img(*b.pixel_size(r)), r)
    assert b"t=d" in out and b"o=z" in out and out.count(b"\x1b_G") >= 1
    chunks = re.findall(rb"\x1b_G[^;]*;([^\x1b]*)\x1b\\", out)
    assert len(chunks) >= 1 and all(len(c) <= 4096 for c in chunks)


def test_sixel_structure():
    b = make_backend(caps("sixel"))
    r = Rect(0, 0, 4, 1)
    w, h = b.pixel_size(r)
    assert h % 6 == 0
    out = b.show(1, img(w, h), r)
    assert out.count(b"\x1bP") == 1 and b'"1;1;%d;%d' % (w, h) in out and out.endswith(b"\x1b\\")
    assert b"#0;2;4;8;78" in out


def test_sixel_two_colors_rows():
    b = make_backend(caps("sixel"))
    im = Image.new("RGB", (4, 6), (0, 0, 0))
    for x in range(4):
        im.putpixel((x, 0), (255, 255, 255))
    out = b.show(1, im, Rect(0, 0, 1, 1))
    body = out.split(b"\x1bP", 1)[1]
    assert b"!4@" in body or b"@@@@" in body          # top row only -> sixel value 1 ('@') for white
    assert b"!4}" in body or b"}}}}" in body          # rows 2..6 -> value 62 ('}') for black


def test_iterm_png():
    b = make_backend(caps("iterm"))
    r = Rect(0, 0, 10, 5)
    out = b.show(1, img(*b.pixel_size(r)), r)
    assert out.startswith(b"\x1b7") and b"\x1b]1337;File=inline=1;width=10;height=5" in out


def test_blocks_cells():
    b = make_backend(caps("blocks"))
    r = Rect(0, 0, 4, 2)
    out = b.show(1, img(*b.pixel_size(r)), r)
    assert out.count("▀".encode()) == 8 and b"38;2;10;20;200" in out


def test_blocks_256_colors():
    b = make_backend(Caps("blocks", False, 10, 20, False, False))
    out = b.show(1, img(4, 4), Rect(0, 0, 4, 2))
    assert b"38;5;" in out and b"38;2;" not in out
