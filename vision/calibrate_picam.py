"""Interactive calibration for the Raspberry Pi Camera Module (ribbon/CSI cable), via
picamera2/libcamera. Run this ON THE PI — picamera2 only installs there, not on a dev Mac.
Needs a display attached to the Pi (monitor+keyboard) for the live preview windows.

Install (on the Pi, not pip): sudo apt install -y python3-picamera2

Run: PYTHONPATH=. python vision/calibrate_picam.py

Saves vision/calibration.json — see run_interactive_calibration in homography.py for the
controls (c to capture+check, s to save, q to quit).

Colors in the preview window may look swapped (red/blue) depending on picamera2's output
format — harmless here, ArUco detection doesn't depend on color channel order.
"""
from picamera2 import Picamera2

from vision.homography import run_interactive_calibration


def main():
    picam2 = Picamera2()
    picam2.configure(picam2.create_preview_configuration(main={"format": "RGB888"}))
    picam2.start()

    try:
        run_interactive_calibration(picam2.capture_array)
    finally:
        picam2.stop()


if __name__ == "__main__":
    main()
