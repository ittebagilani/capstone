"""Warped image of the empty board, captured once right after calibration.

The classifier compares each live square to its own empty self rather than to a global
threshold: the lighting falls off across the board (a light h-file square reads darker than a
green a-file one), so no single brightness cut works. Re-capture whenever calibration is
redone or the lighting changes.

Run (Pi, board EMPTY, camera and board exactly as calibrated):
    PYTHONPATH=. python vision/reference.py
"""
from pathlib import Path

import cv2
import numpy as np

from vision.homography import load_homography, warp

REFERENCE_PATH = Path(__file__).parent / "empty_reference.png"


def save_reference(warped: np.ndarray) -> None:
    cv2.imwrite(str(REFERENCE_PATH), warped)


def load_reference() -> np.ndarray | None:
    if not REFERENCE_PATH.exists():
        return None
    return cv2.imread(str(REFERENCE_PATH))


def main():
    from vision.camera import capture_mean, open_camera  # Pi-only import

    homography = load_homography()
    if homography is None:
        print("No vision/calibration.json — run calibrate_picam.py first.")
        return
    picam2 = open_camera()
    try:
        frame = capture_mean(picam2)
    finally:
        picam2.stop()
    save_reference(warp(frame, homography))
    print(f"Saved {REFERENCE_PATH}")


if __name__ == "__main__":
    main()
