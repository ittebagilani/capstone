"""Replay the verified castle -> promotion -> checkmate game move by move, printing the
board and every state_update after each ply.

Run from the repo root: PYTHONPATH=. python scripts/demo_full_game.py

This is the same move sequence tests/test_full_game.py asserts on — this script exists
purely so you can *watch* it instead of reading pytest dots.
"""
import asyncio

import chess

from backend.fake_vision import FakeVision
from backend.state import GameLoop, GameState
from tests.test_full_game import ENGINE_REPLIES, HUMAN_MOVES, START_FEN, ScriptedEngine


async def main():
    board = chess.Board(START_FEN)
    vision = FakeVision(board)
    loop = GameLoop(board, vision.scan, ScriptedEngine(ENGINE_REPLIES))

    print("Starting position:")
    print(board.unicode(borders=True))
    print()

    for uci in HUMAN_MOVES:
        after = loop.board.copy()
        after.push(chess.Move.from_uci(uci))
        vision.set_board(after)

        print(f">>> human plays {uci}")
        async for update in loop.play_human_turn():
            print(f"    [{update['game_state']}] {update['message']}")

        if loop.state == GameState.PAUSE_FOR_PROMOTION:
            print(">>> resolving promotion: q")
            async for update in loop.resolve_promotion("q"):
                print(f"    [{update['game_state']}] {update['message']}")

        print(board.unicode(borders=True))
        print()

    print("Checkmate!" if board.is_checkmate() else "Did not reach checkmate.")


if __name__ == "__main__":
    asyncio.run(main())
