import json
import random

import pytest

from sgc.game import clock, orders, rules, world
from sgc.game.state import (ALL_TEAMS, ARC_IDS, CORE_TEAMS, RANKS, ROSTER, ArcState, CapturedDrone, Deal, Mission,
                            Team, available_teams, demote, from_dict, has_specialty, new_campaign, rank, rank_index,
                            team_names, to_dict, upgrade_v2)


def test_new_campaign_starts_at_eight_with_the_cartouche_and_the_roster():
    c = new_campaign("campaign", "officer", 42)
    assert c.minutes == clock.START and c.now == 480 and c.pace == "standard"
    assert len(c.worlds) == 20 and next(iter(c.worlds.values())).name == "Abydos"
    assert {t: tm.specialty for t, tm in c.teams.items()} == ROSTER == {
        "SG-1": "elite", "SG-2": "recon", "SG-3": "combat", "SG-4": "science"}
    assert all(tm.status == "base" and tm.idc == "valid" and tm.xp == 0 for tm in c.teams.values())
    assert c.meters == {"security": 70, "personnel": 80} and c.stock == {"malp": 4, "uav": 0}
    assert c.orders == orders.defaults() and c.alarms == [] and c.missions == []
    kinds = [e.kind for e in c.events]
    assert kinds == ["recovery_tick", "incoming", "funding_review"] and c.events.peek().due == 540
    assert list(c.events)[2].due == clock.START + 7 * clock.DAY
    assert 480 + 36 * 60 <= list(c.events)[1].due <= 480 + 96 * 60


def test_new_campaign_is_deterministic_and_keeps_the_pace():
    a, b = new_campaign("sandbox", "recruit", 5, "busy"), new_campaign("sandbox", "recruit", 5, "busy")
    assert a == b and a.pace == "busy"


def test_ranks_specialties_and_availability():
    t = Team("recon")
    assert rank(t) == "green" and rank_index(t) == 0
    for xp, name in [(3, "seasoned"), (8, "veteran"), (15, "elite"), (40, "elite")]:
        t.xp = xp
        assert rank(t) == name
    t.xp = 9
    demote(t)
    assert rank(t) == "seasoned" and t.xp == RANKS[1][1]
    demote(Team("combat"))
    assert has_specialty(Team("elite"), "diplomatic") and has_specialty(Team("recon"), "recon")
    assert not has_specialty(Team("recon"), "combat")
    c = new_campaign("campaign", "officer", 1)
    c.teams["SG-2"].status = "offworld"
    c.teams["SG-3"].until = c.now + 60
    assert available_teams(c) == ["SG-1", "SG-4"]
    c.minutes += 60
    assert available_teams(c) == ["SG-1", "SG-3", "SG-4"]


def busy_campaign():
    c = new_campaign("campaign", "commander", 9, "relaxed")
    w = list(c.worlds.values())[3]
    w.status, w.drone, w.seen = "probed", "malp", {"env": "toxic atmosphere"}
    w.names.append(("Tel'kar", "the locals", 900))
    w.reports.append((900, "MALP telemetry received."))
    w.notes.append((901, "Check the ruins."))
    c.teams["SG-3"].status, c.teams["SG-3"].where, c.teams["SG-3"].mission = "offworld", w.id, 1
    c.missions.append(Mission(1, "SG-3", w.id, "survey", 900, 900 + 24 * 60, findings=["ruins"]))
    c.events.push(1380, "checkin", {"mission": 1})
    c.inventory |= {"ally.tokra", "tech.zat"}
    c.used.add("ally.nox")
    c.alarms.append({"type": "missed_checkin", "title": "Missed check-in", "text": "SG-3 missed a check-in.",
                      "mission": 1, "deadline": 1500})
    c.minutes, c.gate_until, c.over = 1000.5, 1010, None
    rng = random.Random(5)
    rng.random()
    c.rng_state = rng.getstate()
    return c, rng


def test_round_trip_through_json_keeps_everything_in_flight():
    c, rng = busy_campaign()
    back = from_dict(json.loads(json.dumps(to_dict(c))))
    assert back == c and back.events == c.events and list(back.worlds) == list(c.worlds)
    r2 = random.Random()
    r2.setstate(back.rng_state)
    assert r2.random() == rng.random()
    assert back.mission(1).findings == ["ruins"] and back.mission(2) is None
    assert back.active_missions() == [back.mission(1)]


