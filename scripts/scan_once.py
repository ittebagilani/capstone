"""One live scan, printed as a board plus the numbers behind every non-empty or borderline
square. For tuning vision/classifier.py against real pieces. Pi only.

Run from the repo root:
    PYTHONPATH=. python scripts/scan_once.py                 # scan and print
    PYTHONPATH=. python scripts/scan_once.py --recalibrate   # board EMPTY: redo calibration first
    PYTHONPATH=. python scripts/scan_once.py --save out/     # also write warped.png + grid overlay
"""
import argparse
from pathlib import Path

import chess
import cv2
import numpy as np

from vision import live
from vision.classifier import OCCUPIED_FRAC, PIXEL_DIFF, WHITE_RATIO, _gain_matched, _local_light_level
from vision.homography import draw_grid, warp
from vision.squares import slice_squares

SYMBOL = {"empty": ".", "white": "W", "black": "B"}


def square_numbers(warped: np.ndarray, ref: np.ndarray) -> list[tuple[float, float]]:
    """(changed fraction, brightness ratio) per square — the two values classify() thresholds."""
    lv_all, rf_all = _gain_matched(warped, ref), ref.astype(np.float32)
    lum = lambda img: img @ np.float32([0.114, 0.587, 0.299])
    light = _local_light_level([float(lum(s).mean()) for s in slice_squares(rf_all)])
    out = []
    for i, (lv, rf) in enumerate(zip(slice_squares(lv_all), slice_squares(rf_all))):
        changed = np.linalg.norm(lv - rf, axis=2) > PIXEL_DIFF
        ratio = float(lum(lv)[changed].mean()) / light[i] if changed.any() else float("nan")
        out.append((float(changed.mean()), ratio))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recalibrate", action="store_true", help="board must be EMPTY")
    ap.add_argument("--save", type=Path)
    args = ap.parse_args()

    if args.recalibrate:
        print(live.recalibrate())

    scan = live.scan()
    camera, homography, ref = live._ready()
    warped = warp(camera.capture_array(), homography)

    print("\n    a b c d e f g h")
    for rank in range(7, -1, -1):
        row = " ".join(SYMBOL[scan["squares"][rank * 8 + f]] for f in range(8))
        print(f"  {rank + 1} {row}")
    print(f"\nthresholds: changed > {OCCUPIED_FRAC} = occupied, ratio > {WHITE_RATIO} = white")
    print("square  label  conf   changed  ratio")
    for i, (frac, ratio) in enumerate(square_numbers(warped, ref)):
        label, conf = scan["squares"][i], scan["confidence"][i]
        if label != "empty" or conf < 0.95:
            print(f"  {chess.square_name(i)}    {label:5}  {conf:.2f}   {frac:.3f}    {ratio:.2f}")

    if args.save:
        args.save.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.save / "warped.png"), warped)
        cv2.imwrite(str(args.save / "warped_grid.png"), draw_grid(warped))
        print(f"\nsaved images to {args.save}/")


if __name__ == "__main__":
    main()
