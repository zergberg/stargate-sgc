import json
from datetime import datetime

import pytest

from sgc.game import save as save_mod
from sgc.game.save import Saves
from sgc.game.state import Mission, new_campaign, to_dict


def test_save_load_delete(tmp_path):
    s = Saves(tmp_path / "data")
    assert not s.exists() and s.load() == (None, "")
    c = new_campaign("campaign", "officer", 4)
    c.minutes = 900.0
    s.save(c)
    assert s.exists() and not list((tmp_path / "data").glob("*.tmp"))
    loaded, notice = s.load()
    assert loaded == c and notice == ""
    s.delete()
    assert not s.exists()


def test_a_build_1_save_is_set_aside_with_a_clear_notice(tmp_path):
    s = Saves(tmp_path)
    s.path.parent.mkdir(parents=True, exist_ok=True)
    s.path.write_text(json.dumps({"version": 1, "mode": "endless", "cycles": 12}))
    c, notice = s.load()
    assert c is None and notice == "BUILD 1 SAVE SET ASIDE — START A NEW GAME" and not s.exists()
    assert len(list(tmp_path.glob("campaign.json.v1-*"))) == 1


def test_a_campaign_in_flight_resumes_exactly(tmp_path):
    s = Saves(tmp_path)
    c = new_campaign("sandbox", "commander", 8, "busy")
    c.minutes = 2000.25
    world = next(iter(c.worlds))
    c.missions.append(Mission(id=1, team="SG-2", world=world, type="survey", start=1900, end=2200))
    c.teams["SG-2"].status = "offworld"
    c.teams["SG-2"].where = world
    c.teams["SG-2"].mission = 1
    c.events.push(2100, "checkin", {"mission": 1})
    c.alarms.append({"type": "missed_checkin", "title": "MISSED CHECK-IN", "text": "SG-2 missed a check-in.",
                      "mission": 1, "deadline": 2200})
    s.save(c)
    loaded, _ = s.load()
    assert loaded == c and list(loaded.events) == list(c.events) and loaded.events.seq == c.events.seq


def test_damaged_save_is_quarantined(tmp_path):
    s = Saves(tmp_path)
    s.path.parent.mkdir(parents=True, exist_ok=True)
    s.path.write_text("{ not json")
    c, notice = s.load()
    assert c is None and "DAMAGED" in notice and not s.path.exists()
    assert len(list(tmp_path.glob("campaign.json.bad-*"))) == 1


def entry(surveyed, days, result="overrun"):
    return {"mode": "sandbox", "difficulty": "officer", "result": result, "surveyed": surveyed, "days": days}


def test_records_keep_the_best_five(tmp_path):
    s = Saves(tmp_path)
    for i in range(7):
        s.add_record(entry(i, 10 + i))
    s.add_record(entry(6, 30))
    recs = s.records()
    assert len(recs) == 5 and [(r["surveyed"], r["days"]) for r in recs] == [(6, 30), (6, 16), (5, 15), (4, 14),
                                                                           (3, 13)]
    assert recs[0]["mode"] == "sandbox" and recs[0]["result"] == "overrun" and len(recs[0]["date"]) == 10


@pytest.mark.parametrize("bad_records", [
    [{"surveyed": "x", "days": 1}],
    None,
    [{"surveyed": 1, "days": [1]}, {"surveyed": 1, "days": 2}],
    [{"surveyed": True, "days": 1}],
    [{"goauld_defeated": 3, "cycles": 40}],
])
def test_records_ignores_malformed_entries_without_raising(tmp_path, bad_records):
    s = Saves(tmp_path)
    s.root.mkdir(parents=True, exist_ok=True)
    s.records_path.write_text(json.dumps(bad_records))
    recs = s.records()
    assert isinstance(recs, list) and all(r["surveyed"] == 1 for r in recs)
    s.add_record(entry(1, 1))  # must not raise either


def test_load_never_raises_on_out_of_range_numbers(tmp_path):
    s = Saves(tmp_path)
    s.path.parent.mkdir(parents=True, exist_ok=True)
    d = to_dict(new_campaign("campaign", "officer", 1))
    text = json.dumps(d).replace('"gate_until": 0', '"gate_until": 1e400')
    s.path.write_text(text)
    c, notice = s.load()
    assert c is None and "DAMAGED" in notice and not s.path.exists()


def test_two_damaged_saves_in_the_same_second_dont_collide(tmp_path, monkeypatch):
    import sgc.game.save as save_mod

    class _FixedDateTime:
        @staticmethod
        def now():
            return datetime(2024, 1, 1, 12, 0, 0)

    monkeypatch.setattr(save_mod, "datetime", _FixedDateTime)
    s = Saves(tmp_path)
    s.path.parent.mkdir(parents=True, exist_ok=True)
    s.path.write_text("{ not json (first)")
    s.load()
    s.path.write_text("{ not json (second)")
    s.load()
    bads = sorted(tmp_path.glob("campaign.json.bad-*"))
    assert len(bads) == 2


def test_a_failed_save_raises_and_leaves_no_temporary_file(tmp_path, monkeypatch):
    s = Saves(tmp_path)
    c = new_campaign("campaign", "officer", 4)

    def replace(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(save_mod.os, "replace", replace)
    with pytest.raises(OSError):
        s.save(c)
    assert not list(tmp_path.glob("*.tmp")) and not s.exists()
