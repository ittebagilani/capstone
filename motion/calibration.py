"""Build a `coordinates.json` table from four measured board corners.

Calibration procedure (Alex, on the real gantry):
  1. Home the machine.
  2. Jog the magnet over the centre of a1, read the position in mm. Repeat for h1, a8, h8.
  3. `python scripts/make_coordinates.py --a1 X Y --h1 X Y --a8 X Y --h8 X Y`

The 64 centres are bilinearly interpolated between the four corners. That absorbs the board
being rotated, offset, or slightly skewed relative to the gantry, and the X and Y axes having
slightly different real steps/mm. It does not absorb a bent board or a non-linear axis. If a
middle square measures more than ~1 mm off the generated value, that's the problem, and the
fix is per-square measurement rather than a better formula.

Graveyard slots are extrapolated off the same grid, in columns beyond the board file farthest
from home (CLAUDE.md §8.5: graveyard opposite home, so captures never drag across the board).

This module computes values only. It never reads the gantry or writes files. `motion.coordinates`
stays the only reader, and `scripts/make_coordinates.py` is the only writer.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from motion.coordinates import FILES, RANKS, CoordinateTable

Point = tuple[float, float]

#: Worst case: 15 per side, kings are never captured (INTERFACES.md §5).
SLOTS_NEEDED = 30


def _grid_point(corners: dict[str, Point], f: float, r: float) -> Point:
    """Square centre at file index f (a=0..h=7) and rank index r (1=0..8=7). Values outside
    0..7 extrapolate, which is how the graveyard columns are placed."""
    u, v = f / 7.0, r / 7.0
    a1, h1, a8, h8 = corners["a1"], corners["h1"], corners["a8"], corners["h8"]
    x = (1 - u) * (1 - v) * a1[0] + u * (1 - v) * h1[0] + (1 - u) * v * a8[0] + u * v * h8[0]
    y = (1 - u) * (1 - v) * a1[1] + u * (1 - v) * h1[1] + (1 - u) * v * a8[1] + u * v * h8[1]
    return round(x, 3), round(y, 3)


def corners_from_h8(h8: Point, pitch_mm: float) -> dict[str, Point]:
    """Alex's single-square calibration (`MARK H8` in ChessGantryBoard.ino): H8 is the square
    nearest home, files run toward +X from h to a, ranks toward +Y from 8 to 1:
        x = h8_x + ('h' - file) * pitch,   y = h8_y + ('8' - rank) * pitch
    This assumes the board sits perfectly square to the gantry. Measure all four corners once
    the machine is stable and regenerate."""
    x, y = h8
    far = 7 * pitch_mm
    return {"h8": (x, y), "a8": (x + far, y), "h1": (x, y + far), "a1": (x + far, y + far)}


def measured_pitch(corners: dict[str, Point]) -> float:
    """Mean square size implied by the four edges (each edge spans 7 squares)."""
    edges = [("a1", "h1"), ("a8", "h8"), ("a1", "a8"), ("h1", "h8")]
    return sum(math.dist(corners[p], corners[q]) for p, q in edges) / (4 * 7)


def far_file(corners: dict[str, Point]) -> str:
    """'a' or 'h', whichever edge file is farther from gantry home (0,0)."""
    a_mid = _grid_point(corners, 0, 3.5)
    h_mid = _grid_point(corners, 7, 3.5)
    return "a" if math.hypot(*a_mid) > math.hypot(*h_mid) else "h"


def build_table(
    corners: dict[str, Point],
    graveyard_columns: int = 4,
    graveyard_gap_squares: float = 0.5,
    graveyard_side: str | None = None,
    method: str = "bilinear from four measured corner centres",
    note: str | None = None,
) -> dict:
    """Return a dict in the `coordinates.json` shape, validated by `CoordinateTable`.

    Graveyard layout: `graveyard_columns` columns, one square apart, starting
    `1 + graveyard_gap_squares` squares beyond the edge file's centres. Each column has 8
    slots, aligned with ranks 1..8.

    One side ('a' or 'h'): the first half of the columns (nearest the board) is white, the
    rest black. `graveyard_side='both'`: `graveyard_columns` columns on *each* side, white
    beyond the file farther from home, black beyond the home-side file (the printed board's
    split graveyard). Slots number nearest-the-board column first, rank 1 to 8.
    """
    missing = {"a1", "h1", "a8", "h8"} - set(corners)
    if missing:
        raise ValueError(f"missing corner(s): {', '.join(sorted(missing))}")
    if graveyard_columns < 2 or graveyard_columns % 2:
        raise ValueError("graveyard_columns must be an even number >= 2 (half white, half black)")
    side = graveyard_side or far_file(corners)
    if side not in ("a", "h", "both"):
        raise ValueError("graveyard_side must be 'a', 'h' or 'both'")

    coordinates = {
        f"{FILES[f]}{RANKS[r]}": list(_grid_point(corners, f, r)) for r in range(8) for f in range(8)
    }

    def file_index(edge: str, col: int) -> float:
        offset = 1 + graveyard_gap_squares + col
        return 7 + offset if edge == "h" else -offset

    graveyard: dict[str, list[float]] = {}
    if side == "both":
        far = far_file(corners)
        near = "h" if far == "a" else "a"
        for colour, edge in (("white", far), ("black", near)):
            for col in range(graveyard_columns):
                for r in range(8):
                    graveyard[f"{colour}_{col * 8 + r + 1}"] = list(_grid_point(corners, file_index(edge, col), r))
    else:
        per_colour = graveyard_columns // 2
        for col in range(graveyard_columns):
            colour = "white" if col < per_colour else "black"
            base = (col % per_colour) * 8
            for r in range(8):
                graveyard[f"{colour}_{base + r + 1}"] = list(_grid_point(corners, file_index(side, col), r))

    data = {
        "square_size_mm": round(measured_pitch(corners), 3),
        "coordinates": coordinates,
        "graveyard": graveyard,
        "calibration": {
            "method": method,
            **({"note": note} if note else {}),
            "corners_mm": {k: list(corners[k]) for k in ("a1", "h1", "a8", "h8")},
            "graveyard_side": side,
            "graveyard_gap_squares": graveyard_gap_squares,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    }
    CoordinateTable.from_dict(data)  # refuse to emit anything the loader would reject
    return data


def warnings_for(data: dict) -> list[str]:
    """Sanity checks worth a human look before the machine moves."""
    out = []
    points = list(data["coordinates"].values()) + list(data["graveyard"].values())
    negative = [p for p in points if p[0] < 0 or p[1] < 0]
    if negative:
        out.append(f"{len(negative)} position(s) have negative mm, i.e. behind home. Wrong corner order or wrong graveyard side?")
    if len(data["graveyard"]) < SLOTS_NEEDED:
        out.append(f"only {len(data['graveyard'])} graveyard slots; worst case needs {SLOTS_NEEDED}")
    c = data["coordinates"]
    if math.dist(c["a1"], c["h8"]) < math.dist(c["a1"], c["h1"]):
        out.append("a1→h8 diagonal is shorter than an edge; corners are probably mislabelled")
    return out


def clamp_graveyard(data: dict, x_range: tuple[float, float], y_range: tuple[float, float], margin: float = 1.0) -> list[str]:
    """Pull graveyard slots inside the gantry's soft-limit range (minus `margin`), in place.

    Only the graveyard: a dropped piece just has to land somewhere in its slot, and the robot
    never picks it back up. Board squares are never clamped. If one is out of reach the
    calibration itself is wrong, so this raises instead. Returns the slots that moved.
    """
    lo_x, hi_x = x_range[0] + margin, x_range[1] - margin
    lo_y, hi_y = y_range[0] + margin, y_range[1] - margin
    bad = [sq for sq, (x, y) in data["coordinates"].items() if not (lo_x <= x <= hi_x and lo_y <= y <= hi_y)]
    if bad:
        raise ValueError(f"board square(s) outside gantry travel: {', '.join(bad)}")
    moved = []
    for slot, (x, y) in data["graveyard"].items():
        cx, cy = min(max(x, lo_x), hi_x), min(max(y, lo_y), hi_y)
        if (cx, cy) != (x, y):
            data["graveyard"][slot] = [round(cx, 3), round(cy, 3)]
            moved.append(f"{slot} {x:.1f},{y:.1f} -> {cx:.1f},{cy:.1f}")
    if moved:
        data["calibration"]["clamped_slots"] = moved
    return moved


def apply_graveyard_marks(data: dict, slot_marks: dict[str, Point]) -> dict[str, Point]:
    """Shift each colour's graveyard strip so it matches the slots marked on the machine, in place.

    The strips are printed on the board's grid, so spacing is taken from the board and only
    the offset is measured: per colour, the mean of (marked - predicted) over its marked
    slots. One mark per side fixes an offset, more average out jog error. Run before
    `clamp_graveyard`, since the prediction must be the unclamped position.
    Returns the applied offset per colour.
    """
    unknown = sorted(set(slot_marks) - set(data["graveyard"]))
    if unknown:
        raise ValueError(f"unknown graveyard slot(s): {', '.join(unknown)}")
    offsets: dict[str, Point] = {}
    for colour in ("white", "black"):
        mine = [s for s in slot_marks if s.startswith(colour + "_")]
        if not mine:
            continue
        dx = sum(slot_marks[s][0] - data["graveyard"][s][0] for s in mine) / len(mine)
        dy = sum(slot_marks[s][1] - data["graveyard"][s][1] for s in mine) / len(mine)
        for slot, (x, y) in data["graveyard"].items():
            if slot.startswith(colour + "_"):
                data["graveyard"][slot] = [round(x + dx, 3), round(y + dy, 3)]
        offsets[colour] = (round(dx, 3), round(dy, 3))
    if offsets:
        data["calibration"]["graveyard_marks_mm"] = {s: list(v) for s, v in slot_marks.items()}
        data["calibration"]["graveyard_offsets_mm"] = {c: list(v) for c, v in offsets.items()}
    return offsets
