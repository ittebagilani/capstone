#!/usr/bin/env python3
"""Print the exact machine program for one move. No hardware, no backend running.

    python scripts/show_gcode.py e2e4
    python scripts/show_gcode.py --fen "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1" e1g1
    python scripts/show_gcode.py --fen "8/8/8/3pP3/8/8/8/4K2k w - d6 0 2" e5d6
    python scripts/show_gcode.py --gcode-only e2e4 > move.nc

This is the tool for checking a move before trusting it to the machine, and for pasting a
worked example into the report. Without `--coordinates` it uses the synthetic grid, which is
fine for reading the structure but is **not** the calibrated table — see motion/README.md.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chess

from backend.state import GameLoop
from motion.config import MotionConfig
from motion.coordinates import CoordinateTable
from motion.program import compile_actions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("move", help="the move in UCI, e.g. e2e4 or e7e8q")
    parser.add_argument("--fen", default=chess.STARTING_FEN, help="position the move is played from")
    parser.add_argument("--coordinates", help="path to coordinates.json (default: the synthetic grid)")
    parser.add_argument("--feed", type=float, default=MotionConfig().travel_feed_mm_min, help="travel feed, mm/min")
    parser.add_argument("--gcode-only", action="store_true", help="print just the gantry lines")
    args = parser.parse_args()

    board = chess.Board(args.fen)
    try:
        move = chess.Move.from_uci(args.move)
    except ValueError:
        print(f"{args.move!r} is not valid UCI notation", file=sys.stderr)
        return 2
    if move not in board.legal_moves:
        print(f"{args.move} is not legal in this position", file=sys.stderr)
        return 2
    if move.promotion:
        print(
            f"{args.move} is a promotion — the human places the piece, the gantry does not "
            "(CLAUDE.md §3.7). There is no program for it.",
            file=sys.stderr,
        )
        return 2

    table = CoordinateTable.load(args.coordinates) if args.coordinates else CoordinateTable.synthetic()
    config = MotionConfig(travel_feed_mm_min=args.feed)

    # Same decomposition the live backend uses — this script must not have its own copy.
    actions = GameLoop(board, scan_fn=lambda: None, engine=None)._actions_for(move)
    program = compile_actions(actions, table, config, move_id=f"{args.move}@{board.fen()}")

    if args.gcode_only:
        print("\n".join(program.gcode))
    else:
        print(program.listing())
        print(f"; checksum {program.checksum}")
        print(f"; coordinates {table.source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
