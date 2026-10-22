"""The real scan() (INTERFACES.md §1, CLAUDE.md §4.1). Pi only — opens the CSI camera.

Needs vision/calibration.json (calibrate_picam.py) and vision/empty_reference.png
(reference.py) from the current camera/board position.
"""
from datetime import datetime, timezone
from uuid import uuid4

from vision.camera import open_camera
from vision.classifier import classify, vote
from vision.homography import load_homography, warp
from vision.reference import load_reference

FRAMES_PER_SCAN = 3  # CLAUDE.md §3.6

_camera = None
_homography = None
_reference = None


def _ready():
    """Open the camera and load calibration once — the camera takes ~2s to settle, so it
    stays open for the life of the process rather than per scan."""
    global _camera, _homography, _reference
    if _camera is None:
        _homography = load_homography()
        _reference = load_reference()
        if _homography is None or _reference is None:
            raise RuntimeError("vision not calibrated: run vision/calibrate_picam.py, then "
                               "vision/reference.py with the board empty")
        _camera = open_camera()
    return _camera, _homography, _reference


def scan() -> dict:
    camera, homography, reference = _ready()
    frames = [classify(warp(camera.capture_array(), homography), reference)
              for _ in range(FRAMES_PER_SCAN)]
    squares, confidence = vote(frames)
    return {
        "type": "board_scan",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "squares": squares,
        "confidence": confidence,
        "scan_id": str(uuid4()),
    }
