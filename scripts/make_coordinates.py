#!/usr/bin/env python3
"""Generate firmware/coordinates.json from four measured corner-square centres.

    python scripts/make_coordinates.py --a1 251.5 236.0 --h1 24.0 236.5 --a8 251.0 9.0 --h8 23.5 9.5
    python scripts/make_coordinates.py --h8 35 35 --pitch 35      # Alex's one-square mode

Measure on the real gantry: home it, jog the magnet over the centre of each corner square,
read the position in mm (Alex's firmware: `P`). All values are mm relative to gantry home.
(The numbers in the example above are made up. Never run it with them.)

Prints a summary and any warnings. Use --dry-run to inspect without writing.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motion.calibration import build_table, corners_from_h8, warnings_for
from motion.coordinates import DEFAULT_TABLE_PATH


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for sq in ("a1", "h1", "a8", "h8"):
        p.add_argument(f"--{sq}", nargs=2, type=float, metavar=("X", "Y"), help=f"measured centre of {sq}, mm")
    p.add_argument("--pitch", type=float, help="with --h8 only: Alex's single-square mode, square size in mm")
    p.add_argument("--note", help="free text stored in the file's calibration block")
    p.add_argument("--graveyard-columns", type=int, default=4, help="even; 8 slots each (default 4 = 32 slots)")
    p.add_argument("--graveyard-gap", type=float, default=0.5, help="extra clearance between board edge and first graveyard column, in squares")
    p.add_argument("--graveyard-side", choices=("a", "h"), help="which file the graveyard sits beyond (default: whichever is farther from home)")
    p.add_argument("--out", type=Path, default=DEFAULT_TABLE_PATH)
    p.add_argument("--dry-run", action="store_true", help="print the table instead of writing it")
    args = p.parse_args()

    given = [sq for sq in ("a1", "h1", "a8", "h8") if getattr(args, sq)]
    if args.pitch and given == ["h8"]:
        corners = corners_from_h8(tuple(args.h8), args.pitch)
        method = f"single square: H8 + {args.pitch} mm pitch, board assumed square to gantry"
    elif len(given) == 4 and not args.pitch:
        corners = {sq: tuple(getattr(args, sq)) for sq in given}
        method = "bilinear from four measured corner centres"
    else:
        p.error("give either all four of --a1 --h1 --a8 --h8, or --h8 with --pitch")
    try:
        data = build_table(corners, args.graveyard_columns, args.graveyard_gap, args.graveyard_side, method, args.note)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(data, indent=2) + "\n"
    c, g = data["coordinates"], data["graveyard"]
    xs = [pt[0] for pt in list(c.values()) + list(g.values())]
    ys = [pt[1] for pt in list(c.values()) + list(g.values())]
    print(f"square size (measured): {data['square_size_mm']} mm")
    print(f"graveyard: {len(g)} slots beyond the {data['calibration']['graveyard_side']}-file")
    print(f"extent: X {min(xs):.1f}..{max(xs):.1f} mm, Y {min(ys):.1f}..{max(ys):.1f} mm. Must fit inside gantry travel.")
    print(f"spot-check e4 = {c['e4']}, d5 = {c['d5']}: jog there and confirm the magnet is centred")
    for w in warnings_for(data):
        print(f"WARNING: {w}", file=sys.stderr)

    if args.dry_run:
        print(text)
    else:
        args.out.write_text(text)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
