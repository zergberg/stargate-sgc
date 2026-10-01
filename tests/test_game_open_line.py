"""Part 7: a check-in's open prompt keeps its wormhole up, holding the gate, for up to 38 game minutes."""
import json

from sgc.game import clock
from sgc.game import engine as eng
from sgc.game.state import from_dict, to_dict
from sgc.model import Step
from tests.test_game_engine import Rig, UNKNOWN
from tests.test_game_missions import CHECKIN, UNDER_FIRE, probed


def _raise_the_line(r, monkeypatch, team="SG-3"):
    """SG-3 reaches its first check-in, which raises UNDER_FIRE's prompt (a non-routine node)."""
    monkeypatch.setattr(eng, "MISS", (0,) * 4)
    w = probed(r)
    r.e.assign(w.id, team, "survey")
    r.e.advance(8 * 60 + 2)
    return w


def _raised_at(r):
    """The game minute the open alarm was raised at, backed out of its own (unrelated) decision window."""
    return r.c.alarms[0]["deadline"] - clock.window(r.e.sph)


def test_a_checkin_prompt_holds_the_gate_for_38_minutes_so_a_queued_dial_out_waits(monkeypatch):
    r = Rig(UNDER_FIRE)
    _raise_the_line(r, monkeypatch)
    assert r.alarms == ["CHECK-IN"]
    raised_at = _raised_at(r)
    assert r.c.gate_until == raised_at + eng.CHECKIN_LINE_MINUTES
    [ev] = r.c.events.find(lambda e: e.kind == "checkin_timeout")
    assert ev.due == raised_at + eng.CHECKIN_LINE_MINUTES and ev.data == {"mission": 1, "team": "SG-3"}

    w2 = r.world(6)
    assert r.e.probe(w2.id).startswith("MALP QUEUED")
    r.e.advance(5)
    assert w2.status == "unexplored"                      # the dial-out is still waiting for the gate
    [dial] = r.c.events.find(lambda e: e.kind == "dial_out" and e.data.get("world") == w2.id)
    assert dial.due == raised_at + eng.CHECKIN_LINE_MINUTES

    r.e.advance(dial.due - r.c.now + clock.GATE_MINUTES["probe"] + 1)
    assert w2.status == "probed"                          # once the line is gone, the gate is free again


def test_answering_in_time_logs_orders_sent_and_frees_the_gate(monkeypatch):
    r = Rig(UNDER_FIRE)
    _raise_the_line(r, monkeypatch)
    r.e.advance(5)                                         # well inside the 38-minute window
    r.e.key("1")                                           # recall
    assert "ORDERS SENT TO SG-3" in r.logs
    assert "SG-3 RECALLED" in r.logs
    assert r.c.gate_until <= r.c.now
    assert not r.c.events.find(lambda e: e.kind == "checkin_timeout")


def test_38_minutes_unanswered_drops_the_line_but_leaves_the_prompt_open(monkeypatch):
    r = Rig(UNDER_FIRE)
    _raise_the_line(r, monkeypatch)
    r.e.advance(eng.CHECKIN_LINE_MINUTES + 1)
    assert "WORMHOLE LOST — SG-3 WILL RECEIVE ORDERS AT NEXT CONTACT" in r.logs
    assert r.c.gate_until <= r.c.now
    assert r.c.alarms and r.c.alarms[0]["scenario"] == "t_fire"              # the prompt is still open
    before = len(r.logs)
    r.e.key("1")                                           # answered late: recall still applies
    assert "SG-3 RECALLED" in r.logs[before:]
    assert not any("ORDERS SENT TO SG-3" in line for line in r.logs[before:])


def test_a_routine_checkin_is_unchanged(monkeypatch):
    r = Rig(CHECKIN)
    _raise_the_line(r, monkeypatch)
    assert r.c.alarms == [] and not r.c.events.find(lambda e: e.kind == "checkin_timeout")
    assert r.c.gate_until < r.c.now + eng.CHECKIN_LINE_MINUTES


