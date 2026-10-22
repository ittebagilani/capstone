"""Slice the warped 800x800 top-down board into 64 square crops, a1-row-major (CLAUDE.md §4.0).

This is the one place camera row/column order becomes board order. In the warped image a1 is
bottom-left and image y grows downward, so rank 1 is the bottom row of pixels.
"""
import numpy as np

from vision.homography import WARP_SIZE

CELL = WARP_SIZE // 8

# Fraction of each cell kept around its centre. The outer band is where calibration error,
# paper seams and neighbouring pieces' overhang land; the piece crown sits in the middle.
INNER = 0.6


def square_slices(index: int, inner: float = INNER) -> tuple[slice, slice]:
    """(rows, cols) slices into the warped image for square `index` (0=a1 .. 63=h8)."""
    file, rank = index % 8, index // 8
    margin = int(CELL * (1 - inner) / 2)
    y0, x0 = (7 - rank) * CELL, file * CELL
    return slice(y0 + margin, y0 + CELL - margin), slice(x0 + margin, x0 + CELL - margin)


def slice_squares(warped: np.ndarray, inner: float = INNER) -> list[np.ndarray]:
    """64 crops, index 0 = a1, 7 = h1, 8 = a2, ... 63 = h8."""
    return [warped[square_slices(i, inner)] for i in range(64)]
