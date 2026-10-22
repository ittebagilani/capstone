"""The printed board and the homography must agree: render the board, view it at an angle,
detect markers, warp, and check every square lands in its 100px cell (LABBOOK-4 marker bug)."""
import cv2
import numpy as np

from vision.generate_board import render
from vision.homography import WARP_SIZE, compute_homography, detect_markers, warp


def test_warp_lines_up_with_printed_squares():
    sq, dpi = 48, 80
    img = render(sq, bx0=-2 * sq, by_top=9 * sq, w_mm=12 * sq, h_mm=10 * sq, dpi=dpi)

    # Tilted camera: squash the top edge inward like an off-axis overhead view.
    h, w = img.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[0.08 * w, 0.05 * h], [0.95 * w, 0], [w, h], [0.02 * w, 0.97 * h]])
    frame = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (w, h),
                                borderValue=(255, 255, 255))

    hom = compute_homography(detect_markers(frame))
    assert hom is not None
    top_down = warp(frame, hom)

    cell = WARP_SIZE // 8
    for f in range(8):
        for r in range(8):
            dark = (f + r) % 2 == 0
            # Sample near the cell's corners, not just its centre, so a shift of >0.2 squares fails.
            for fx in (0.2, 0.8):
                for fy in (0.2, 0.8):
                    x, y = int((f + fx) * cell), int((8 - r - fy) * cell)
                    assert (top_down[y, x].mean() < 200) == dark, f"square {'abcdefgh'[f]}{r + 1}"
