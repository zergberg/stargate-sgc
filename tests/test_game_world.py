import random

import pytest

from sgc.game import world
from sgc.game.world import World


def test_designations_come_from_the_glyphs():
    a = world.designation((27, 7, 15, 32, 12, 30))
    assert a == world.designation((27, 7, 15, 32, 12, 30))
    assert a != world.designation((7, 27, 15, 32, 12, 30))
    assert a[0] == "P" and a[1] in "23456789" and a[3] == "-" and 100 <= int(a[4:]) <= 999


def test_names_are_hidden_until_revealed_and_a_world_can_have_several():
    w = world.canon_world("Langara")
    assert w.name == w.id and w.names == []
    assert world.reveal_name(w, "locals", 600) == "Langara" and w.name == "Langara"
    assert world.reveal_name(w, "locals", 700) is None                      # nothing new
    assert world.reveal_name(w, "comms", 800) == "Kelowna" and w.name == "Kelowna"
    assert [(n, s) for n, s, _ in w.names] == [("Langara", "the locals"), ("Kelowna", "a UAV comms intercept")]
    assert world.reveal_name(w, "ruins", 900) == "Langara" and w.name == "Langara" and len(w.names) == 3


def test_a_source_without_its_own_name_gives_the_locals_name():
    w = World("P3X-100", (2, 3, 4, 5, 6, 7), hidden_names={"locals": "Tel'kar"})
    assert world.reveal_name(w, "jaffa", 10) == "Tel'kar"
    assert w.names == [("Tel'kar", "the Jaffa", 10)]
    assert world.reveal_name(World("P3X-101", (2, 3, 4, 5, 6, 8)), "locals", 1) is None
    assert world.learn_name(w, "Tel'kar", "the Jaffa", 20) is False
    assert world.learn_name(w, "Hold of Sokar", "a Goa'uld database", 20) is True and w.name == "Hold of Sokar"


@pytest.mark.parametrize("start,to,ok", [
    ("unexplored", "probed", True), ("probed", "surveyed", True), ("surveyed", "contact", True),
    ("unexplored", "surveyed", True), ("surveyed", "probed", False), ("contact", "surveyed", False),
    ("probed", "hostile", True), ("contact", "lost", True), ("lost", "probed", True), ("lost", "surveyed", False),
    ("hostile", "contact", True), ("hostile", "surveyed", False), ("probed", "probed", False),
])
def test_status_transitions(start, to, ok):
    w = World("P3X-100", (2, 3, 4, 5, 6, 7), status=start)
    assert world.set_status(w, to) is ok and w.status == (to if ok else start)


def test_unknown_status_raises():
    with pytest.raises(ValueError):
        world.set_status(World("P3X-100", (2, 3, 4, 5, 6, 7)), "sunny")


def test_worlds_start_unsurveyed():
    assert World("P3X-100", (2, 3, 4, 5, 6, 7)).surveyed is False


def test_seeded_generation_is_stable():
    a = world.generate(random.Random(7), set())
    b = world.generate(random.Random(7), set())
    assert a == b and a.env in world.ENVIRONMENTS and a.inhabitants in world.INHABITANTS
    assert len(a.glyphs) == 6 and len(set(a.glyphs)) == 6 and 1 not in a.glyphs
    assert a.hidden_names["locals"] and a.status == "unexplored" and a.options == ["survey"]
    c = world.generate(random.Random(7), {a.id})
    assert c.id != a.id


def test_the_cartouche_has_twenty_unique_addresses_with_abydos_known():
    for mode in ("campaign", "sandbox"):
        ws = world.cartouche(mode, 11, 480)
        assert len(ws) == world.CARTOUCHE_SIZE and len({w.glyph_text for w in ws.values()}) == 20
        first = next(iter(ws.values()))
        assert first.name == "Abydos" and first.status == "contact" and first.names[0][2] == 480
        assert first.options == ["survey", "contact"]
        assert all(w.status == "unexplored" and w.names == [] for w in list(ws.values())[1:])
        assert world.cartouche(mode, 11, 480) == ws
    canon = {w.hidden_names["locals"] for w in world.cartouche("campaign", 3, 480).values() if w.canon}
    assert canon == {"Abydos", *world.CAMPAIGN_CANON}
    assert [w for w in world.cartouche("sandbox", 3, 480).values() if w.canon][0].hidden_names["locals"] == "Abydos"


def test_canon_worlds_use_the_real_glyphs():
    w = world.canon_world("Chulak")
    assert w.glyphs == (9, 2, 23, 15, 37, 20) and w.owner == "Apophis" and w.inhabitants == "jaffa"
    assert w.address().glyphs == w.glyphs and w.address().designation == w.id


def test_danger_follows_the_hidden_traits():
    assert World("a", (), inhabitants="none").danger == 0
    assert World("a", (), inhabitants="jaffa").danger == 2
    assert World("a", (), inhabitants="goauld", env="radiation").danger == 3
    assert World("a", (), inhabitants="human", env="extreme").danger == 1


def test_uav_readings_say_more_than_a_malp():
    w = World("P3X-100", (2, 3, 4, 5, 6, 7), env="toxic", inhabitants="jaffa", features=("ruins", "naquadah"))
    malp = world.readings(w, "malp", "partial", random.Random(1))
    uav = world.readings(w, "uav", "full", random.Random(1))
    assert malp == {"env": "toxic atmosphere", "life": "life signs", "features": "ruins"}
    assert uav["inhabitants"] == "Jaffa garrison" and uav["features"] == "ruins, naquadah traces"
    assert uav["count"].startswith("about ") and uav["life"] == "humanoid life signs"
    assert world.readings(w, "malp", "minimal", random.Random(1)) == {"env": "toxic atmosphere",
                                                                      "life": "inconclusive"}
