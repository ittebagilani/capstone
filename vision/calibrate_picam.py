"""Interactive calibration for the Raspberry Pi Camera Module (ribbon/CSI cable), via
picamera2/libcamera. Run this ON THE PI — picamera2 only installs there, not on a dev Mac.
Needs a display attached to the Pi (monitor+keyboard) for the live preview windows.

Install (on the Pi, not pip): sudo apt install -y python3-picamera2

Run: PYTHONPATH=. python vision/calibrate_picam.py [zoom]   (zoom defaults to 1.0 = widest)

Saves vision/calibration.json — see run_interactive_calibration in homography.py for the
controls (c to capture+check, s to save, q to quit).

Colors in the preview window may look swapped (red/blue) depending on picamera2's output
format — harmless here, ArUco detection doesn't depend on color channel order.
"""
import sys

from vision.camera import open_camera
from vision.homography import run_interactive_calibration


def main():
    # Optional digital zoom. scan() opens the camera at 1.0, so only change this for testing.
    zoom = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    picam2 = open_camera(zoom)

    try:
        run_interactive_calibration(picam2.capture_array)
    finally:
        picam2.stop()


if __name__ == "__main__":
    main()
