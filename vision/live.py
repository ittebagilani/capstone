"""The real scan() (INTERFACES.md §1, CLAUDE.md §4.1). Pi only — opens the CSI camera.

Needs vision/calibration.json (calibrate_picam.py) and vision/empty_reference.png
(reference.py) from the current camera/board position.
"""
from datetime import datetime, timezone
from uuid import uuid4

from vision.camera import capture_mean, open_camera
from vision.classifier import classify, vote
from vision.homography import (compute_homography, detect_markers, load_homography,
                               save_homography, warp)
from vision.reference import load_reference, save_reference

FRAMES_PER_SCAN = 3  # CLAUDE.md §3.6

_camera = None
_homography = None
_reference = None


def _get_camera():
    """Opened once — the camera takes ~2s to settle, so it stays open for the process."""
    global _camera
    if _camera is None:
        _camera = open_camera()
    return _camera


def _ready():
    global _homography, _reference
    if _homography is None or _reference is None:
        _homography = load_homography()
        _reference = load_reference()
        if _homography is None or _reference is None:
            raise RuntimeError("vision not calibrated: recalibrate from the HMI with the board "
                               "empty, or run vision/calibrate_picam.py then vision/reference.py")
    return _get_camera(), _homography, _reference


def recalibrate() -> str:
    """The HMI's explicit recalibrate (CLAUDE.md §7.2). Board must be EMPTY: one averaged
    capture gives both the homography and the empty reference, so they can't disagree."""
    global _homography, _reference
    frame = capture_mean(_get_camera())
    centers = detect_markers(frame)
    homography = compute_homography(centers)
    if homography is None:
        missing = sorted({0, 1, 2, 3} - set(centers))
        raise RuntimeError(f"marker(s) {missing} not visible")
    save_homography(homography)
    save_reference(warp(frame, homography))
    _homography, _reference = homography, load_reference()
    return "Calibrated — place the pieces and start a new game"


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