def test_round_trip_survives_a_pending_reinforcement():
    c = new_campaign("campaign", "officer", 3)
    w = next(iter(c.worlds))
    c.missions.append(Mission(1, "SG-2", w, "survey", 480, 480 + 24 * 60))
    c.teams["SG-2"].status, c.teams["SG-2"].where, c.teams["SG-2"].mission = "offworld", w, 1
    c.events.push(480 + 24 * 60, "team_return", {"mission": 1})
    rules.apply_all([rules.parse_effect("reinforce {team}")], c, {"team": "SG-2"})
    assert any(e.kind == "team_return" and "mission" not in e.data for e in c.events)
    back = from_dict(json.loads(json.dumps(to_dict(c))))
    assert back == c


def test_from_dict_rejects_other_versions_and_bad_data():
    d = to_dict(new_campaign("campaign", "officer", 1))
    d["version"] = 1
    with pytest.raises(ValueError):
        from_dict(d)
    d = to_dict(new_campaign("campaign", "officer", 1))
    d["mode"] = "endless"
    with pytest.raises(ValueError):
        from_dict(d)


@pytest.mark.parametrize("path,value", [
    (("teams", "SG-1", "status"), "napping"),
    (("teams", "SG-1", "specialty"), "cooking"),
    (("teams", "SG-1", "xp"), "x"),
    (("teams", "SG-1", "xp"), -1),
    (("teams", "SG-1", "until"), -1),
    (("teams", "SG-1", "mission"), 999),
    (("worlds", 0, "status"), "sunny"),
    (("worlds", 0, "surveyed"), 1),
    (("worlds", 0, "env"), "lava"),
    (("worlds", 0, "glyphs"), [1, 2]),
    (("worlds", 0, "drone"), "rover"),
    (("worlds", 0, "canon"), 1),
    (("worlds", 0, "telemetry"), "oops"),
    (("worlds", 0, "telemetry"), [1, 2]),
    (("worlds", 0, "id"), ""),
    (("missions", 0, "world"), "XX-000"),
    (("missions", 0, "state"), "missing"),                    # never set by anything; gone
    (("orders", "object"), "ignore"),
    (("stock", "malp"), 1.5),
    (("stock", "malp"), -1),
    (("meters", "security"), 150),
    (("meters", "security"), -5),
    (("events",), [{"due": "x", "seq": 0, "kind": "k", "data": {}}]),
    (("minutes",), float("inf")),
    (("minutes",), -5),
    (("minutes",), 10 ** 400),
    (("pace",), "frantic"),
    (("rng_state",), [3, [1, 2], None]),
    (("record",), {}),
    (("version",), 2.0),
    (("inventory",), ["tech.zat", 7]),
    (("inventory",), "ally.nox"),
    (("used",), ["tech.zat", 7]),
    (("used",), "ally.nox"),
    (("alarms",), ["x"]),
    (("alarms",), [{"type": 5, "title": "T", "text": "X", "deadline": None}]),
    (("alarms",), [{"type": "missed_checkin", "title": 5, "text": "X", "deadline": None}]),
    (("alarms",), [{"type": "missed_checkin", "title": "T", "text": 5, "deadline": None}]),
    (("alarms",), [{"type": "missed_checkin", "title": "T", "text": "X", "deadline": "x"}]),
    (("alarms",), [{"type": "node", "title": "T", "text": "X", "deadline": None}]),
    (("alarms",), [{"type": "node", "title": "T", "text": "X", "deadline": None, "bind": "nope", "scenario": "s"}]),
    (("alarms",), [{"type": "node", "title": "T", "text": "X", "deadline": None, "bind": {}, "scenario": 5}]),
    (("alarms",), [{"type": "node", "title": "T", "text": "X", "deadline": None, "bind": {}, "scenario": "s"}]),
    (("alarms",), [{"type": "node", "title": "T", "text": "X", "deadline": None, "bind": {}, "scenario": "s",
                    "node": 5}]),
    (("alarms",), [{"type": "missed_checkin", "title": "T", "text": "X", "deadline": None}]),
    (("alarms",), [{"type": "missed_checkin", "title": "T", "text": "X", "deadline": None, "mission": "1"}]),
    (("alarms",), [{"type": "missed_checkin", "title": "T", "text": "X", "deadline": None, "mission": True}]),
])
def test_from_dict_rejects_bad_field_values(path, value):
    d = to_dict(busy_campaign()[0])
    target = d
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        from_dict(d)


def test_from_dict_rejects_duplicate_mission_ids():
    c, _ = busy_campaign()
    w = next(iter(c.worlds))
    c.missions.append(Mission(1, "SG-1", w, "survey", 900, 1000))
    with pytest.raises(ValueError):
        from_dict(to_dict(c))


