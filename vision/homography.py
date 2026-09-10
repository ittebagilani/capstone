"""ArUco corner detection -> homography -> warp to a canonical top-down board image
(CLAUDE.md §7.1-7.2). Compute once at calibration and cache to disk — never recompute per
frame, so a hand passing over a marker mid-game can't break the read.
"""
import json
from pathlib import Path
from typing import Callable

import cv2
import cv2.aruco as aruco
import numpy as np

DICT = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
DETECTOR = aruco.ArucoDetector(DICT, aruco.DetectorParameters())

WARP_SIZE = 800  # -> 100px/square, CLAUDE.md §7.2
CALIBRATION_PATH = Path(__file__).parent / "calibration.json"

# Destination corners in the warped image, keyed by marker ID — same corner assignment as
# generate_markers.py (ID0=a1, ID1=h1, ID2=h8, ID3=a8). a1 is bottom-left (INTERFACES.md §1),
# so it maps to the bottom-left of the image (high y — image y grows downward).
DEST_CORNERS = {
    0: (0, WARP_SIZE),          # a1 -> bottom-left
    1: (WARP_SIZE, WARP_SIZE),  # h1 -> bottom-right
    2: (WARP_SIZE, 0),          # h8 -> top-right
    3: (0, 0),                  # a8 -> top-left
}


def detect_markers(frame: np.ndarray) -> dict[int, tuple[float, float]]:
    """{marker_id: (center_x, center_y)} for every detected ID 0-3 marker in `frame`."""
    corners, ids, _ = DETECTOR.detectMarkers(frame)
    centers = {}
    if ids is None:
        return centers
    for marker_corners, marker_id in zip(corners, ids.flatten()):
        if int(marker_id) in DEST_CORNERS:
            centers[int(marker_id)] = tuple(marker_corners[0].mean(axis=0))
    return centers


def compute_homography(centers: dict[int, tuple[float, float]]) -> np.ndarray | None:
    """Needs all 4 marker IDs present. Returns the 3x3 homography, or None if incomplete."""
    if set(centers) != {0, 1, 2, 3}:
        return None
    src = np.array([centers[i] for i in range(4)], dtype=np.float32)
    dst = np.array([DEST_CORNERS[i] for i in range(4)], dtype=np.float32)
    homography, _ = cv2.findHomography(src, dst)
    return homography


def warp(frame: np.ndarray, homography: np.ndarray) -> np.ndarray:
    return cv2.warpPerspective(frame, homography, (WARP_SIZE, WARP_SIZE))


def draw_grid(img: np.ndarray) -> np.ndarray:
    """8x8 grid overlay for visually checking a warp lines up with the real board squares."""
    step = WARP_SIZE // 8
    out = img.copy()
    for i in range(9):
        cv2.line(out, (i * step, 0), (i * step, WARP_SIZE), (0, 255, 0), 1)
        cv2.line(out, (0, i * step), (WARP_SIZE, i * step), (0, 255, 0), 1)
    return out


def save_homography(homography: np.ndarray) -> None:
    CALIBRATION_PATH.write_text(json.dumps({"homography": homography.tolist()}))


def load_homography() -> np.ndarray | None:
    if not CALIBRATION_PATH.exists():
        return None
    data = json.loads(CALIBRATION_PATH.read_text())
    return np.array(data["homography"], dtype=np.float64)


def run_interactive_calibration(get_frame: Callable[[], np.ndarray]) -> None:
    """Shared live-preview calibration loop, driven by any frame source.

    'c' captures+checks once all 4 markers are detected; the warped preview then takes
    's' to save or any other key to retry. 'q' quits without saving. Used by both
    calibrate.py (USB webcam, cv2.VideoCapture) and calibrate_picam.py (Pi Camera Module,
    picamera2) — they only differ in how `get_frame` is implemented.
    """
    print("Point the camera at the board. Press 'c' once all 4 markers are detected, 'q' to quit.")
    try:
        while True:
            frame = get_frame()

            corners, ids, _ = DETECTOR.detectMarkers(frame)
            preview = aruco.drawDetectedMarkers(frame.copy(), corners, ids)
            centers = detect_markers(frame)
            cv2.putText(preview, f"detected: {sorted(centers)} / need [0, 1, 2, 3]",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            cv2.imshow("calibrate - live", preview)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break

            if key == ord('c'):
                homography = compute_homography(centers)
                if homography is None:
                    print(f"Not all 4 markers detected: {sorted(centers)}")
                    continue

                warped = warp(frame, homography)
                grid_window = "calibrate - warped (grid should align to board squares)"
                cv2.imshow(grid_window, draw_grid(warped))
                print("Check the grid lines against the real board. 's' to save, any other key to retry.")
                save_key = cv2.waitKey(0) & 0xFF
                if save_key == ord('s'):
                    save_homography(homography)
                    print("Saved vision/calibration.json")
                    break
                cv2.destroyWindow(grid_window)
    finally:
        cv2.destroyAllWindows()