def test_only_a_checkin_scenario_opens_a_line():
    r = Rig(UNKNOWN)
    r.c.events.push(r.c.now, "incoming")
    r.e.advance(1)
    assert r.alarms == ["INCOMING"]
    assert not r.c.events.find(lambda e: e.kind == "checkin_timeout")
    assert r.c.gate_until < r.c.now + eng.CHECKIN_LINE_MINUTES


def test_a_save_mid_line_loses_the_line_on_load(monkeypatch):
    r = Rig(UNDER_FIRE)
    _raise_the_line(r, monkeypatch)
    assert r.c.events.find(lambda e: e.kind == "checkin_timeout")
    b = Rig(UNDER_FIRE, campaign=from_dict(json.loads(json.dumps(to_dict(r.c)))))
    assert not b.c.events.find(lambda e: e.kind == "checkin_timeout")
    assert "WORMHOLE LOST — SG-3 WILL RECEIVE ORDERS AT NEXT CONTACT" in b.logs
    assert b.c.gate_until <= b.c.now
    assert b.c.alarms and b.c.alarms[0]["scenario"] == "t_fire"              # the prompt still waits for an order
    b.e.key("1")
    assert "SG-3 RECALLED" in b.logs


def test_the_checkin_visual_leaves_the_wormhole_up_when_a_prompt_is_coming():
    r = Rig(director=True)
    closed = [st.log for st in r.e._v_checkin("SG-3") if st.log]
    open_line = [st.log for st in r.e._v_checkin("SG-3", keep_open=True) if st.log]
    assert "WORMHOLE DISENGAGED" in closed
    assert "WORMHOLE DISENGAGED" not in open_line
    assert open_line and open_line[:-1] == closed[:len(open_line) - 1]        # same intro, just no shutdown


def test_the_open_line_panel_shows_the_team_and_time_left():
    r = Rig(director=True)
    r.c.events.push(r.c.now + 20, "checkin_timeout", {"mission": 1, "team": "SG-3"})
    r.e.update(0.0)
    assert r.d.scene.panel_title == "TEAM ON THE LINE · SG-3"
    assert r.d.scene.panel_rows == [("TIME LEFT", "20 MIN")]


def test_the_open_line_panel_leaves_another_scenes_panel_alone():
    """An uplink feed (or any other scene) owns the panel while it plays; the open line must not clobber it."""
    r = Rig(director=True)
    r.d.scene.panel_title = "UPLINK · ABYDOS"
    r.d.scene.panel_rows = [("PROGRESS", "40%")]
    r.c.events.push(r.c.now + 20, "checkin_timeout", {"mission": 1, "team": "SG-3"})
    r.e.update(0.0)
    assert r.d.scene.panel_title == "UPLINK · ABYDOS"
    assert r.d.scene.panel_rows == [("PROGRESS", "40%")]


def test_a_checkin_timeout_closes_the_wormhole_even_with_other_traffic_showing(monkeypatch):
    r = Rig(UNDER_FIRE, director=True)
    _raise_the_line(r, monkeypatch)
    for _ in range(400):                                    # drain SG-3's departure: it shuts down on its own
        if r.d.idle:
            break
        r.d.advance(0.1)
    r.e.update(0.0)                                         # the line's panel is up, as usual
    assert r.d.scene.panel_title == "TEAM ON THE LINE · SG-3"
    r.e._queue([Step(1000.0, None)])                        # some other traffic is showing, unrelated to the line
    assert r.e.showing
    r.e.advance(eng.CHECKIN_LINE_MINUTES + 1)
    assert "WORMHOLE LOST — SG-3 WILL RECEIVE ORDERS AT NEXT CONTACT" in r.logs
    r.d.advance(1001.0)                                     # let the other traffic finish
    for _ in range(400):
        if r.d.idle:
            break
        r.d.advance(0.1)
    assert r.d.idle
    assert r.d.scene.horizon == "off"
    assert r.d.scene.panel_title != "TEAM ON THE LINE · SG-3"


