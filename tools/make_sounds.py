#!/usr/bin/env python3
"""Generate the `synth` sound pack: original gate sound effects, stdlib only.

Usage: python tools/make_sounds.py assets/sounds/synth

Every cue is built from oscillators, filtered noise, envelopes and a small
reverb, with fixed random seeds so the output is identical on every run.
Loop cues are made seamless by crossfading their tail into their head.
"""
from __future__ import annotations

import math
import random
import sys
import wave
from array import array
from pathlib import Path

RATE = 44100
CUES = ("ring_spin", "chevron_lock", "kawoosh", "wormhole_hum", "shutdown", "iris_close",
        "iris_open", "iris_impact", "klaxon", "dial_fail", "idc_accept")
LOOPS = {"ring_spin", "wormhole_hum", "klaxon"}
TAU = 2 * math.pi


# ---------------------------------------------------------------- building blocks

def n(sec: float) -> int:
    return int(round(sec * RATE))


def silence(sec: float) -> list[float]:
    return [0.0] * n(sec)


def sweep(f0: float, f1: float, sec: float, curve: str = "exp"):
    """Frequency function of time going f0 -> f1 over `sec` seconds, then holding."""
    def f(t: float) -> float:
        p = min(1.0, t / sec) if sec > 0 else 1.0
        return f0 * (f1 / f0) ** p if curve == "exp" else f0 + (f1 - f0) * p
    return f


def osc(sec: float, freq, shape: str = "sine", vibrato: tuple[float, float] = (0.0, 0.0)) -> list[float]:
    """Oscillator; `freq` is a number or a function of time. vibrato = (rate Hz, depth fraction)."""
    out, phase = [], 0.0
    vr, vd = vibrato
    for i in range(n(sec)):
        t = i / RATE
        f = freq(t) if callable(freq) else freq
        if vd:
            f *= 1 + vd * math.sin(TAU * vr * t)
        phase = (phase + f / RATE) % 1.0
        if shape == "sine":
            out.append(math.sin(TAU * phase))
        elif shape == "saw":
            out.append(2 * phase - 1)
        else:  # square
            out.append(1.0 if phase < 0.5 else -1.0)
    return out


def noise(sec: float, rng: random.Random, color: str = "white") -> list[float]:
    out = []
    if color == "white":
        return [rng.uniform(-1, 1) for _ in range(n(sec))]
    if color == "brown":
        v = 0.0
        for _ in range(n(sec)):
            v = (v + rng.uniform(-1, 1) * 0.08) * 0.995
            out.append(v * 4)
        return out
    # pink (Paul Kellet's economy filter)
    b0 = b1 = b2 = 0.0
    for _ in range(n(sec)):
        w = rng.uniform(-1, 1)
        b0 = 0.99765 * b0 + w * 0.0990460
        b1 = 0.96300 * b1 + w * 0.2965164
        b2 = 0.57000 * b2 + w * 1.0526913
        out.append((b0 + b1 + b2 + w * 0.1848) * 0.2)
    return out


def lowpass(x: list[float], cutoff) -> list[float]:
    """One-pole low-pass; cutoff is Hz or a function of time."""
    out, y = [], 0.0
    const = None if callable(cutoff) else 1 - math.exp(-TAU * cutoff / RATE)
    for i, v in enumerate(x):
        a = const if const is not None else 1 - math.exp(-TAU * cutoff(i / RATE) / RATE)
        y += a * (v - y)
        out.append(y)
    return out


def highpass(x: list[float], cutoff: float) -> list[float]:
    return [a - b for a, b in zip(x, lowpass(x, cutoff))]


def bandpass(x: list[float], f0: float, q: float) -> list[float]:
    """RBJ biquad band-pass (0 dB peak)."""
    w0 = TAU * f0 / RATE
    alpha = math.sin(w0) / (2 * q)
    b0, b2 = alpha, -alpha
    a0, a1, a2 = 1 + alpha, -2 * math.cos(w0), 1 - alpha
    b0, b2, a1, a2 = b0 / a0, b2 / a0, a1 / a0, a2 / a0
    out, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
    for v in x:
        y = b0 * v + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, v, y1, y
        out.append(y)
    return out


def env(sec: float, attack: float = 0.005, decay: float = 0.0, hold: float = 0.0, release: float = 0.0) -> list[float]:
    """Linear attack, optional hold, exponential decay (rate per second), optional linear release at the end."""
    out = []
    total = n(sec)
    for i in range(total):
        t = i / RATE
        if t < attack:
            v = t / attack
        elif t < attack + hold:
            v = 1.0
        else:
            v = math.exp(-decay * (t - attack - hold))
        if release and t > sec - release:
            v *= max(0.0, (sec - t) / release)
        out.append(v)
    return out


def mul(*sigs: list[float]) -> list[float]:
    length = min(len(s) for s in sigs)
    out = list(sigs[0][:length])
    for s in sigs[1:]:
        for i in range(length):
            out[i] *= s[i]
    return out


