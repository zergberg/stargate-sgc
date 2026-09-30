import pytest

from sgc.game import clock
from sgc.game import engine as eng
from sgc.game import uav
from sgc.game.world import World
from tests.test_game_engine import Rig, play


def total(steps):
    return sum(st.duration for st in steps)


def step(steps, log):
    return next(st for st in steps if st.log == log)


def uav_of(scene):
    return next(f for f in scene.figures if f.kind == "uav")


# ---------------------------------------------------------------- the feed as data

def test_the_tint_only_uses_what_the_player_has_been_told():
    w = World("P3X-774", (1, 2, 3, 4, 5, 6), env="toxic")
    assert uav.tint(w, {}) == "neutral"                                   # hidden: no spoilers
    assert uav.tint(w, {"env": "toxic atmosphere"}) == "toxic"            # this report says so
    w.seen["env"] = "toxic atmosphere"
    assert uav.tint(w, {}) == "toxic"                                     # earlier intel says so
    w2 = World("P2A-018", (7, 8, 9, 10, 11, 12), env="extreme")
    assert uav.tint(w2, {"env": "extreme temperatures"}) in ("ice", "volcanic")
    assert uav.tint(w2, {"env": "breathable atmosphere"}) in ("forest", "ocean")
    assert uav.tint(w2, {"env": "high radiation"}) == "desert"


def test_the_same_world_always_gets_the_same_seed_and_readouts():
    w = World("P3X-774", (1, 2, 3, 4, 5, 6))
    assert uav.seed(w) == uav.seed(World("P3X-774", (9, 9, 9, 9, 9, 9))) != uav.seed(World("P3X-775", (1,) * 6))
    sd = uav.seed(w)
    assert uav.rows(sd, 0.5) == uav.rows(sd, 0.5) and uav.rows(sd, 0.0) != uav.rows(sd, 1.0)
    assert [k for k, _ in uav.rows(sd, 0.2)] == ["ALT", "HDG", "SPEED", "FUEL"]
    assert uav.hud(sd, 0.2).startswith("UAV  ALT ") and "M  HDG " in uav.hud(sd, 0.2)


@pytest.mark.parametrize("seen,box", [({"inhabitants": "settlement"}, True), ({"features": "ruins"}, True),
                                      ({"inhabitants": "no settlements", "life": "none detected"}, False),
                                      ({}, False)])
def test_a_contact_is_boxed_when_the_readings_name_one(seen, box):
    assert uav.contact(seen) is box


# ---------------------------------------------------------------- the launch and the flight home

def test_a_uav_launch_fires_off_the_rail_and_climbs_into_the_gate():
    r = Rig(director=True)
    w = r.world(5, env="normal")
    steps = r.e._v_drone(w, "uav")
    logs = [st.log for st in steps if st.log]
    assert logs.index("UAV LAUNCHED") < logs.index("UAV IN TRANSIT")
    assert abs(total(steps) - total(r.e._v_drone(w, "malp"))) <= 1.0
    s = r.d.scene
    step(steps, "UAV LAUNCHED").update(s, 0.0)
    assert sorted(f.kind for f in s.figures) == ["rail", "uav"] and uav_of(s).pos == eng.UAV_RAIL
    assert uav_of(s).alt == 0.0 and uav_of(s).facing == "away"
    fly = steps[steps.index(step(steps, "UAV LAUNCHED")) + 1]
    fly.update(s, 0.5)
    assert eng.UAV_RAIL < uav_of(s).pos < 0.95 and uav_of(s).alt > eng.UAV_CRUISE * 0.5     # an ease-out climb
    fly.update(s, 1.0)
    assert uav_of(s).pos == pytest.approx(0.95) and uav_of(s).alt == pytest.approx(eng.UAV_CRUISE)
    through = step(steps, "UAV IN TRANSIT")
    through.update(s, 0.5)
    assert s.splashes
    through.update(s, 1.0)
    assert s.figures == [] and s.splashes == []


def test_a_uav_flies_home_nose_first_and_lands():
    r = Rig(director=True)
    w = r.world(5, env="normal")
    steps = r.e._v_drone(w, "uav", home=True)
    assert abs(total(steps) - total(r.e._v_drone(w, "malp", home=True))) <= 1.0
    s = r.d.scene
    down = step(steps, "UAV RETURNING THROUGH THE GATE")
    down.update(s, 0.0)
    assert uav_of(s).pos == pytest.approx(0.95) and uav_of(s).alt == pytest.approx(eng.UAV_CRUISE)
    assert uav_of(s).facing == "toward"
    down.update(s, 0.5)
    mid = uav_of(s)
    assert 0.05 < mid.pos < 0.95 and 0 < mid.alt < eng.UAV_CRUISE
    down.update(s, 1.0)
    assert uav_of(s).pos == pytest.approx(0.05) and uav_of(s).alt == 0.0
    landed = step(steps, "UAV RECOVERED")
    landed.update(s, 0.5)
    assert uav_of(s).alt == 0.0 and uav_of(s).pos < 0.05
    landed.update(s, 1.0)
    assert s.figures == []


