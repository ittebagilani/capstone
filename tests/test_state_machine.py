"""GameLoop scenario tests: castling, promotion, checkmate (Itteba Phase 1, Aug 22-24
acceptance: "full game vs Stockfish ... including a castle and a promotion, ending in
checkmate"). Each scenario is exercised from a constructed position rather than as one
hand-scripted 40-move game — naturally reaching all three in one line isn't practical to
hand-verify, and the state machine's behavior doesn't depend on how the position arose.

Uses a deterministic stub in place of Stockfish so these stay fast and don't depend on the
Stockfish binary being on PATH; backend/engine.py itself is exercised in test_engine.py.
"""
import json

import chess
import pytest

from backend.fake_vision import FakeVision
from backend.state import GameLoop, GameState


class FirstLegalMoveEngine:
    """Deterministic engine stand-in: always plays the first legal move."""

    async def play(self, board: chess.Board, think_time: float = 0.0) -> chess.Move:
        return next(iter(board.legal_moves))


async def drain(agen):
    return [update async for update in agen]


def vision_after(board: chess.Board, uci: str) -> FakeVision:
    """FakeVision reporting `board` with `uci` already played, without mutating `board`."""
    after = board.copy()
    after.push(chess.Move.from_uci(uci))
    return FakeVision(after)


@pytest.mark.asyncio
async def test_human_castles():
    board = chess.Board("rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
    vision = vision_after(board, "e1g1")
    loop = GameLoop(board, vision.scan, FirstLegalMoveEngine())

    updates = await drain(loop.play_human_turn())

    assert board.piece_at(chess.G1).piece_type == chess.KING
    assert board.piece_at(chess.F1).piece_type == chess.ROOK
    assert [u["game_state"] for u in updates][:2] == [
        GameState.SCANNING.value,
        GameState.THINKING.value,
    ]


@pytest.mark.asyncio
async def test_human_promotes_and_resolves_choice():
    board = chess.Board("8/4P1k1/8/8/8/8/6K1/8 w - - 0 1")
    vision = vision_after(board, "e7e8q")
    loop = GameLoop(board, vision.scan, FirstLegalMoveEngine())

    updates = await drain(loop.play_human_turn())
    assert updates[-1]["game_state"] == GameState.PAUSE_FOR_PROMOTION.value
    assert board.piece_at(chess.E7).piece_type == chess.PAWN  # not yet applied

    updates = await drain(loop.resolve_promotion("q"))
    assert board.piece_at(chess.E8).piece_type == chess.QUEEN
    assert updates[0]["game_state"] == GameState.THINKING.value


@pytest.mark.asyncio
async def test_checkmate_ends_the_game_without_calling_engine():
    # One ply before fool's mate: black to move, Qd8-h4 is checkmate.
    board = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq g3 0 2")
    vision = vision_after(board, "d8h4")

    class EngineShouldNotBeCalled:
        async def play(self, *_args, **_kwargs):
            raise AssertionError("engine.play() must not be called after checkmate")

    loop = GameLoop(board, vision.scan, EngineShouldNotBeCalled())
    updates = await drain(loop.play_human_turn())

    assert board.is_checkmate()
    assert updates[-1]["game_state"] == GameState.IDLE.value
    assert "CHECKMATE" in updates[-1]["message"]


@pytest.mark.asyncio
async def test_gantry_failure_routes_to_recovery():
    """A move the gantry couldn't finish leaves the physical board out of sync with the
    stored position, so it takes the same RECOVERY path as an unreadable scan."""
    board = chess.Board()
    vision = vision_after(board, "e2e4")

    async def failing_execute(actions):
        return {"type": "move_complete", "move_id": "m1", "status": "error", "error": "pickup_failed"}

    loop = GameLoop(board, vision.scan, FirstLegalMoveEngine(), execute_fn=failing_execute)
    updates = await drain(loop.play_human_turn())

    assert [u["game_state"] for u in updates][-2:] == [
        GameState.ERROR.value,
        GameState.RECOVERY.value,
    ]
    assert "pickup_failed" in updates[-2]["message"]


@pytest.mark.asyncio
async def test_every_engine_move_of_a_game_compiles_to_gcode():
    """The decomposition and the compiler are the two halves of one contract; run them
    together over a whole game so a mismatch can't hide in a rare move type."""
    from motion.executor import SimulatedExecutor

    board = chess.Board()
    motion = SimulatedExecutor()
    loop = GameLoop(board, FakeVision(board).scan, FirstLegalMoveEngine(), execute_fn=motion.execute)

    for _ in range(40):
        if board.is_game_over():
            break
        human = next(iter(board.legal_moves))
        if human.promotion:  # promotion is a human action, not a gantry action
            break
        loop.scan_fn = vision_after(board, human.uci()).scan
        updates = await drain(loop.play_human_turn())
        assert updates[-1]["game_state"] != GameState.RECOVERY.value

    assert motion.programs
    for program in motion.programs:
        assert program.gcode[:2] == ("G21", "G90")
        assert program.checksum


@pytest.mark.asyncio
async def test_robot_move_that_didnt_land_routes_to_recovery(tmp_path):
    """The gantry acked, but the camera still sees the pre-move board: a dropped piece must
    stop the game now, not surface next turn as an "illegal" human move."""
    board = chess.Board()
    physical = vision_after(board, "e2e4")  # never updated with the robot's reply
    log = tmp_path / "scans.jsonl"
    loop = GameLoop(board, physical.scan, FirstLegalMoveEngine(),
                    verify_robot_moves=True, scan_log=log)

    updates = await drain(loop.play_human_turn())

    assert [u["game_state"] for u in updates][-2:] == [GameState.ERROR.value, GameState.RECOVERY.value]
    assert "didn't land" in updates[-2]["message"]

    # Human fixes the board to the displayed position and confirms.
    physical.set_board(board.copy())
    updates = await drain(loop.retry_scan())
    assert updates[-1]["game_state"] == GameState.WAITING_HUMAN.value

    purposes = [json.loads(line).get("purpose") for line in log.read_text().splitlines()]
    assert {"human_move", "verify_robot"} <= set(purposes)


@pytest.mark.asyncio
async def test_new_game_waits_for_correct_setup():
    board = chess.Board()
    physical = FakeVision(chess.Board("8/8/8/8/8/8/8/4K2k w - - 0 1"))  # pieces not set up
    loop = GameLoop(board, physical.scan, FirstLegalMoveEngine())

    updates = await drain(loop.new_game(chess.WHITE))
    assert updates[-1]["game_state"] == GameState.RECOVERY.value

    physical.set_board(chess.Board())
    updates = await drain(loop.retry_scan())
    assert updates[-1]["game_state"] == GameState.WAITING_HUMAN.value
