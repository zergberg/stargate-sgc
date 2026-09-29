from sgc.glyphs import glyph_char, GLYPH_NAMES, load_glyph_font


def test_mapping():
    assert glyph_char(1) == "A" and glyph_char(26) == "Z"
    assert glyph_char(27) == "a" and glyph_char(39) == "m"
    assert len(GLYPH_NAMES) == 39 and GLYPH_NAMES[0] == "Earth" and GLYPH_NAMES[38] == "Leo"


def test_missing_font_falls_back(tmp_path):
    assert load_glyph_font(tmp_path / "missing.ttf", 20) is None
    assert load_glyph_font(None, 20) is None
