"""slice_squares must hand back a1-row-major crops (CLAUDE.md §4.0): a1 bottom-left of the warp."""
import numpy as np

from vision.homography import WARP_SIZE
from vision.squares import CELL, slice_squares


def test_crops_are_a1_row_major():
    # Paint each warped cell with its expected index: row 0 of pixels is rank 8, col 0 is file a.
    img = np.zeros((WARP_SIZE, WARP_SIZE), dtype=np.uint8)
    for rank in range(8):
        for file in range(8):
            y0 = (7 - rank) * CELL
            img[y0:y0 + CELL, file * CELL:(file + 1) * CELL] = rank * 8 + file

    crops = slice_squares(img)
    assert len(crops) == 64
    for i, crop in enumerate(crops):
        assert crop.size and (crop == i).all(), f"index {i}"