def gain(x: list[float], g: float) -> list[float]:
    return [v * g for v in x]


def place(dst: list[float], src: list[float], at: float, g: float = 1.0) -> None:
    """Mix `src` into `dst` starting at time `at` (extends dst if needed)."""
    start = n(at)
    if len(dst) < start + len(src):
        dst.extend([0.0] * (start + len(src) - len(dst)))
    for i, v in enumerate(src):
        dst[start + i] += v * g


def reverb(x: list[float], wet: float = 0.3, tail: float = 0.6) -> list[float]:
    """Small Schroeder-style reverb: four feedback combs in parallel."""
    y = x + [0.0] * n(tail)
    acc = [0.0] * len(y)
    for delay_ms, fb in ((29.7, 0.72), (37.1, 0.70), (41.1, 0.68), (43.7, 0.66)):
        d = n(delay_ms / 1000)
        buf = [0.0] * len(y)
        for i in range(len(y)):
            buf[i] = y[i] + (fb * buf[i - d] if i >= d else 0.0)
        for i in range(len(y)):
            acc[i] += buf[i] * 0.25
    return [dry * (1 - wet) + w * wet for dry, w in zip(y, acc)]


def normalize(x: list[float], peak: float = 0.89) -> list[float]:
    m = max(abs(v) for v in x) or 1.0
    return [v * peak / m for v in x]


def loopify(x: list[float], xfade: float = 0.05) -> list[float]:
    """Crossfade the tail into the head so the sound loops without a click."""
    k = n(xfade)
    head = x[:-k]
    tail = x[-k:]
    for i in range(k):
        w = i / k
        head[i] = head[i] * w + tail[i] * (1 - w)
    return head


def burst(sec: float, rng: random.Random, lp: float, decay: float) -> list[float]:
    return mul(lowpass(noise(sec, rng), lp), env(sec, 0.001, decay))


def thump(sec: float, f0: float, f1: float, decay: float) -> list[float]:
    return mul(osc(sec, sweep(f0, f1, sec * 0.5)), env(sec, 0.002, decay))


def metal(sec: float, partials: tuple[tuple[float, float], ...], decay: float) -> list[float]:
    out = silence(sec)
    for f, g in partials:
        place(out, osc(sec, f), 0, g)
    return mul(out, env(sec, 0.001, decay))


# ---------------------------------------------------------------- the cues

def ring_spin(rng: random.Random) -> list[float]:
    sec = 2.05
    out = gain(lowpass(noise(sec, rng, "brown"), 300), 0.5)
    for k in range(16):  # glyph ticks at 8 Hz
        place(out, burst(0.05, rng, 1200, 60), k / 8, 0.35)
    motor = mul(osc(sec, 55), [0.75 + 0.25 * math.sin(TAU * 0.5 * i / RATE) for i in range(n(sec))])
    place(out, motor, 0, 0.3)
    place(out, osc(sec, 110), 0, 0.08)
    return loopify(normalize(out[:n(sec)]))


def chevron_lock(rng: random.Random) -> list[float]:
    out = silence(0.9)
    place(out, burst(0.04, rng, 2000, 80), 0.0, 0.5)             # clamp
    place(out, thump(0.3, 150, 130, 25), 0.0, 0.6)
    place(out, thump(0.6, 90, 70, 12), 0.13, 0.9)                 # lock
    place(out, burst(0.08, rng, 1500, 50), 0.13, 0.6)
    place(out, metal(0.7, ((520, 0.15), (1310, 0.1), (2270, 0.07)), 6), 0.13)
    return normalize(reverb(out, 0.25, 0.4))


def kawoosh(rng: random.Random) -> list[float]:
    sec = 2.4

    def cut(t: float) -> float:
        return 200 * (6000 / 200) ** (t / 0.35) if t < 0.35 else 6000 * (400 / 6000) ** ((t - 0.35) / (sec - 0.35))
    body = lowpass(noise(sec, rng), cut)
    amp = []
    for i in range(n(sec)):
        t = i / RATE
        a = t / 0.05 if t < 0.05 else math.exp(-2.0 * max(0.0, t - 0.3))
        if t > 0.4:
            a *= 1 - 0.3 * (0.5 + 0.5 * math.sin(TAU * 7 * t))   # watery wobble
        amp.append(a)
    out = mul(body, amp)
    place(out, mul(osc(sec, sweep(60, 40, 0.8)), env(sec, 0.02, 3)), 0, 0.8)   # boom
    return normalize(reverb(out, 0.35, 0.6))


def wormhole_hum(rng: random.Random) -> list[float]:
    sec = 4.05
    out = silence(sec)
    for f, g in ((60, 0.4), (60.5, 0.3), (90, 0.25), (90.25, 0.2), (120, 0.2), (120.75, 0.15)):
        place(out, osc(sec, f), 0, g)
    shimmer = bandpass(noise(sec, rng, "pink"), 700, 1.2)
    lfo = [0.6 + 0.4 * math.sin(TAU * 0.5 * i / RATE) for i in range(n(sec))]
    place(out, mul(shimmer, lfo), 0, 0.6)
    return loopify(normalize(out[:n(sec)]))


