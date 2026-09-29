import math
import pathlib
import sys
import wave
from array import array

import numpy as np
import soundfile as sf

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "tools"))
from convert_freesound import convert  # noqa: E402


def test_convert_trims_resamples_and_normalizes(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    t = np.arange(48000 * 2) / 48000
    tone = 0.5 * np.sin(2 * math.pi * 220 * t)
    sf.write(src / "tone.ogg", np.stack([tone, tone], axis=1), 48000)
    pack = {"kawoosh": {"file": "tone.ogg", "start": 0.25, "end": 1.25, "fade_in": 0.01, "fade_out": 0.05,
                        "gain": 1.0, "loop": False}}
    out = tmp_path / "out"
    convert(pack, src, out)
    with wave.open(str(out / "kawoosh.wav")) as w:
        assert w.getframerate() == 44100 and w.getnchannels() == 1
        frames = w.getnframes()
        data = array("h", w.readframes(frames))
    assert abs(frames - 44100) <= 45          # 1.0 s +- 1 ms
    assert max(abs(v) for v in data) <= 0.9 * 32767 + 1
    assert max(abs(v) for v in data) > 0.8 * 32767


def test_loop_entries_are_seamless(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    t = np.arange(44100 * 2) / 44100
    sf.write(src / "n.ogg", 0.3 * np.sin(2 * math.pi * 117.7 * t), 44100)   # a plain cut at 1.5 s would click
    pack = {"wormhole_hum": {"file": "n.ogg", "start": 0, "end": 1.5, "fade_in": 0, "fade_out": 0,
                             "gain": 1.0, "loop": True}}
    convert(pack, src, tmp_path / "out")
    with wave.open(str(tmp_path / "out" / "wormhole_hum.wav")) as w:
        data = array("h", w.readframes(w.getnframes()))
    assert abs(data[0] - data[-1]) < 0.05 * 32767
