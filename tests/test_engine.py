"""Smoke test against the real Stockfish binary (Itteba Phase 1: "Stockfish wired").

Skipped if stockfish isn't on PATH rather than failing — CI/dev machines without it
shouldn't block the rest of the suite.
"""
import shutil

import chess
import pytest

from backend.engine import Engine

pytestmark = pytest.mark.skipif(shutil.which("stockfish") is None, reason="stockfish not on PATH")


@pytest.mark.asyncio
async def test_engine_plays_a_legal_move():
    engine = Engine()
    await engine.start()
    try:
        board = chess.Board()
        move = await engine.play(board, think_time=0.05)
        assert move in board.legal_moves
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_difficulty_configuration_does_not_raise():
    engine = Engine()
    await engine.start()
    try:
        await engine.configure_difficulty(1)
        await engine.configure_difficulty(10)
    finally:
        await engine.stop()