def shutdown(rng: random.Random) -> list[float]:
    sec = 1.6
    hum = osc(sec, sweep(90, 30, 1.2))
    out = mul(hum, [max(0.0, 1 - i / n(1.2)) for i in range(n(sec))])
    place(out, mul(osc(sec, sweep(180, 60, 1.2)), [max(0.0, 1 - i / n(1.0)) for i in range(n(sec))]), 0, 0.3)
    suck = lowpass(noise(sec, rng), sweep(3000, 150, 1.0))
    suck_amp = [min(1.0, (i / RATE) / 0.9) if i < n(0.95) else max(0.0, 1 - (i - n(0.95)) / n(0.08)) for i in range(n(sec))]
    place(out, mul(suck, suck_amp), 0, 0.5)
    place(out, thump(0.4, 70, 55, 15), 1.2, 0.5)
    return normalize(reverb(out, 0.3, 0.5))


def _iris(rng: random.Random, closing: bool) -> list[float]:
    sec = 1.2
    whine_f = sweep(900, 1400, 0.9, "lin") if closing else sweep(1400, 900, 0.9, "lin")
    whine = [a + 0.3 * b + 0.15 * c for a, b, c in zip(osc(sec, whine_f), osc(sec, lambda t: 2 * whine_f(t)),
                                                         osc(sec, lambda t: 3 * whine_f(t)))]
    out = mul(whine, env(sec, 0.05, 0.0, release=0.3))
    out = gain(out, 0.25)
    for at in (0.1, 0.3, 0.5, 0.7):                                  # blades sliding
        place(out, mul(highpass(noise(0.08, rng), 3000), env(0.08, 0.002, 40)), at, 0.3)
    if closing:                                                      # final clang
        place(out, thump(0.5, 120, 105, 10), 0.95, 0.8)
        place(out, metal(0.5, ((700, 0.2), (1650, 0.12), (2900, 0.08)), 7), 0.95)
        place(out, burst(0.05, rng, 2500, 60), 0.95, 0.4)
    else:                                                            # soft settle
        place(out, thump(0.25, 95, 85, 20), 1.0, 0.3)
    return normalize(reverb(out, 0.3, 0.4))


def iris_close(rng: random.Random) -> list[float]:
    return _iris(rng, True)


def iris_open(rng: random.Random) -> list[float]:
    return _iris(rng, False)


def iris_impact(rng: random.Random) -> list[float]:
    out = gain(thump(0.7, 70, 45, 9), 1.0)
    place(out, burst(0.02, rng, 4000, 150), 0, 0.6)
    place(out, metal(0.6, ((380, 0.12), (1020, 0.08)), 8), 0)
    return normalize(reverb(out, 0.3, 0.4))


def klaxon(rng: random.Random) -> list[float]:
    horn = [a + 0.7 * b for a, b in zip(osc(1.0, 311, "saw", (5, 0.01)), osc(1.0, 466, "saw", (5, 0.01)))]
    horn = mul(lowpass(horn, 2500), env(1.0, 0.03, 0.0, release=0.08))
    return normalize(horn + silence(0.5))


def dial_fail(rng: random.Random) -> list[float]:
    out = gain(mul(lowpass(noise(0.6, rng, "brown"), 300), [1 - i / n(0.6) for i in range(n(0.6))]), 0.5)
    t = 0.0
    for k in range(6):                                               # ticks slowing to a halt
        place(out, burst(0.05, rng, 1200, 60), t, 0.35)
        t += 0.06 + 0.03 * k
    fail = [a + 0.3 * b for a, b in zip(osc(1.0, sweep(400, 80, 1.0)), osc(1.0, sweep(400, 80, 1.0), "square"))]
    place(out, mul(fail, env(1.0, 0.02, 0.0, release=0.2)), 0.3, 0.35)
    place(out, thump(0.3, 75, 65, 14), 1.2, 0.8)
    place(out, burst(0.05, rng, 1200, 60), 1.2, 0.4)
    return normalize(reverb(out, 0.25, 0.3))


def idc_accept(rng: random.Random) -> list[float]:
    out = silence(0.5)
    beep = mul([a + 0.2 * b for a, b in zip(osc(0.09, 1200), osc(0.09, 2400))], env(0.09, 0.005, 0.0, release=0.005))
    place(out, beep, 0.0)
    place(out, beep, 0.15)
    return normalize(out)


def synth(cue: str) -> list[float]:
    """Render one cue as mono floats in [-1, 1]."""
    rng = random.Random(f"sgc-{cue}")
    return globals()[cue](rng)


def write_wav(path: Path, samples: list[float]) -> None:
    data = array("h", (int(max(-1.0, min(1.0, v)) * 32767) for v in samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())


def main(outdir) -> None:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for cue in CUES:
        write_wav(outdir / f"{cue}.wav", synth(cue))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "assets/sounds/synth")
