"""One continuous game through GameLoop: a real castle, a real promotion (via the
pause_for_promotion flow), ending in checkmate — closes the Itteba Phase 1 acceptance
criterion (CLAUDE.md §9): "full game ... including a castle and a promotion, ending in
checkmate," as one unbroken run rather than three isolated positions (test_state_machine.py
covers those in isolation).

The move sequence below (white/human plies) and the reply sequence (black/engine plies)
were constructed and verified against python-chess + the real Stockfish binary directly,
not hand-derived — see the scratch exploration in this feature's chat history if it ever
needs re-deriving for a different starting position. White: Ke1, Ra1 (queenside castling
rights), pawn one step from promotion on g7. Black: lone king, so every black reply is
forced/arbitrary and safe to script.
"""
import chess
import pytest

from backend.fake_vision import FakeVision
from backend.state import GameLoop, GameState

START_FEN = "k7/6P1/8/8/8/8/8/R3K3 w Q - 0 1"

# White (human) moves, applied via vision at each waiting_human turn, in order.
HUMAN_MOVES = ["e1c1", "g7g8q", "g8e6", "e6b6", "d1d8"]
# Black (engine) replies, played back in order — verified legal + non-losing against Stockfish.
ENGINE_REPLIES = ["a8b8", "b8c7", "c7b8", "b8c8"]


class ScriptedEngine:
    """Deterministic playback of the pre-verified replies, standing in for Stockfish."""

    def __init__(self, moves: list[str]):
        self._moves = iter(moves)

    async def play(self, board: chess.Board, think_time: float = 0.0) -> chess.Move:
        return chess.Move.from_uci(next(self._moves))


async def drain(agen):
    return [update async for update in agen]


@pytest.mark.asyncio
async def test_full_game_castle_promotion_checkmate():
    board = chess.Board(START_FEN)
    vision = FakeVision(board)
    loop = GameLoop(board, vision.scan, ScriptedEngine(ENGINE_REPLIES))

    all_updates = []
    saw_promotion_pause = False

    for i, uci in enumerate(HUMAN_MOVES):
        after = loop.board.copy()
        after.push(chess.Move.from_uci(uci))
        vision.set_board(after)

        updates = await drain(loop.play_human_turn())
        all_updates += updates

        if i == 0:
            assert board.piece_at(chess.C1).piece_type == chess.KING
            assert board.piece_at(chess.D1).piece_type == chess.ROOK

        if updates[-1]["game_state"] == GameState.PAUSE_FOR_PROMOTION.value:
            saw_promotion_pause = True
            promo_updates = await drain(loop.resolve_promotion("q"))
            all_updates += promo_updates
            assert board.piece_at(chess.G8).piece_type == chess.QUEEN

    assert saw_promotion_pause, "promotion never triggered pause_for_promotion"
    assert board.is_checkmate()
    assert all_updates[-1]["game_state"] == GameState.WAITING_HUMAN.value
    assert "CHECKMATE" in all_updates[-1]["message"]

    seen_states = [u["game_state"] for u in all_updates]
    assert GameState.SCANNING.value in seen_states
    assert GameState.THINKING.value in seen_states
    assert GameState.EXECUTING.value in seen_states
    assert GameState.PAUSE_FOR_PROMOTION.value in seen_states
