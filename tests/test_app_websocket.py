"""Transport smoke test: the websocket round-trips a state_update in the exact shape
INTERFACES.md §6 specifies. Game logic itself is covered by test_state_machine.py — this
only proves app.py wires GameLoop to the socket correctly.

Requires the real Stockfish binary (the app opens one engine at startup for all
connections); skipped if it isn't on PATH.
"""
import shutil

import pytest
from starlette.testclient import TestClient

from backend.app import app

pytestmark = pytest.mark.skipif(shutil.which("stockfish") is None, reason="stockfish not on PATH")


def test_attempt_move_with_no_change_round_trips():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "attempt_move"})
            first = ws.receive_json()
            assert first["type"] == "state_update"
            assert first["game_state"] == "scanning"

            second = ws.receive_json()
            assert second["game_state"] == "waiting_human"
            assert second["message"] == "Your turn"
            chess_start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
            assert second["board_fen"] == chess_start_fen
