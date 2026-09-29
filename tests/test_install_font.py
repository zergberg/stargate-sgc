import zipfile
from pathlib import Path

import pytest

from sgc.glyphs import FONT_FILE, install_font


def _any_ttf() -> Path:
    for d in (Path("/usr/share/fonts"), Path("/usr/local/share/fonts")):
        for p in d.rglob("*.ttf") if d.is_dir() else ():
            return p
    pytest.skip("no system .ttf to stand in for the glyph font")


def test_installs_ttf_under_the_expected_name(tmp_path):
    src = tmp_path / "stargatesg1addressglyphs.ttf"
    src.write_bytes(_any_ttf().read_bytes())
    dest = install_font(src, dest_dir=tmp_path / "fonts")
    assert dest == tmp_path / "fonts" / FONT_FILE
    assert dest.read_bytes() == src.read_bytes()


def test_installs_from_a_zip(tmp_path):
    zp = tmp_path / "stargate_glyphs.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("readme.txt", "hi")
        z.writestr("Stargate SG-1 Address Glyphs.ttf", _any_ttf().read_bytes())
    dest = install_font(zp, dest_dir=tmp_path / "fonts")
    assert dest.name == FONT_FILE and dest.stat().st_size > 0


def test_finds_download_when_no_path_given(tmp_path):
    dl = tmp_path / "Downloads"
    dl.mkdir()
    (dl / "stargate_sg1_adress_glyphs.ttf").write_bytes(_any_ttf().read_bytes())
    dest = install_font(None, dest_dir=tmp_path / "fonts", search_dirs=(dl,))
    assert dest.name == FONT_FILE


def test_rejects_a_file_that_is_not_a_font(tmp_path):
    bad = tmp_path / "stargate_glyphs.ttf"
    bad.write_bytes(b"not a font")
    with pytest.raises(ValueError):
        install_font(bad, dest_dir=tmp_path / "fonts")
    assert not (tmp_path / "fonts" / FONT_FILE).exists()


def test_explains_when_nothing_is_found(tmp_path):
    with pytest.raises(ValueError, match="thescifiworld.net"):
        install_font(None, dest_dir=tmp_path / "fonts", search_dirs=(tmp_path,))


def test_cli_install_font(tmp_path, monkeypatch, capsys):
    import sgc.app as app
    seen = {}
    monkeypatch.setattr(app, "install_font", lambda src: seen.setdefault("src", src) or tmp_path / FONT_FILE)
    assert app.main(["--install-font", str(tmp_path / "x.zip")]) == 0
    assert seen["src"] == tmp_path / "x.zip"
    assert "installed" in capsys.readouterr().out


def test_cli_install_font_failure_is_reported(monkeypatch, capsys):
    import sgc.app as app
    def boom(src):
        raise ValueError("nothing here")
    monkeypatch.setattr(app, "install_font", boom)
    assert app.main(["--install-font"]) == 1
    assert "nothing here" in capsys.readouterr().err
