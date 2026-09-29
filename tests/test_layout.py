from sgc.layout import compute_layout


def inside(r, c, rw):
    return r.x >= 0 and r.y >= 0 and r.x + r.w <= c and r.y + r.h <= rw


def overlap(a, b):
    return not (a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y)


def test_modes():
    assert compute_layout(30, 10, 9, 18).mode == "tiny"
    assert compute_layout(70, 20, 9, 18).mode == "compact"
    assert compute_layout(100, 27, 9, 18).mode == "full"


def test_rects_valid_across_sizes():
    for cols in range(40, 260, 7):
        for rows in range(12, 80, 5):
            L = compute_layout(cols, rows, 9, 18)
            rects = [r for r in L.rects() if r.w and r.h]
            for r in rects:
                assert inside(r, cols, rows), (cols, rows, L.mode)
            for i, a in enumerate(rects):
                for b in rects[i + 1:]:
                    assert not overlap(a, b), (cols, rows, a, b)


def test_gate_is_roughly_square_in_pixels():
    L = compute_layout(100, 27, 9, 18)
    assert abs(L.gate.w * 9 - L.gate.h * 18) <= 18
    assert L.gate.h >= 14 and L.side.w >= 26
