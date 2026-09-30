import json
from datetime import datetime

import pytest

from sgc.game.save import Saves
from sgc.game.state import new_campaign, to_dict


def test_save_load_delete(tmp_path):
    s = Saves(tmp_path / "data")
    assert not s.exists() and s.load() == (None, "")
    c = new_campaign("campaign", "officer", 4)
    c.cycles = 7
    s.save(c)
    assert s.exists() and not list((tmp_path / "data").glob("*.tmp"))
    loaded, notice = s.load()
    assert loaded == c and notice == ""
    s.delete()
    assert not s.exists()


def test_damaged_save_is_quarantined(tmp_path):
    s = Saves(tmp_path)
    s.path.parent.mkdir(parents=True, exist_ok=True)
    s.path.write_text("{ not json")
    c, notice = s.load()
    assert c is None and "DAMAGED" in notice and not s.path.exists()
    assert len(list(tmp_path.glob("campaign.json.bad-*"))) == 1


def test_records_keep_the_best_five(tmp_path):
    s = Saves(tmp_path)
    for i in range(7):
        c = new_campaign("endless", "officer", i)
        c.record["goauld_defeated"], c.cycles = i, 10 + i
        s.add_record(c, "overrun")
    recs = s.records()
    assert len(recs) == 5 and [r["goauld_defeated"] for r in recs] == [6, 5, 4, 3, 2]
    assert recs[0]["mode"] == "endless" and recs[0]["result"] == "overrun"


@pytest.mark.parametrize("bad_records", [
    [{"goauld_defeated": "x", "cycles": 1}],
    None,
    [{"goauld_defeated": 1, "cycles": [1]}, {"goauld_defeated": 1, "cycles": 2}],
    [{"goauld_defeated": True, "cycles": 1}],
])
def test_records_ignores_malformed_entries_without_raising(tmp_path, bad_records):
    s = Saves(tmp_path)
    s.root.mkdir(parents=True, exist_ok=True)
    s.records_path.write_text(json.dumps(bad_records))
    recs = s.records()
    assert isinstance(recs, list)
    c = new_campaign("endless", "officer", 1)
    s.add_record(c, "overrun")  # must not raise either


def test_load_never_raises_on_out_of_range_numbers(tmp_path):
    s = Saves(tmp_path)
    s.path.parent.mkdir(parents=True, exist_ok=True)
    d = to_dict(new_campaign("campaign", "officer", 1))
    text = json.dumps(d).replace('"cycles": 0', '"cycles": 1e400')
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
