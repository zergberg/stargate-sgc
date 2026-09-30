import random

from sgc import sequences as sq
from sgc.model import Prompt, Scene


def play(steps, scene, dt=0.05):
    logs, cues = [], []
    for st in steps:
        if st.log:
            logs.append(st.log)
        cues += st.cues
        if st.duration <= 0:
            if st.update:
                st.update(scene, 1.0)
            continue
        t = 0.0
        while t < st.duration:
            t = min(st.duration, t + dt)
            if st.update:
                st.update(scene, t / st.duration)
    return logs, cues


def test_scene_defaults_to_gate_room_without_prompt():
    s = Scene()
    assert s.view_p == 1.0 and s.muzzle == [] and s.prompt is None
    p = Prompt("DECISION", "text", [("Open", True)], 12.0, 12.0)
    assert p.options[0] == ("Open", True)


def test_walks_move_view_both_ways():
    s = Scene(view_p=0.0)
    logs, cues = play(sq.to_gateroom(2.0), s)
    assert s.view_p == 1.0 and "door" in cues and "footsteps" in cues and logs
    play(sq.to_briefing(2.0), s)
    assert s.view_p == 0.0


def test_firefight_flashes_then_clears():
    s = Scene()
    seen = []
    steps = sq.firefight(4.0, random.Random(3), True)
    for st in steps:
        if st.duration > 0:
            st.update(s, 0.5)
            seen.append(len(s.muzzle))
    play(steps, s)
    assert any(seen) and s.muzzle == [] and s.alert == "normal"


def test_hot_firefight_leaves_red_alert():
    s = Scene()
    play(sq.firefight(2.0, random.Random(1), None), s)
    assert s.alert == "red"


def test_bomb_puts_a_crate_on_the_ramp():
    s = Scene()
    logs, _ = play(sq.bomb(2.0), s)
    assert s.figures and s.figures[0].kind == "crate" and s.alert == "red"
    assert any("NAQUADAH" in line for line in logs)


def test_reset_clears_muzzle():
    s = Scene()
    s.muzzle = [[0.0, 0.5, 1.0]]
    sq.reset_scene(s)
    assert s.muzzle == []
