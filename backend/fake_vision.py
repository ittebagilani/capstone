"""Stand-in for vision.scan() — same schema, zero hardware (CLAUDE.md §6.5).

Real vision.scan() must be a one-line swap for this. If wiring it in ever takes more than
that, the vision/backend boundary leaked and that's the bug to fix (CLAUDE.md §3.3).
"""
from datetime import datetime, timezone
from uuid import uuid4

import chess

from backend.inference import board_to_squares


class FakeVision:
    def __init__(self, board: chess.Board | None = None):
        self.board = board if board is not None else chess.Board()

    def set_board(self, board: chess.Board) -> None:
        self.board = board

    def scan(self) -> dict:
        return {
            "type": "board_scan",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "squares": board_to_squares(self.board),
            "confidence": [1.0] * 64,
            "scan_id": str(uuid4()),
        }
