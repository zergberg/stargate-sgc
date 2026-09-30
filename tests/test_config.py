from sgc.config import Config, load_config, save_setting


def test_defaults_when_missing(tmp_path):
    cfg, warns = load_config(tmp_path / "nope.toml")
    assert cfg == Config() and warns == []


def test_merge_over_defaults(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('volume = 0.8\nsound_pack = "freesound"\n[event_weights]\ncode_red = 3\n')
    cfg, warns = load_config(p)
    assert cfg.volume == 0.8 and cfg.sound_pack == "freesound"
    assert cfg.event_weights["code_red"] == 3 and cfg.event_weights["science"] == 1
    assert warns == []


def test_bad_values_fall_back(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('volume = "loud"\nfps = -5\ngraphics = "hologram"\nbogus = 1\n')
    cfg, warns = load_config(p)
    assert cfg.volume == 0.3 and cfg.fps == 24 and cfg.graphics == "auto"
    assert len(warns) == 4


def test_unparseable_file(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("volume = = 1")
    cfg, warns = load_config(p)
    assert cfg == Config() and len(warns) == 1


def test_walk_timing_default_and_validation(tmp_path):
    assert Config().transition_seconds == 10.0
    p = tmp_path / "c.toml"
    p.write_text("transition_seconds = 15\n")
    cfg, warns = load_config(p)
    assert cfg.transition_seconds == 15 and warns == []


def test_game_settings_defaults_and_validation(tmp_path):
    cfg = Config()
    assert cfg.game_pace is None and cfg.notify is True and cfg.legend == "bar"
    p = tmp_path / "c.toml"
    p.write_text('game_pace = 30\nnotify = false\nlegend = "full"\n')
    cfg, warns = load_config(p)
    assert (cfg.game_pace, cfg.notify, cfg.legend) == (30, False, "full") and warns == []
    p.write_text('game_pace = 2\nlegend = "huge"\ndecision_countdown = 12\n')
    cfg, warns = load_config(p)
    assert cfg.game_pace is None and cfg.legend == "bar" and len(warns) == 3


def test_save_setting_replaces_or_adds_a_top_level_key(tmp_path):
    p = tmp_path / "sub" / "c.toml"
    save_setting("legend", "full", p)
    assert load_config(p)[0].legend == "full"
    p.write_text('volume = 0.5\nlegend = "bar"\n[event_weights]\ncode_red = 2\n')
    save_setting("legend", "off", p)
    save_setting("notify", False, p)
    cfg, warns = load_config(p)
    assert cfg.legend == "off" and cfg.notify is False and cfg.volume == 0.5
    assert cfg.event_weights["code_red"] == 2 and warns == []
    assert p.read_text().count("legend") == 1


def test_save_setting_keeps_a_trailing_comment(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('legend = "bar"  # controls legend\n')
    save_setting("legend", "off", p)
    assert p.read_text() == 'legend = "off"  # controls legend\n'
    p.write_text('note = "a # b"  # keep this\n')
    save_setting("note", "x", p)
    assert p.read_text() == 'note = "x"  # keep this\n'
