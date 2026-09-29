import random

from sgc.addresses import load_canon, random_address, AddressPicker


def test_canon_valid():
    canon = load_canon()
    assert len(canon) >= 25
    for a in canon:
        assert all(2 <= g <= 39 for g in a.glyphs), a.name
        assert len(set(a.glyphs)) == len(a.glyphs), a.name
        assert a.chevrons in (7, 8, 9)
    by = {a.name: a for a in canon}
    assert by["Abydos"].glyphs == (27, 7, 15, 32, 12, 30)
    assert by["Atlantis"].chevrons == 8 and by["Destiny"].chevrons == 9


def test_random_valid():
    rng = random.Random(1)
    for _ in range(200):
        a = random_address(rng)
        assert len(a.glyphs) == 6 and len(set(a.glyphs)) == 6 and 1 not in a.glyphs
        assert not a.canon and a.designation.startswith("P")


def test_picker_no_repeat_no_earth():
    p = AddressPicker(load_canon(), 0.9, random.Random(2))
    prev = None
    for _ in range(300):
        a = p.next()
        assert a.label != prev and a.name != "Earth"
        prev = a.label
