"""Unit tests for the mission-type registry itself (not of engine behavior — that's covered by the
existing suite once Task 13 switches the engine over to reading this registry)."""
from sgc.game.missions import MISSION_TYPES, REGISTRY, get
from sgc.game.missions.base import NO_TARGET
from sgc.game.state import Mission


def test_mission_types_matches_the_registry_and_todays_order():
    assert MISSION_TYPES == ("survey", "contact", "trade", "raid", "study", "rescue", "recover", "mine",
                              "aid")
    assert set(REGISTRY) == set(MISSION_TYPES)


def test_hours_match_todays_mission_hours():
    assert {name: get(name).hours for name in MISSION_TYPES} == {
        "survey": 24, "contact": 36, "trade": 30, "raid": 18, "study": 36, "rescue": 20, "recover": 12,
        "mine": 48, "aid": 30}


def test_needs_match_todays_mission_needs():
    assert {name: get(name).needs for name in MISSION_TYPES if get(name).needs} == {
        "contact": "diplomatic", "trade": "diplomatic", "raid": "combat", "study": "science",
        "aid": "medical"}


def test_ends_as_matches_todays_contact_types():
    contact_ending = {name for name in MISSION_TYPES if get(name).ends_as == "contact"}
    assert contact_ending == {"contact", "trade", "aid"}
    assert all(get(name).ends_as == "surveyed" for name in MISSION_TYPES if name not in contact_ending)


def test_exact_scenarios_matches_todays_exact_types():
    assert {name for name in MISSION_TYPES if get(name).exact_scenarios} == {"rescue", "recover"}


def test_planner_score_matches_todays_type_score():
    assert {name: get(name).planner_score for name in MISSION_TYPES if get(name).planner_score} == {
        "rescue": 50, "recover": 15, "aid": 10, "study": 9, "mine": 8}


def test_only_rescue_and_recover_have_a_real_target():
    assert get("rescue").target is not NO_TARGET
    assert get("recover").target is not NO_TARGET
    assert all(get(name).target is NO_TARGET for name in MISSION_TYPES if name not in ("rescue", "recover"))


def test_rescue_bind_adds_captive_only_when_the_mission_has_a_target():
    m = Mission(1, "SG-1", "w1", "rescue", 0, 100, target="SG-2")
    assert get("rescue").bind(m, "SG-1") == {"captive": "SG-2"}
    m2 = Mission(2, "SG-1", "w1", "rescue", 0, 100, target=None)
    assert get("rescue").bind(m2, "SG-1") == {}


def test_recover_bind_is_the_default_empty_dict():
    m = Mission(1, "SG-1", "w1", "recover", 0, 100, target="malp")
    assert get("recover").bind(m, "SG-1") == {}


def test_assign_labels_match_todays_room_py_text():
    assert get("rescue").assign_label("SG-2") == "RESCUE SG-2"
    assert get("recover").assign_label("malp") == "RECOVER THE MALP"
    assert get("survey").assign_label("anything") is None


def test_registry_values_equal_the_constants_they_replaced():
    """The guard against drift: the registry's values, written out as the old engine and planner constants held them
    on stage2 (MISSION_HOURS, MISSION_NEEDS, CONTACT_TYPES, EXACT_TYPES, TYPE_SCORE) before the refactor removed them."""
    assert {name: get(name).hours for name in MISSION_TYPES} == {
        "survey": 24, "contact": 36, "trade": 30, "raid": 18, "study": 36, "rescue": 20, "recover": 12, "mine": 48,
        "aid": 30}
    assert {name: get(name).needs for name in MISSION_TYPES if get(name).needs} == {
        "contact": "diplomatic", "trade": "diplomatic", "raid": "combat", "study": "science", "aid": "medical"}
    assert {name for name in MISSION_TYPES if get(name).ends_as == "contact"} == {"contact", "trade", "aid"}
    assert {name for name in MISSION_TYPES if get(name).exact_scenarios} == {"rescue", "recover"}
    assert {name: get(name).planner_score for name in MISSION_TYPES if get(name).planner_score} == {
        "rescue": 50, "recover": 15, "aid": 10, "study": 9, "mine": 8}
