import math

import pytest

from motion.calibration import build_table, far_file, measured_pitch, warnings_for
from motion.coordinates import CoordinateTable, GraveyardAllocator

PITCH = 32.5


def square_corners(origin=(30.0, 20.0), pitch=PITCH, angle_deg=0.0):
    """Corners of an ideal board, a1 at origin, optionally rotated."""
    t = math.radians(angle_deg)
    ux, uy = math.cos(t), math.sin(t)       # file direction
    vx, vy = -math.sin(t), math.cos(t)      # rank direction

    def at(f, r):
        return (origin[0] + pitch * (f * ux + r * vx), origin[1] + pitch * (f * uy + r * vy))

    return {"a1": at(0, 0), "h1": at(7, 0), "a8": at(0, 7), "h8": at(7, 7)}


def test_ideal_grid_reproduced_exactly():
    data = build_table(square_corners())
    c = data["coordinates"]
    assert c["a1"] == [30.0, 20.0]
    assert c["e4"] == [30.0 + 4 * PITCH, 20.0 + 3 * PITCH]
    assert data["square_size_mm"] == pytest.approx(PITCH)


def test_rotated_board_stays_rigid():
    data = build_table(square_corners(origin=(60, 40), angle_deg=2.0))
    c = data["coordinates"]
    assert math.dist(c["d4"], c["e4"]) == pytest.approx(PITCH, abs=1e-3)
    assert math.dist(c["d4"], c["d5"]) == pytest.approx(PITCH, abs=1e-3)


def test_loader_accepts_output_and_graveyard_covers_worst_case():
    data = build_table(square_corners())
    table = CoordinateTable.from_dict(data)
    assert len(table.graveyard_slots("white")) == 16
    assert len(table.graveyard_slots("black")) == 16
    alloc = GraveyardAllocator(table)
    for i in range(30):
        alloc.allocate("white" if i % 2 else "black")
    assert warnings_for(data) == []


def test_graveyard_goes_beyond_file_farthest_from_home():
    # a1 near home → h-file is far → graveyard at larger X than h-file
    data = build_table(square_corners(origin=(20, 20)))
    assert far_file(square_corners(origin=(20, 20))) == "h"
    h1_x = data["coordinates"]["h1"][0]
    assert all(x > h1_x for x, _ in data["graveyard"].values())
    # first white column sits 1.5 squares beyond the h-file centres
    assert data["graveyard"]["white_1"][0] == pytest.approx(h1_x + 1.5 * PITCH)


def test_mirrored_board_puts_graveyard_beyond_a_file():
    # Alex's firmware assumes H8 is nearest home, so files run toward larger X from h to a.
    corners = {"h8": (20.0, 20.0), "a8": (20 + 7 * PITCH, 20.0), "h1": (20.0, 20 + 7 * PITCH), "a1": (20 + 7 * PITCH, 20 + 7 * PITCH)}
    data = build_table(corners)
    a1_x = data["coordinates"]["a1"][0]
    assert all(x > a1_x for x, _ in data["graveyard"].values())
    assert warnings_for(data) == []


def test_mislabelled_corners_warn():
    c = square_corners()
    c["h1"], c["h8"] = c["h8"], c["h1"]
    assert any("mislabelled" in w for w in warnings_for(build_table(c)))


def test_missing_corner_rejected():
    c = square_corners()
    del c["a8"]
    with pytest.raises(ValueError, match="a8"):
        build_table(c)


def test_measured_pitch():
    assert measured_pitch(square_corners(pitch=40)) == pytest.approx(40)


def test_single_square_matches_alex_firmware_formula():
    # ChessGantryBoard.ino: x = boardX + ('H'-file)*pitchX, y = boardY + ('8'-rank)*pitchY
    from motion.calibration import corners_from_h8

    h8, p = (35.0, 35.0), 35.0
    c = build_table(corners_from_h8(h8, p))["coordinates"]
    for sq in ("a1", "e4", "h8", "b7"):
        f, r = sq[0].upper(), sq[1]
        assert c[sq] == pytest.approx([h8[0] + (ord("H") - ord(f)) * p, h8[1] + (ord("8") - ord(r)) * p])


def test_split_graveyard_both_sides():
    # printed board: 2x8 slots beyond each side; white far from home, black on the home side
    data = build_table(square_corners(origin=(90.0, 40.0), pitch=35.0), graveyard_columns=2, graveyard_side="both")
    g, c = data["graveyard"], data["coordinates"]
    assert len(g) == 32 and warnings_for(data) == []
    whites = [v for k, v in g.items() if k.startswith("white")]
    blacks = [v for k, v in g.items() if k.startswith("black")]
    assert all(x > c["h1"][0] for x, _ in whites)      # beyond the h-file (far from home here)
    assert all(x < c["a1"][0] for x, _ in blacks)      # beyond the a-file (home side here)
    assert g["black_1"][0] == pytest.approx(c["a1"][0] - 1.5 * 35.0)  # inner home-side column first
    assert g["black_9"][0] == pytest.approx(c["a1"][0] - 2.5 * 35.0)


def test_clamp_graveyard_only_moves_slots():
    from motion.calibration import clamp_graveyard

    data = build_table(square_corners(origin=(90.0, 40.0), pitch=35.0), graveyard_columns=2, graveyard_side="both")
    moved = clamp_graveyard(data, (5, 455), (10, 330))
    assert moved and all(s.startswith("black_") for s in moved)        # outer home-side column only
    assert min(x for x, _ in data["graveyard"].values()) == pytest.approx(6.0)
    with pytest.raises(ValueError, match="board square"):
        clamp_graveyard(data, (100, 455), (10, 330))                   # h-file out of reach -> refuse


def test_graveyard_marks_shift_only_that_side():
    from motion.calibration import apply_graveyard_marks

    data = build_table(square_corners(origin=(90.0, 40.0), pitch=35.0), graveyard_columns=2, graveyard_side="both")
    before = {k: list(v) for k, v in data["graveyard"].items()}
    w8 = before["white_8"]
    offsets = apply_graveyard_marks(data, {"white_8": (w8[0] - 3.0, w8[1])})   # white strip 3 mm short in X
    assert offsets == {"white": (-3.0, 0.0)}
    assert all(data["graveyard"][k][0] == pytest.approx(v[0] - 3.0) for k, v in before.items() if k.startswith("white"))
    assert all(data["graveyard"][k] == v for k, v in before.items() if k.startswith("black"))
    with pytest.raises(ValueError, match="unknown"):
        apply_graveyard_marks(data, {"white_99": (0, 0)})
