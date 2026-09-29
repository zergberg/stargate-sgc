import pathlib
import sys
import wave

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "tools"))
import make_sounds as ms  # noqa: E402


def test_every_cue_nonempty_and_in_range():
    for cue in ms.CUES:
        s = ms.synth(cue)
        assert len(s) > ms.RATE * 0.3, cue
        peak = max(abs(x) for x in s)
        assert 0.3 < peak <= 0.9, (cue, peak)


def test_deterministic():
    assert ms.synth("kawoosh") == ms.synth("kawoosh")


def test_loops_are_seamless():
    for cue in ms.LOOPS:
        s = ms.synth(cue)
        assert abs(s[0] - s[-1]) < 0.05, cue


def test_writes_wavs(tmp_path):
    ms.main(tmp_path)
    for cue in ms.CUES:
        with wave.open(str(tmp_path / f"{cue}.wav")) as w:
            assert w.getframerate() == 44100 and w.getnchannels() == 1 and w.getsampwidth() == 2
