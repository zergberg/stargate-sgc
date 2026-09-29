import random

from sgc import sequences as sq
from sgc.addresses import AddressPicker, load_canon
from sgc.config import Config
from sgc.director import Director
from sgc.events import REGISTRY
from sgc.model import Scene


def run(steps, scene, dt=0.05):
    """Play steps against a scene the way the director does; return (logs, cues)."""
    logs, cues = [], []
    for st in steps:
        if st.log:
            logs.append(st.log)
        cues += st.cues
        t = 0.0
        if st.duration <= 0 and st.update:
            st.update(scene, 1.0)
        while t < st.duration:
            t = min(st.duration, t + dt)
            if st.update:
                st.update(scene, t / st.duration)
    return logs, cues


def canon():
    return {a.name: a for a in load_canon()}


def test_lock_order_7():
    s = Scene()
    steps, _ = sq.dial(canon()["Abydos"], 0.0, 1.0)
    order = []
    for st in steps:
        before = set(s.lit)
        run([st], s)
        order += sorted(s.lit - before)
    assert order == [1, 2, 3, 6, 7, 8, 0] and s.locked == 7


def test_lock_order_9_and_8():
    for name, n in (("Atlantis", 8), ("Destiny", 9)):
        s = Scene()
        steps, _ = sq.dial(canon()[name], 0.0, 1.0)
        run(steps, s)
        assert s.lit == set(sq.LOCK_ORDER[n]) and s.locked == n


def test_ring_ends_on_point_of_origin():
    s = Scene()
    steps, final = sq.dial(canon()["Chulak"], 0.0, 1.0)
    run(steps, s)
    diff = (s.ring_angle - sq.angle_for_glyph(1)) % 360
    assert min(diff, 360 - diff) < 1e-6 and abs(final - s.ring_angle) < 1e-6


def test_ring_alternates_direction_and_really_spins():
    s = Scene()
    steps, _ = sq.dial(canon()["Abydos"], 0.0, 1.0)
    spins = [st for st in steps if "loop:ring_spin" in st.cues]
    deltas = []
    for st in spins:
        before = s.ring_angle
        run([st], s)
        deltas.append(s.ring_angle - before)
        run([steps[steps.index(st) + 1]], s)
    assert all(abs(d) >= 40 for d in deltas)
    assert all((a > 0) != (b > 0) for a, b in zip(deltas, deltas[1:]))


def test_shutdown_clears():
    s = Scene(lit={1, 2, 3, 6, 7, 8, 0}, horizon="open", locked=7)
    run(sq.shutdown(1.0), s)
    assert s.lit == set() and s.horizon == "off" and s.locked == 0


def director(**cfg):
    return Director(Config(**cfg), random.Random(3), AddressPicker(load_canon(), 0.6, random.Random(4)), REGISTRY)


def test_director_runs_every_event_to_completion():
    for name in REGISTRY:
        d = director()
        d.queue_event(name)
        logs_all = []
        for _ in range(int(200 / 0.1)):
            logs, _ = d.advance(0.1)
            logs_all += logs
        assert logs_all, name
        assert d.scene.horizon in ("off", "open", "kawoosh", "collapse")


def test_large_dt_does_not_skip_state():
    d = director()
    d.queue_event("science")
    d.advance(15.0)
    d.advance(15.0)
    assert d.scene.locked >= 7 or d.scene.horizon != "off"


def test_exit_finishes_within_duration():
    d = director(exit_duration=6.0)
    d.advance(20.0)
    d.begin_exit()
    t = 0.0
    while not d.finished and t < 20:
        d.advance(0.1)
        t += 0.1
    assert d.finished and 5.5 <= t <= 6.6


def test_skip_moves_to_a_new_cycle():
    d = director()
    d.queue_event("science")
    d.advance(25.0)                     # gate open during science
    first = d.scene.address
    d.skip()
    for _ in range(100):
        d.advance(0.1)
    assert d.scene.address is None or d.scene.address != first
