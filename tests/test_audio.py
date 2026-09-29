import wave
from array import array

from sgc.audio.bank import SoundBank
from sgc.audio.mixer import Mixer, Voice, mix_chunk


def test_mix_sums_and_clips():
    a = Voice(array("h", [20000] * 10), loop=False, gain=1.0)
    b = Voice(array("h", [20000] * 10), loop=False, gain=1.0)
    out = array("h")
    out.frombytes(mix_chunk([a, b], 10, 1.0))
    assert list(out) == [32767] * 10


def test_oneshot_finishes_and_loop_wraps():
    one = Voice(array("h", [100] * 4), loop=False, gain=1.0)
    lp = Voice(array("h", [1, 2, 3]), loop=True, gain=1.0)
    out = array("h")
    out.frombytes(mix_chunk([one, lp], 6, 1.0))
    assert list(out) == [101, 102, 103, 101, 2, 3]
    assert one.done and not lp.done


def test_master_volume_scales():
    v = Voice(array("h", [10000] * 4), loop=False, gain=1.0)
    out = array("h")
    out.frombytes(mix_chunk([v], 4, 0.5))
    assert list(out) == [5000] * 4


def test_fade_out_reaches_silence_and_finishes():
    v = Voice(array("h", [10000] * 100), loop=True, gain=1.0)
    v.fade_out(10)
    out = array("h")
    out.frombytes(mix_chunk([v], 20, 1.0))
    assert out[0] > out[5] > 0 and list(out[10:]) == [0] * 10 and v.done


def _wav(p, v):
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(array("h", [v] * 10).tobytes())


def test_bank_prefers_user_then_pack_then_synth(tmp_path):
    assets, user = tmp_path / "assets", tmp_path / "user"
    _wav(assets / "synth/kawoosh.wav", 1)
    _wav(assets / "synth/klaxon.wav", 2)
    _wav(assets / "freesound/wav/klaxon.wav", 3)
    _wav(user / "kawoosh.wav", 4)
    b = SoundBank("freesound", user, assets)
    assert b.get("kawoosh")[0] == 4 and b.get("klaxon")[0] == 3
    assert b.get("nothing") is None


def test_bank_converts_rate_and_channels(tmp_path):
    p = tmp_path / "assets/synth/kawoosh.wav"
    p.parent.mkdir(parents=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(array("h", [1000, 3000] * 100).tobytes())
    s = SoundBank("synth", tmp_path / "u", tmp_path / "assets").get("kawoosh")
    assert abs(len(s) - 200) <= 1 and all(abs(x - 2000) <= 1 for x in s)


def test_mixer_survives_dead_player(tmp_path):
    m = Mixer(SoundBank("synth", tmp_path, tmp_path), 0.3, player_cmd=["false"])
    m.start()
    m.play("kawoosh")
    m.stop_all()
    m.close()   # must not raise


def test_no_player_is_silent(monkeypatch):
    import sgc.audio.players as pl
    monkeypatch.setattr(pl.shutil, "which", lambda _: None)
    assert pl.find_player() is None
