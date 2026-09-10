"""Move inference: derive the human's move from an occupancy delta.

Backend state is authoritative, not the camera (CLAUDE.md §3.1). Vision only ever reports
empty/white/black per square (CLAUDE.md §3.2); the move is recovered by brute-force
simulating every legal move and matching the resulting occupancy pattern.
"""
import chess

EMPTY, WHITE, BLACK = "empty", "white", "black"


def board_to_squares(board: chess.Board) -> list[str]:
    """64-entry occupancy array, a1..h8 row-major (identical to chess.SQUARES — INTERFACES.md §1)."""
    squares = []
    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        squares.append(EMPTY if piece is None else (WHITE if piece.color else BLACK))
    return squares


def is_promotion_ambiguity(matches: list[chess.Move]) -> bool:
    """Four promotion choices produce byte-identical occupancy arrays — not a vision failure."""
    return (
        len(matches) > 1
        and len({(m.from_square, m.to_square) for m in matches}) == 1
        and all(m.promotion for m in matches)
    )


def infer_move(prev: list[str], observed: list[str], board: chess.Board):
    """Diff `prev` vs `observed` against board.legal_moves.

    Returns (result, status):
      (chess.Move, "ok")            - the single matching move
      (None, "no_change")           - human hasn't moved yet
      ([Move,...], "promotion_ambiguous") - same from/to, differing only in promotion piece
      ([Move,...], "ambiguous")     - genuine ambiguity, should not happen in practice
      (None, "no_match")            - illegal move, toppled piece, tampering, or misread
    """
    if observed == prev:
        return None, "no_change"

    matches = []
    for move in board.legal_moves:
        board.push(move)
        predicted = board_to_squares(board)
        board.pop()
        if predicted == observed:
            matches.append(move)

    if len(matches) == 1:
        return matches[0], "ok"
    if len(matches) == 0:
        return None, "no_match"
    if is_promotion_ambiguity(matches):
        return matches, "promotion_ambiguous"
    return matches, "ambiguous"
