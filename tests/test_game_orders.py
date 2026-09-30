import pytest

from sgc.game import orders
from sgc.game.orders import SITUATIONS


def test_every_situation_from_the_spec_is_there_with_a_cautious_default():
    assert orders.defaults() == {
        "unknown_idc": "closed", "hostiles_following": "open_briefly", "bad_idc": "closed_alert",
        "object": "seal", "missed_checkin": "malp", "under_fire": "recall", "contact_offer": "defer"}
    assert SITUATIONS["missed_checkin"].keys == ("malp", "team", "wait")
    assert orders.label("object", "push_back") == "Dial out, push it back through"


def test_the_missed_check_in_malp_order_says_what_it_does_and_old_saves_keep_it():
    # the search resolves on its own; nobody is asked to decide afterwards. Saves store the key, not the label.
    assert orders.label("missed_checkin", "malp") == "Send a MALP to search"
    assert orders.validate({**orders.defaults(), "missed_checkin": "malp"})["missed_checkin"] == "malp"


@pytest.mark.parametrize("sid", sorted(SITUATIONS))
def test_timeout_runs_the_default_order(sid):
    s = SITUATIONS[sid]
    opts = [(k, True) for k in s.keys]
    assert orders.on_timeout(sid, orders.defaults(), opts, s.default) == s.default


@pytest.mark.parametrize("sid", sorted(SITUATIONS))
def test_timeout_runs_each_non_default_order(sid):
    s = SITUATIONS[sid]
    opts = [(k, True) for k in s.keys]
    for key in s.keys[1:]:
        o = orders.defaults()
        o[sid] = key
        assert orders.on_timeout(sid, o, opts, s.default) == key


def test_an_order_that_cant_run_falls_back_to_the_default():
    o = orders.defaults()
    o["under_fire"] = "reinforce"
    opts = [("recall", True), ("hold", True), ("reinforce", False)]
    assert orders.on_timeout("under_fire", o, opts, "recall") == "recall"
    assert orders.on_timeout("under_fire", o, [("recall", True), ("hold", True)], "recall") == "recall"
    assert orders.on_timeout(None, o, opts, "hold") == "hold"


def test_cycle_and_validate():
    o = orders.defaults()
    assert orders.cycle(o, "missed_checkin") == "team" and orders.cycle(o, "missed_checkin") == "wait"
    assert orders.cycle(o, "missed_checkin") == "malp"
    assert orders.validate(o) == o and orders.validate(o) is not o
    for bad in ({**o, "object": "ignore"}, {k: v for k, v in o.items() if k != "object"}, {**o, "x": "y"}, []):
        with pytest.raises(ValueError):
            orders.validate(bad)


def test_a_disabled_order_and_a_disabled_default_fall_back_to_the_first_enabled_choice():
    o = orders.defaults()
    o["missed_checkin"] = "team"
    opts = [("malp", False), ("team", False), ("wait", True)]
    assert orders.on_timeout("missed_checkin", o, opts, "malp") == "wait"
    assert orders.on_timeout("missed_checkin", o, [("malp", False), ("team", True), ("wait", True)], "malp") == "team"
    assert orders.on_timeout("under_fire", {**o, "under_fire": "reinforce"},
                             [("recall", False), ("hold", True), ("reinforce", False)], "recall") == "hold"
