"""Interactive calibration for a USB webcam (cv2.VideoCapture). For the Pi Camera Module
(ribbon/CSI cable), use calibrate_picam.py instead — a CSI camera doesn't work through
cv2.VideoCapture.

Run: PYTHONPATH=. python vision/calibrate.py [camera_index]   (camera_index defaults to 0)

Saves vision/calibration.json — see run_interactive_calibration in homography.py for the
controls (c to capture+check, s to save, q to quit). Re-run any time the camera or board
moves — that's what CLAUDE.md §7.2's explicit recalibrate() step is for.
"""
import sys

import cv2

from vision.homography import run_interactive_calibration


def main():
    cam_index = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        print(f"Could not open camera {cam_index}")
        return

    def get_frame():
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError("frame grab failed")
        return frame

    try:
        run_interactive_calibration(get_frame)
    finally:
        cap.release()


if __name__ == "__main__":
    main()
