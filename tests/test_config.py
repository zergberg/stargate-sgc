from sgc.config import Config, load_config


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


def test_game_timing_defaults_and_validation(tmp_path):
    from sgc.config import Config, load_config
    assert Config().transition_seconds == 10.0 and Config().decision_countdown == 12.0
    p = tmp_path / "c.toml"
    p.write_text("transition_seconds = 15\ndecision_countdown = 3\n")
    cfg, warns = load_config(p)
    assert cfg.transition_seconds == 15 and cfg.decision_countdown == 12.0
    assert any("decision_countdown" in w for w in warns)
