"""Reference-diff classifier on synthetic warped boards: uneven lighting (like the real rig,
dimmer toward the h-file) plus an exposure change between the reference and the live frame."""
import chess
import cv2
import numpy as np

from vision.classifier import BLACK, EMPTY, WHITE, classify, vote
from vision.homography import WARP_SIZE
from vision.squares import CELL

LIGHT, DARK = (200, 215, 210), (110, 160, 115)  # BGR: cream and green, as printed
WHITE_PIECE, BLACK_PIECE = (235, 238, 240), (35, 35, 40)


def _lighting() -> np.ndarray:
    """Falls from 1.0 at the a-file to 0.6 at the h-file."""
    return np.tile(np.linspace(1.0, 0.6, WARP_SIZE, dtype=np.float32), (WARP_SIZE, 1))[..., None]


def _render(pieces: dict[str, tuple[int, int, int]]) -> np.ndarray:
    img = np.zeros((WARP_SIZE, WARP_SIZE, 3), dtype=np.float32)
    for i in range(64):
        f, r = i % 8, i // 8
        y0, x0 = (7 - r) * CELL, f * CELL
        img[y0:y0 + CELL, x0:x0 + CELL] = DARK if (f + r) % 2 == 0 else LIGHT
    for name, colour in pieces.items():
        sq = chess.parse_square(name)
        centre = (chess.square_file(sq) * CELL + CELL // 2, (7 - chess.square_rank(sq)) * CELL + CELL // 2)
        cv2.circle(img, centre, int(CELL * 0.35), colour, -1)
    return img * _lighting()


def test_empty_board_is_all_empty_and_confident():
    ref = _render({}).astype(np.uint8)
    squares, confidence = classify(ref, ref)
    assert squares == [EMPTY] * 64
    assert min(confidence) == 1.0


def test_pieces_on_both_square_colours_under_uneven_light():
    # White and black on light and dark squares, at the bright a-file and the dim h-file.
    pieces = {"a1": WHITE_PIECE, "b1": WHITE_PIECE, "g1": WHITE_PIECE, "h1": WHITE_PIECE,
              "a8": BLACK_PIECE, "b8": BLACK_PIECE, "g8": BLACK_PIECE, "h8": BLACK_PIECE}
    ref = _render({}).astype(np.uint8)
    live = np.clip(_render(pieces) * 1.15, 0, 255).astype(np.uint8)  # auto-exposure drifted

    squares, _ = classify(live, ref)
    expected = [EMPTY] * 64
    for name, colour in pieces.items():
        expected[chess.parse_square(name)] = WHITE if colour == WHITE_PIECE else BLACK
    assert squares == expected


def test_vote_majority_and_disagreement_lowers_confidence():
    a = ([WHITE] + [EMPTY] * 63, [0.9] + [1.0] * 63)
    b = ([EMPTY] * 64, [0.8] + [1.0] * 63)
    squares, confidence = vote([a, a, b])
    assert squares[0] == WHITE
    assert confidence[0] == (0.9 + 0.9) / 3
    assert confidence[1:] == [1.0] * 63