def test_answering_a_checkin_early_frees_a_deferred_dial_out_in_order(monkeypatch):
    r = Rig(UNDER_FIRE)
    _raise_the_line(r, monkeypatch)
    wa, wb = r.world(6), r.world(8)
    assert r.e.probe(wa.id).startswith("MALP QUEUED")
    r.e.advance(1)                                          # the line is open: A waits for the busy gate
    [dial_a] = r.c.events.find(lambda e: e.kind == "dial_out" and e.data.get("world") == wa.id)
    assert dial_a.due == r.c.gate_until

    r.e.key("2")                                            # hold position: answered well inside the window
    assert r.c.gate_until <= r.c.now
    [dial_a] = r.c.events.find(lambda e: e.kind == "dial_out" and e.data.get("world") == wa.id)
    assert dial_a.due == r.c.now                            # A is freed now, not left at the old deadline

    assert r.e.probe(wb.id).startswith("MALP QUEUED")       # B is queued only after A was freed
    r.e.advance(1)
    assert f"MALP SENT TO {wa.name.upper()}" in r.logs
    assert f"MALP SENT TO {wb.name.upper()}" not in r.logs  # B still waits behind A

    r.e.advance(clock.GATE_MINUTES["probe"] + 1)
    assert f"MALP SENT TO {wb.name.upper()}" in r.logs
    assert r.logs.index(f"MALP SENT TO {wa.name.upper()}") < r.logs.index(f"MALP SENT TO {wb.name.upper()}")


def test_a_reload_frees_a_deferred_dial_out_in_order(monkeypatch):
    r = Rig(UNDER_FIRE)
    _raise_the_line(r, monkeypatch)
    wa, wb = r.world(6), r.world(8)
    wa_name, wa_id, wb_id, wb_name = wa.name, wa.id, wb.id, wb.name
    assert r.e.probe(wa_id).startswith("MALP QUEUED")
    r.e.advance(1)
    [dial_a] = r.c.events.find(lambda e: e.kind == "dial_out" and e.data.get("world") == wa_id)
    assert dial_a.due == r.c.gate_until

    b = Rig(UNDER_FIRE, campaign=from_dict(json.loads(json.dumps(to_dict(r.c)))))
    [dial_a2] = b.c.events.find(lambda e: e.kind == "dial_out" and e.data.get("world") == wa_id)
    assert dial_a2.due == b.c.now                           # reload frees it now, not at the old deadline

    assert b.e.probe(wb_id).startswith("MALP QUEUED")       # B is queued only after the reload freed A
    b.e.advance(1)
    assert f"MALP SENT TO {wa_name.upper()}" in b.logs
    assert f"MALP SENT TO {wb_name.upper()}" not in b.logs  # B still waits behind A


def test_answering_a_checkin_early_keeps_a_naturally_due_incoming_on_schedule(monkeypatch):
    """An incoming due 20 minutes ahead on its own was never deferred for the busy gate: answering the
    line early must not wake it along with whatever genuinely was waiting."""
    r = Rig(UNDER_FIRE)
    _raise_the_line(r, monkeypatch)
    due = r.c.now + 20                                      # inside the 38-minute window, but never deferred
    r.c.events.push(due, "incoming")

    r.e.key("2")                                            # hold position: answered well inside the window
    assert r.c.gate_until <= r.c.now
    [ev] = r.c.events.find(lambda e: e.kind == "incoming")
    assert ev.due == due                                    # untouched: it was never waiting on the gate


def test_a_reload_keeps_a_naturally_due_incoming_on_schedule(monkeypatch):
    r = Rig(UNDER_FIRE)
    _raise_the_line(r, monkeypatch)
    due = r.c.now + 20
    r.c.events.push(due, "incoming")

    b = Rig(UNDER_FIRE, campaign=from_dict(json.loads(json.dumps(to_dict(r.c)))))
    assert b.c.gate_until <= b.c.now
    [ev] = b.c.events.find(lambda e: e.kind == "incoming")
    assert ev.due == due