def test_the_malp_still_rolls():
    r = Rig(director=True)
    s = r.d.scene
    roll = step(r.e._v_drone(r.world(5), "malp"), "MALP IN TRANSIT")
    roll.update(s, 0.5)
    assert [f.kind for f in s.figures] == ["malp"]


# ---------------------------------------------------------------- the feed and losing it

def test_a_uav_report_plays_the_aerial_feed_and_a_malp_report_does_not():
    r = Rig(director=True)
    w = r.world(5, env="toxic", inhabitants="human")
    seen = {"env": "toxic atmosphere", "life": "life signs", "inhabitants": "settlement"}
    show = step(r.e._v_telemetry(w, seen, "uav"), "TELEMETRY RECEIVED")
    assert show.duration == 6.0
    s = r.d.scene
    show.update(s, 0.5)
    f = s.feed
    assert f.seed == uav.seed(w) and f.tint == "toxic" and f.p == 0.5 and f.lost == 0.0 and f.contact
    assert f.hud == uav.hud(f.seed, 0.5)
    assert [k for k, _ in s.panel_rows] == ["ALT", "HDG", "SPEED", "FUEL", "ENV", "LIFE", "INHABITANTS"]
    show.update(s, 1.0)
    assert s.feed is None
    step(r.e._v_telemetry(w, seen), "TELEMETRY RECEIVED").update(s, 0.5)
    assert s.feed is None and s.panel_rows == [(k.upper(), v) for k, v in seen.items()]


def test_the_signal_lost_visual_goes_to_static():
    r = Rig(director=True)
    w = r.world(5, env="toxic", seen={})                          # nothing known about it yet
    steps = r.e._v_signal_lost(w, {})
    s = r.d.scene
    static = step(steps, "UAV SIGNAL LOST")
    assert static.duration == pytest.approx(1.5)
    static.update(s, 0.5)
    assert s.feed.lost == pytest.approx(0.5) and s.feed.tint == "neutral"     # the env is still hidden
    static.update(s, 1.0)
    assert s.feed.lost == 1.0
    assert Rig().e._v_signal_lost(w, {}) == []                                # headless: nothing to show


def test_a_uav_shot_down_plays_signal_lost_and_a_malp_does_not(monkeypatch):
    monkeypatch.setitem(eng.DESTROYED, "extreme", 100)
    lines = {}
    for drone in ("uav", "malp"):
        r = Rig(director=True, pace=10)
        w = r.world(5, env="extreme", inhabitants="none")
        (r.e.send_uav if drone == "uav" else r.e.probe)(w.id)
        lines[drone] = [line for _, line in play(r, 150, 0.1)]
    assert "UAV LAUNCHED" in lines["uav"] and "UAV SIGNAL LOST" in lines["uav"]
    assert any(line.startswith("UAV SIGNAL LOST ON") for line in lines["uav"])
    assert "UAV SIGNAL LOST" not in lines["malp"]
    assert any(line.startswith("MALP SIGNAL LOST ON") for line in lines["malp"])


def test_a_captured_uav_plays_signal_lost(monkeypatch):
    monkeypatch.setitem(eng.DESTROYED, "normal", -10)          # the UAV's +10 over a garrison cancels out
    monkeypatch.setitem(eng.CAPTURED, "jaffa", 100)
    r = Rig(director=True, pace=10)
    w = r.world(5, env="normal", inhabitants="jaffa")
    r.e.send_uav(w.id)
    lines = [line for _, line in play(r, 150, 0.1)]
    assert any(line.startswith("UAV CAPTURED ON") for line in lines) and "UAV SIGNAL LOST" in lines


def test_the_uav_log_reads_in_order_at_a_busy_pace(monkeypatch):
    monkeypatch.setitem(eng.DESTROYED, "normal", 0)               # it must come back to report
    r = Rig(director=True, pace=10)
    w = r.world(5, env="normal", inhabitants="none")
    name = w.name.upper()
    r.e.send_uav(w.id)
    lines = [line for _, line in play(r, 120, 0.1)]
    assert lines.index("UAV LAUNCHED") < lines.index("UAV IN TRANSIT") < lines.index(f"UAV TELEMETRY FROM {name}")
    assert "TELEMETRY RECEIVED" in lines[lines.index(f"UAV TELEMETRY FROM {name}"):]


def test_uav_visuals_do_not_change_the_campaign_at_any_frame_rate():
    a, b = Rig(director=True, pace=10), Rig(director=True, pace=10)
    for r in (a, b):
        r.e.send_uav(r.world(4, env="normal").id)
        r.e.probe(r.world(5, env="normal").id)
    play(a, 200, 1 / 30)
    play(b, 200, 1 / 7)
    for r in (a, b):
        r.e.advance(clock.START + clock.DAY - r.c.minutes)
        r.e.save_now()
    assert a.saves[-1] == b.saves[-1]
