"""Unit tests for infer_move across every move type (Itteba Phase 1, Aug 22-24)."""
import chess

from backend.inference import board_to_squares, infer_move, is_promotion_ambiguity


def apply(board: chess.Board, uci: str) -> tuple[list[str], list[str]]:
    prev = board_to_squares(board)
    board.push(chess.Move.from_uci(uci))
    return prev, board_to_squares(board)


def test_normal_move():
    board = chess.Board()
    prev, observed = apply(board, "e2e4")
    board.pop()
    move, status = infer_move(prev, observed, board)
    assert status == "ok"
    assert move == chess.Move.from_uci("e2e4")


def test_capture():
    board = chess.Board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    prev, observed = apply(board, "e4d5")
    board.pop()
    move, status = infer_move(prev, observed, board)
    assert status == "ok"
    assert move == chess.Move.from_uci("e4d5")


def test_castling_kingside():
    board = chess.Board("rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
    prev, observed = apply(board, "e1g1")
    board.pop()
    move, status = infer_move(prev, observed, board)
    assert status == "ok"
    assert board.is_castling(move)


def test_en_passant():
    board = chess.Board("rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3")
    prev, observed = apply(board, "e5f6")
    board.pop()
    move, status = infer_move(prev, observed, board)
    assert status == "ok"
    assert board.is_en_passant(move)


def test_promotion_ambiguous():
    board = chess.Board("8/4P1k1/8/8/8/8/6K1/8 w - - 0 1")
    prev = board_to_squares(board)
    board.push(chess.Move.from_uci("e7e8q"))
    observed = board_to_squares(board)
    board.pop()
    result, status = infer_move(prev, observed, board)
    assert status == "promotion_ambiguous"
    assert len(result) == 4
    assert is_promotion_ambiguity(result)


def test_no_match():
    board = chess.Board()
    prev = board_to_squares(board)
    observed = list(prev)
    observed[0] = "empty"  # a1 rook vanishes — not a legal delta
    _, status = infer_move(prev, observed, board)
    assert status == "no_match"


def test_no_change():
    board = chess.Board()
    prev = board_to_squares(board)
    _, status = infer_move(prev, list(prev), board)
    assert status == "no_change"
