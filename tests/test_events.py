import random

from sgc.addresses import load_canon
from sgc.events import REGISTRY, EventContext
from sgc.model import Scene
from tests.test_director import run


def ctx(scene, seed=1):
    return EventContext(addr=load_canon()[1], rng=random.Random(seed), scene=scene, speed=1.0,
                        open_scale=1.0, ring_angle=0.0)


def test_registry_names():
    assert set(REGISTRY) == {"science", "traffic", "malp", "failed_dial", "code_red", "friendly", "kawoosh_hazard"}


def test_code_red_closes_iris_and_resets():
    s = Scene()
    logs, cues = run(REGISTRY["code_red"].build(ctx(s)), s)
    assert "iris_close" in cues and "iris_open" in cues and any("IRIS" in line for line in logs)
    assert "iris_impact" in cues
    assert s.iris == 0.0 and s.alert == "normal" and s.impacts == []


def test_failed_dial_never_opens():
    s = Scene()
    logs, cues = run(REGISTRY["failed_dial"].build(ctx(s)), s)
    assert "kawoosh" not in cues and "dial_fail" in cues and s.lit == set()
    assert any("WILL NOT LOCK" in line for line in logs)


def test_traffic_moves_team_offworld_then_friendly_returns_it():
    s = Scene()
    run(REGISTRY["traffic"].build(ctx(s)), s)
    away = [t for t, v in s.teams.items() if v.startswith("OFFWORLD")]
    assert len(away) == 1
    logs, cues = run(REGISTRY["friendly"].build(ctx(s)), s)
    assert s.teams[away[0]] == "AT BASE" and "idc_accept" in cues


def test_traffic_figures_enter_the_horizon():
    s = Scene()
    seen_people = 0
    for st in REGISTRY["traffic"].build(ctx(s)):
        run([st], s)
        seen_people = max(seen_people, sum(f.kind == "person" for f in s.figures))
    assert seen_people == 4 and not s.figures


def test_malp_fills_telemetry():
    s = Scene()
    rows_seen = []
    for st in REGISTRY["malp"].build(ctx(s)):
        run([st], s)
        if s.panel_rows:
            rows_seen = s.panel_rows
    assert s.panel_title.startswith("MALP") or any(r[0] == "ATMOS" for r in rows_seen)


def test_hazard_vaporizes_crate():
    s = Scene()
    run(REGISTRY["kawoosh_hazard"].build(ctx(s)), s)
    assert not any(f.kind == "crate" for f in s.figures)


def test_every_event_ends_clean():
    for name, ev in REGISTRY.items():
        s = Scene()
        run(ev.build(ctx(s)), s)
        assert s.horizon == "off" and s.lit == set() and s.iris == 0.0 and s.alert == "normal", name
        assert not s.figures and not s.impacts, name


def test_open_scale_lengthens_gate_time():
    s1, s2 = Scene(), Scene()
    c1, c2 = ctx(s1), ctx(s2)
    c2.open_scale = 2.0
    d1 = sum(st.duration for st in REGISTRY["science"].build(c1))
    d2 = sum(st.duration for st in REGISTRY["science"].build(c2))
    assert d2 > d1 + 30


def test_friendly_brings_home_a_campaign_team_shown_as_away():
    s = Scene()
    s.teams["SG-3"] = "AWAY: ABYDOS"
    logs, _ = run(REGISTRY["friendly"].build(ctx(s)), s)
    assert any("SG-3" in line for line in logs)
