"""Pi Camera Module (imx708, CSI) setup shared by calibration, reference capture and scan().

The homography is in pixels of *this* stream. Anything that warps frames must open the camera
through here, or a different size/crop silently invalidates vision/calibration.json.
Pi only — picamera2 comes from apt (python3-picamera2), not pip.
"""
import time

import numpy as np
from libcamera import controls
from picamera2 import Picamera2

# Full-sensor 2x2-binned mode. Without this, libcamera picks the 1536x864 mode for a small
# preview, and on the imx708 that mode is a centre crop of the sensor -- the board won't fit.
SENSOR_MODE = (2304, 1296)
FRAME_SIZE = (1536, 864)  # same 16:9 aspect as the sensor, so nothing is cropped off
SETTLE_S = 2.0  # autofocus + auto-exposure need a moment after start


def open_camera(zoom: float = 1.0) -> Picamera2:
    """Started camera. zoom 1.0 = full field of view (widest possible), 2.0 = centre half."""
    picam2 = Picamera2()
    picam2.configure(picam2.create_preview_configuration(
        main={"format": "RGB888", "size": FRAME_SIZE}, sensor={"output_size": SENSOR_MODE}))
    picam2.start()

    x0, y0, w, h = picam2.camera_properties["ScalerCropMaximum"]
    cw, ch = int(w / zoom), int(h / zoom)
    picam2.set_controls({
        "ScalerCrop": (x0 + (w - cw) // 2, y0 + (h - ch) // 2, cw, ch),
        "AfMode": controls.AfModeEnum.Continuous,  # Camera Module 3 has autofocus
    })
    time.sleep(SETTLE_S)
    return picam2


def capture_mean(picam2: Picamera2, n: int = 8) -> np.ndarray:
    """Average of n frames — less sensor noise, for references rather than live scans."""
    frames = [picam2.capture_array().astype(np.float32) for _ in range(n)]
    return np.mean(frames, axis=0).round().astype(np.uint8)
