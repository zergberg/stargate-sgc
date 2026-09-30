import json
import random

import pytest

from sgc.game.state import (POOL, ROSTER, TEAMS, Campaign, Lord, from_dict, new_campaign, replace_lord,
                            to_dict)


def test_new_campaign_draws_five_lords_deterministically():
    a, b = new_campaign("campaign", "officer", 42), new_campaign("campaign", "officer", 42)
    assert [l.name for l in a.lords] == [l.name for l in b.lords]
    assert len(a.lords) == ROSTER and len({l.name for l in a.lords}) == ROSTER
    assert all(3 <= l.strength <= 5 and 10 <= l.aggression <= 30 for l in a.lords)
    assert set(a.teams) == set(TEAMS) and all(t.status == "base" and t.idc == "valid" for t in a.teams.values())
    assert a.meters == {"security": 70, "personnel": 80, "intel": 10}


def test_lord_lookup_and_active():
    c = new_campaign("campaign", "recruit", 1)
    first = c.lords[0]
    assert c.lord(first.name) is first and c.lord("Nobody") is None
    first.defeated = True
    assert first not in c.active_lords()


def test_replace_lord_takes_an_unused_name_then_revives():
    c = new_campaign("endless", "officer", 3)
    fallen = c.lords[0]
    fallen.defeated = True
    new = replace_lord(c, fallen.name)
    assert new is not None and new.name not in {l.name for l in c.lords[:ROSTER]} and not new.defeated
    for name in POOL:                      # exhaust the pool
        if c.lord(name) is None:
            c.lords.append(Lord(name, 1, 0, defeated=True))
    revived = replace_lord(c, fallen.name)
    assert revived is not None and revived.name != fallen.name and not revived.defeated


def test_round_trip_through_json_keeps_everything():
    c = new_campaign("endless", "commander", 9)
    c.inventory |= {"ally.tokra", "tech.zat"}
    c.used.add("ally.nox")
    c.teams["SG-3"].status, c.teams["SG-3"].idc, c.teams["SG-3"].captured_at = "captured", "compromised", "Chulak"
    c.cycles, c.since_briefing, c.over = 12, 2, None
    rng = random.Random(5)
    rng.random()
    c.rng_state = rng.getstate()
    back = from_dict(json.loads(json.dumps(to_dict(c))))
    assert back == c
    r2 = random.Random()
    r2.setstate(back.rng_state)
    assert r2.random() == rng.random()


def test_from_dict_rejects_other_versions_and_bad_data():
    d = to_dict(new_campaign("campaign", "officer", 1))
    d["version"] = 99
    with pytest.raises(ValueError):
        from_dict(d)
    d = to_dict(new_campaign("campaign", "officer", 1))
    d["mode"] = "arcade"
    with pytest.raises(ValueError):
        from_dict(d)


@pytest.mark.parametrize("path,value", [
    (("teams", "SG-1", "status"), "napping"),
    (("lords", 0, "strength"), "x"),
    (("won",), "false"),
    (("rng_state",), [3, [1, 2], None]),
    (("lords", 0, "name"), "Anubis"),
    (("record",), {}),
])
def test_from_dict_rejects_bad_field_values(path, value):
    d = to_dict(new_campaign("campaign", "officer", 1))
    target = d
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        from_dict(d)