@pytest.mark.parametrize("kind,data", [
    ("checkin", {"mission": 999}),
    ("team_return", {"mission": 999}),
    ("malp_return", {"mission": 999}),
])
def test_from_dict_rejects_events_pointing_at_unknown_missions(kind, data):
    d = to_dict(busy_campaign()[0])
    d["events"].append({"due": 2000, "seq": 999, "kind": kind, "data": data})
    with pytest.raises(ValueError):
        from_dict(d)


def test_from_dict_rejects_a_team_return_naming_an_unknown_team():
    d = to_dict(busy_campaign()[0])
    d["events"].append({"due": 2000, "seq": 999, "kind": "team_return", "data": {"team": "SG-9"}})
    with pytest.raises(ValueError):
        from_dict(d)


def test_the_roster_starts_with_four_teams_and_can_grow_to_sg12():
    assert CORE_TEAMS == ("SG-1", "SG-2", "SG-3", "SG-4") and ALL_TEAMS[-1] == "SG-12" and len(ALL_TEAMS) == 12
    c = new_campaign("campaign", "officer", 1)
    c.teams["SG-10"] = Team("medical")
    c.teams["SG-5"] = Team("recon", status="forming", until=c.now + 60)
    assert team_names(c) == ["SG-1", "SG-2", "SG-3", "SG-4", "SG-5", "SG-10"]
    assert available_teams(c) == ["SG-1", "SG-2", "SG-3", "SG-4", "SG-10"]
    assert from_dict(json.loads(json.dumps(to_dict(c)))) == c
    del c.teams["SG-2"]
    with pytest.raises(ValueError):
        from_dict(to_dict(c))                    # the core teams never leave the roster


W0 = "@world"                   # stands for a real world id of the busy campaign


@pytest.mark.parametrize("kind,data", [
    ("dial_out", {"world": W0}),                                   # no op
    ("dial_out", {"op": "teleport", "world": W0}),
    ("dial_out", {"op": "malp", "world": "XX-000"}),
    ("dial_out", {"op": "uav"}),
    ("dial_out", {"op": "recall", "world": "XX-000"}),
    ("dial_out", {"op": "depart"}),
    ("dial_out", {"op": "depart", "mission": 999}),
    ("dial_out", {"op": "depart", "mission": "1"}),
    ("dial_out", {"op": "search", "mission": 1}),
    ("dial_out", {"op": "search", "mission": 1, "by": "SG-9"}),
    ("dial_out", {"op": "search", "by": "malp"}),
    ("malp_return", {"world": "XX-000", "drone": "malp"}),
    ("malp_return", {"world": W0, "drone": "rover"}),
    ("malp_return", {"world": W0}),
    ("malp_return", {"drone": "uav"}),
    ("malp_return", {"world": ["x"], "drone": "uav"}),
    ("checkin", {}),
    ("checkin", {"mission": "1"}),
    ("checkin", {"mission": 1, "since": "noon"}),
    ("team_return", {}),
    ("team_return", {"team": "SG-9"}),
    ("team_return", {"mission": 999}),
    ("search_report", {"mission": 1}),                             # no by
    ("search_report", {"mission": 1, "by": "rover"}),
    ("search_report", {"by": "malp"}),
    ("search_report", {"mission": 999, "by": "malp"}),
    ("overdue", {}),
    ("overdue", {"mission": 999}),
])
def test_from_dict_rejects_bad_event_payloads(kind, data):
    d = to_dict(busy_campaign()[0])
    wid = d["worlds"][0]["id"]
    data = {k: wid if v == W0 else v for k, v in data.items()}
    d["events"].append({"due": 2000, "seq": 999, "kind": kind, "data": data})
    with pytest.raises(ValueError):
        from_dict(d)


@pytest.mark.parametrize("kind,data", [
    ("dial_out", {"op": "malp", "world": W0}),
    ("dial_out", {"op": "uav", "world": W0}),
    ("dial_out", {"op": "recall", "world": W0}),
    ("dial_out", {"op": "depart", "mission": 1}),
    ("dial_out", {"op": "search", "mission": 1, "by": "malp"}),
    ("malp_return", {"world": W0, "drone": "uav"}),
    ("checkin", {"mission": 1, "since": 1400}),
    ("team_return", {"mission": 1}),
    ("search_report", {"mission": 1, "by": "malp"}),
    ("overdue", {"mission": 1}),
    ("incoming", {}),
    ("recovery_tick", {}),
])
def test_from_dict_accepts_every_event_the_engine_makes(kind, data):
    c = busy_campaign()[0]
    if data.get("op") == "depart":
        c.teams["SG-3"].status = "staging"              # its departure still waits for the gate
    wid = next(iter(c.worlds))
    c.events.push(2000, kind, {k: wid if v == W0 else v for k, v in data.items()})
    assert from_dict(json.loads(json.dumps(to_dict(c)))) == c


