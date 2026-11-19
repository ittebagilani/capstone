"""Capture labelled scans through the exact pipeline scan() uses, for measuring the classifier
(CLAUDE.md §7.3) with vision/evaluate.py. Pi only.

Same camera stream, same cached homography, same empty reference as vision/live.py — so the
numbers evaluate.py reports are the numbers scan() will get, not those of a separate
still-capture setup with a different resolution and exposure.

Run from the repo root, after calibration (HMI recalibrate, or calibrate_picam.py + reference.py),
with the camera free (stop a CXRA_VISION=real backend first):
    PYTHONPATH=. python vision/capture_samples.py

Per shot: type a name and the occupied squares; everything unlisted is empty. A single digit
1-8 means that whole rank ("1 2" = White's starting squares). Writes, per session:
    vision/samples/<session>/reference.png        the empty reference in use
    vision/samples/<session>/<name>_f0..2.png     3 warped frames (what scan() votes over)
    vision/samples/<session>/labels.jsonl         {"name", "frames", "white", "black"}

Suggested shot list with white only: 00_empty, 01_start, 02_light (white on light squares,
the hard case), 03_dark, 04_mixed..06_mixed (include edges and corners), 07_shadow.
"""
import json
import re
from datetime import datetime
from pathlib import Path

import cv2

from vision import live
from vision.homography import warp

OUT = Path(__file__).parent / "samples"
SQUARE = re.compile(r"^[a-h][1-8]$")
RANK = re.compile(r"^[1-8]$")


def parse_squares(text: str) -> list[str] | None:
    """Space-separated squares and/or whole-rank digits -> square names, or None if malformed."""
    out = []
    for tok in text.lower().split():
        if SQUARE.match(tok):
            out.append(tok)
        elif RANK.match(tok):
            out.extend(f"{f}{tok}" for f in "abcdefgh")
        else:
            return None
    return sorted(set(out))


def ask_squares(colour: str) -> list[str]:
    while True:
        squares = parse_squares(input(f"  {colour} squares (blank for none): "))
        if squares is not None:
            return squares
        print("  use squares like e4 or a rank digit like 2, try again")


def main():
    camera, homography, reference = live._ready()
    session = OUT / datetime.now().strftime("%Y%m%d_%H%M%S")
    session.mkdir(parents=True)
    cv2.imwrite(str(session / "reference.png"), reference)
    print(f"Session {session}. Don't move the camera, board or lights from here.")

    while True:
        name = input("\nshot name (e.g. 02_light), or q to quit: ").strip()
        if name == "q":
            break
        if not name:
            continue
        white, black = ask_squares("white"), ask_squares("black")
        if set(white) & set(black):
            print(f"  {sorted(set(white) & set(black))} listed as both colours, shot skipped")
            continue
        input("  hands clear of the board, then Enter...")
        frames = []
        for k in range(live.FRAMES_PER_SCAN):
            path = session / f"{name}_f{k}.png"
            cv2.imwrite(str(path), warp(camera.capture_array(), homography))
            frames.append(path.name)
        with open(session / "labels.jsonl", "a") as f:
            f.write(json.dumps({"name": name, "frames": frames,
                                "white": white, "black": black}) + "\n")
        print(f"  saved {name} ({len(white)} white, {len(black)} black)")

    print(f"\nEvaluate with:  PYTHONPATH=. python vision/evaluate.py {session}")


if __name__ == "__main__":
    main()
