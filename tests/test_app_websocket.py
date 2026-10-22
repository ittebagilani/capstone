"""Transport tests: app.py wires GameLoop to the socket in the exact shapes INTERFACES.md §6
specifies. Game logic itself is covered by test_state_machine.py.

Requires the real Stockfish binary (the app opens one engine at startup for all
connections); skipped if it isn't on PATH. Runs with fake vision and simulated motion.
"""
import shutil

import pytest
from starlette.testclient import TestClient

from backend.app import app

pytestmark = pytest.mark.skipif(shutil.which("stockfish") is None, reason="stockfish not on PATH")

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def receive_until(ws, state):
    updates = []
    while not updates or updates[-1]["game_state"] != state:
        updates.append(ws.receive_json())
    return updates


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.app.SCAN_LOG", tmp_path / "scans.jsonl")
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            yield ws


def test_connect_is_idle_then_new_game_checks_board(ws):
    first = ws.receive_json()
    assert first["type"] == "state_update"
    assert first["game_state"] == "idle"
    assert first["board_fen"] == START_FEN

    ws.send_json({"type": "new_game", "human_color": "white", "difficulty": 3})
    updates = receive_until(ws, "waiting_human")
    assert updates[0]["game_state"] == "scanning"
    assert updates[-1]["message"] == "Your turn"
    assert updates[-1]["difficulty"] == 3
    assert updates[-1]["human_color"] == "white"


def test_debug_move_plays_a_full_turn_with_robot_verification(ws):
    ws.receive_json()
    ws.send_json({"type": "new_game"})
    receive_until(ws, "waiting_human")

    ws.send_json({"type": "debug_move", "uci": "e2e4"})
    updates = receive_until(ws, "waiting_human")
    states = [u["game_state"] for u in updates]
    assert states[:2] == ["scanning", "thinking"]
    assert "executing" in states
    assert any(u["message"] == "Checking the robot's move..." for u in updates)
    assert updates[-1]["board_fen"].split()[1] == "w"  # robot replied, human to move again


def test_human_as_black_robot_moves_first(ws):
    ws.receive_json()
    ws.send_json({"type": "new_game", "human_color": "black"})
    updates = receive_until(ws, "waiting_human")
    assert "executing" in [u["game_state"] for u in updates]
    assert updates[-1]["board_fen"].split()[1] == "b"


def test_attempt_move_before_a_game_is_refused(ws):
    ws.receive_json()
    ws.send_json({"type": "attempt_move"})
    reply = ws.receive_json()
    assert reply["game_state"] == "idle"
    assert reply["message"] == "Not your turn right now"