def test_a_search_team_is_offworld_without_a_mission():
    c = busy_campaign()[0]
    c.teams["SG-1"].status, c.teams["SG-1"].where = "offworld", c.mission(1).world
    with pytest.raises(ValueError):
        from_dict(to_dict(c))                                  # offworld, on nothing, going nowhere
    c.events.push(2000, "dial_out", {"op": "search", "mission": 1, "by": "SG-1"})
    assert from_dict(to_dict(c)) == c                          # queued to search
    c.events.cancel(lambda e: e.kind == "dial_out")
    c.events.push(2000, "search_report", {"mission": 1, "by": "SG-1"})
    assert from_dict(to_dict(c)) == c                          # searching


def test_an_offworld_team_needs_a_mission_or_a_way_home():
    c = new_campaign("campaign", "officer", 3)
    c.teams["SG-2"].status, c.teams["SG-2"].where = "offworld", next(iter(c.worlds))
    with pytest.raises(ValueError):
        from_dict(to_dict(c))
    c.events.push(c.now + 360, "team_return", {"team": "SG-2"})       # reinforcing: home in 6 hours
    assert from_dict(to_dict(c)) == c


def test_saves_no_longer_carry_check_in_counts_but_older_saves_still_load():
    c = busy_campaign()[0]
    d = to_dict(c)
    assert "checkins" not in d["missions"][0] and "missed" not in d["missions"][0]
    d["missions"][0] |= {"checkins": 2, "missed": 1}                   # as a save from before
    assert from_dict(d) == c


def test_a_new_campaign_has_funding_factions_and_arcs():
    c = new_campaign("campaign", "officer", 42)
    assert c.funding == 500 and c.naquadah == 0 and c.upgrades == set() and c.reserve == {"malp": 2, "uav": 0}
    assert set(c.factions) == set(world.FACTION_IDS) and not any(f.known for f in c.factions.values())
    assert c.factions["apophis"].kind == "goauld" and c.factions["tokra"].kind == "ally"
    assert all(f.attention == 0 and f.trust == 0 for f in c.factions.values())
    assert c.arcs == {a: ArcState() for a in ARC_IDS} and c.deals == [] and c.captured_drones == []
    assert [w.hidden_names for w in c.unlisted.values()] == [{"allies": "Vorash"}]
    assert not set(c.unlisted) & set(c.worlds)
    assert c.ledger == dict.fromkeys(("intel", "tech", "allies", "missions", "arcs", "lost", "captured",
                                      "breaches", "incidents"), 0)
    assert c.won is None and c.ending is None and c.hints == set() and c.reviews == []
    s = new_campaign("sandbox", "officer", 42)
    assert s.arcs == {} and s.unlisted == {} and set(s.factions) == set(world.FACTION_IDS)


def test_secondary_specialties_count():
    t = Team("combat", secondary="medical")
    assert has_specialty(t, "medical") and has_specialty(t, "combat") and not has_specialty(t, "science")


def stage2_campaign():
    c, rng = busy_campaign()
    w = list(c.worlds)[3]
    c.funding, c.naquadah, c.upgrades = 345, 7, {"uav_program", "iris_reinforcement"}
    c.reserve = {"malp": 3, "uav": 1}
    c.factions["apophis"].attention, c.factions["apophis"].known = 55, True
    c.factions["apophis"].source, c.factions["apophis"].quiet_since = "the Jaffa", 900
    c.factions["tokra"].trust = 30
    c.arcs["apophis"] = ArcState("active", 2, 700, None, 5000)
    c.deals.append(Deal(1, w, "naquadah", 2, 1600, 5))
    c.captured_drones.append(CapturedDrone("uav", w, 800, located=True))
    c.ledger["intel"] = 4
    c.reviews.append((480, 300, "BASE 300"))
    c.teams["SG-6"] = Team("diplomatic", status="training", until=1400, secondary="medical")
    c.missions[0].target = None
    c.hints.add("explored")
    c.won = 990
    c.events.push(1600, "trade_delivery", {"deal": 1})
    c.events.push(1700, "faction_action", {"faction": "apophis"})
    c.events.push(5000, "arc_step", {"arc": "apophis", "stage": 2})
    return c, rng


def test_round_trip_keeps_every_stage_2_field():
    c, _ = stage2_campaign()
    back = from_dict(json.loads(json.dumps(to_dict(c))))
    assert back == c and back.deal(1).amount == 2 and back.unlisted == c.unlisted


