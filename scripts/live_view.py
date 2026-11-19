"""Live classifier view: the warped board with every square's reading drawn on it, updated
continuously. Put a piece down and watch its square change. Pi only, needs the display.

Run from the repo root (camera must be free — stop a CXRA_VISION=real backend first):
    PYTHONPATH=. python scripts/live_view.py
Keys: q quit, s save a snapshot to logs/live_view_<n>.png

Per square: label (W / B / blank for empty), then changed-pixel fraction (occupied above
OCCUPIED_FRAC) and brightness ratio (white above WHITE_RATIO). Box colour: green = white
piece, magenta = black piece, red outline = confidence < 0.9 (would trigger a re-scan).
Single frame per update, no 3-frame vote, so it flickers where the real scan() wouldn't.
"""
import time
from pathlib import Path

import cv2

from scripts.scan_once import square_numbers
from vision import live
from vision.classifier import BLACK, OCCUPIED_FRAC, WHITE, WHITE_RATIO, classify
from vision.homography import warp
from vision.squares import CELL

COLOURS = {WHITE: (60, 200, 60), BLACK: (200, 60, 200)}  # BGR


def render(warped, ref):
    squares, confidence = classify(warped, ref)
    numbers = square_numbers(warped, ref)
    out = warped.copy()
    for i in range(64):
        x0, y0 = (i % 8) * CELL, (7 - i // 8) * CELL
        label, (frac, ratio) = squares[i], numbers[i]
        if label in COLOURS:
            cv2.rectangle(out, (x0 + 4, y0 + 4), (x0 + CELL - 4, y0 + CELL - 4), COLOURS[label], 4)
            cv2.putText(out, label[0].upper(), (x0 + 36, y0 + 52), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                        COLOURS[label], 3)
        if confidence[i] < 0.9:
            cv2.rectangle(out, (x0 + 1, y0 + 1), (x0 + CELL - 1, y0 + CELL - 1), (0, 0, 255), 2)
        ratio_txt = "--" if ratio != ratio else f"{ratio:.2f}"  # NaN when nothing changed
        for line, text in enumerate((f"c {frac:.2f}", f"r {ratio_txt}")):
            org = (x0 + 6, y0 + CELL - 24 + line * 16)
            cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3)
            cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
    return out


def main():
    camera, homography, ref = live._ready()
    title = f"CXRA live view  (occupied: c > {OCCUPIED_FRAC}, white: r > {WHITE_RATIO})  q quit, s save"
    snaps, last = 0, time.time()
    try:
        while True:
            frame = render(warp(camera.capture_array(), homography), ref)
            now = time.time()
            cv2.putText(frame, f"{1 / (now - last):.0f} fps", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 0, 255), 1)
            last = now
            cv2.imshow(title, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                Path("logs").mkdir(exist_ok=True)
                cv2.imwrite(f"logs/live_view_{snaps}.png", frame)
                print(f"saved logs/live_view_{snaps}.png")
                snaps += 1
    finally:
        camera.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