@pytest.mark.parametrize("path,value", [
    (("funding",), -1), (("funding",), "lots"), (("naquadah",), -3),
    (("upgrades",), ["warp_drive"]), (("reserve", "malp"), 9), (("reserve",), {"malp": 1}),
    (("factions", "apophis", "attention"), 101), (("factions", "apophis", "kind"), "ally"),
    (("factions", "tokra", "trust"), -1), (("factions",), {}),
    (("arcs", "apophis", "state"), "sleeping"), (("arcs", "apophis", "stage"), -1), (("arcs",), {"ghost": {}}),
    (("deals", 0, "world"), "XX-000"), (("deals", 0, "goods"), "spice"), (("deals", 0, "state"), "paused"),
    (("captured_drones", 0, "drone"), "rover"), (("captured_drones", 0, "world"), "XX-000"),
    (("ledger",), {"intel": 1}), (("won",), "yes"), (("ending",), "bored"), (("hints",), [3]),
    (("teams", "SG-6", "secondary"), "cooking"), (("missions", 0, "target"), 5),
])
def test_from_dict_rejects_bad_stage_2_values(path, value):
    d = to_dict(stage2_campaign()[0])
    target = d
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        from_dict(d)


@pytest.mark.parametrize("kind,data", [
    ("faction_action", {}), ("faction_action", {"faction": "tokra"}), ("faction_action", {"faction": "zeus"}),
    ("trade_delivery", {}), ("trade_delivery", {"deal": 99}),
    ("arc_step", {"arc": "apophis"}), ("arc_step", {"arc": "ghost", "stage": 1}),
    ("arc_step", {"arc": "apophis", "stage": 0}),
])
def test_from_dict_rejects_bad_stage_2_events(kind, data):
    d = to_dict(stage2_campaign()[0])
    d["events"].append({"due": 2000, "seq": 999, "kind": kind, "data": data})
    with pytest.raises(ValueError):
        from_dict(d)


def test_a_stage_1_save_is_upgraded_with_nothing_in_flight_lost():
    with open("tests/data/stage1_save.json") as f:
        old = json.load(f)
    c = from_dict(upgrade_v2(old))
    assert c.minutes == old["minutes"] and len(c.missions) == len(old["missions"])
    assert [(e.due, e.kind) for e in c.events if e.kind != "funding_review"] == \
        [(e["due"], e["kind"]) for e in sorted(old["events"], key=lambda e: (e["due"], e["seq"]))]
    review = next(e for e in c.events if e.kind == "funding_review")
    assert review.due > c.minutes and (review.due - clock.START) % (7 * clock.DAY) == 0
    assert c.funding == 500 and c.upgrades == {"uav_program"} and c.stock == old["stock"]
    assert c.arcs == {a: ArcState() for a in ARC_IDS} and set(c.unlisted) == {world.place_id("Vorash")}
    assert all(t.secondary is None for t in c.teams.values()) and all(m.target is None for m in c.missions)
    assert old["version"] == 2                                  # the input is left alone
    with pytest.raises(ValueError):
        upgrade_v2({"version": 3})


def test_the_upgrade_never_drops_vorash():
    with open("tests/data/stage1_save.json") as f:
        old = json.load(f)
    used = {m["world"] for m in old["missions"]} | {e["data"].get("world") for e in old["events"]} \
        | {t["where"] for t in old["teams"].values()}
    spare = next(w for w in old["worlds"] if w["id"] not in used)
    spare["id"] = world.place_id("Vorash")          # a forced collision: no Stage 1 world can have this id
    up = upgrade_v2(old)
    assert [w["id"] for w in up["unlisted"]] == [world.place_id("Vorash")]
    with pytest.raises(ValueError):
        from_dict(up)                               # a damaged save, set aside rather than losing Vorash


@pytest.mark.parametrize("path", [("teams", "SG-6", "secondary"), ("missions", 0, "target")])
def test_version_3_saves_carry_every_team_and_mission_key(path):
    d = to_dict(stage2_campaign()[0])
    del d[path[0]][path[1]][path[2]]
    with pytest.raises(ValueError):
        from_dict(d)


def test_a_wreck_round_trips_and_an_old_save_has_none():
    c = busy_campaign()[0]
    w = list(c.worlds.values())[3]
    w.wreck = "shot_down"
    assert from_dict(json.loads(json.dumps(to_dict(c)))).worlds[w.id].wreck == "shot_down"
    d = to_dict(c)
    del d["worlds"][3]["wreck"]                            # a save from before wrecks
    assert from_dict(d).worlds[w.id].wreck is None
    d["worlds"][3]["wreck"] = "melted"
    with pytest.raises(ValueError):
        from_dict(d)
