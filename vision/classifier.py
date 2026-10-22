"""Baseline square classifier: compare each live square to its own empty reference
(CLAUDE.md §7.4 step 1, adapted — see vision/reference.py for why not fixed HSV cuts).

Pure numpy/OpenCV on already-warped images, so it's testable off the Pi.

Every threshold here is PROVISIONAL — set before any piece was in front of the camera.
Tune against real captures of the starting position, then record the numbers in CLAUDE.md.
"""
import cv2
import numpy as np

from vision.squares import slice_squares

EMPTY, WHITE, BLACK = "empty", "white", "black"

PIXEL_DIFF = 30.0    # per-pixel colour distance from reference that counts as "changed".
                     # Empty-board frame-to-frame noise measured at ~2.5 (Oct 22).
OCCUPIED_FRAC = 0.20  # fraction of a square's changed pixels above which it's occupied
WHITE_RATIO = 0.60    # changed-pixel brightness / local light-square brightness above which
                      # the piece is white. Dark squares sit ~0.75, black pieces should be far below.


def _gain_matched(live: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Scale live per channel so its median matches the reference's. Undoes auto-exposure and
    white-balance drift between the reference capture and now. The median ignores pieces as
    long as they cover well under half the board — 32 pieces on 60% crops is ~25%."""
    live = live.astype(np.float32)
    gain = np.median(ref.reshape(-1, 3), axis=0) / np.maximum(np.median(live.reshape(-1, 3), axis=0), 1)
    return live * gain


def _local_light_level(ref_lum: list[float]) -> list[float]:
    """Brightness of light (unoccupied-colour) squares around each square: itself if light,
    plus orthogonal neighbours that are light. Tracks the lighting falloff across the board."""
    levels = []
    for i in range(64):
        f, r = i % 8, i // 8
        near = [(f, r), (f - 1, r), (f + 1, r), (f, r - 1), (f, r + 1)]
        light = [ref_lum[nr * 8 + nf] for nf, nr in near
                 if 0 <= nf < 8 and 0 <= nr < 8 and (nf + nr) % 2 == 1]  # a1 is dark
        levels.append(float(np.mean(light)))
    return levels


def _margin_confidence(value: float, threshold: float, span: float) -> float:
    """0.5 at the threshold, rising linearly to 1.0 at `span` away from it. A heuristic until
    real confidence distributions exist — only its ordering matters to the retry rule (§3.5)."""
    return 0.5 + 0.5 * min(1.0, abs(value - threshold) / span)


def classify(live: np.ndarray, ref: np.ndarray) -> tuple[list[str], list[float]]:
    """(squares, confidence) for one warped BGR frame against the warped empty reference.
    Both lists are 64 long, a1-row-major."""
    live = _gain_matched(live, ref)
    ref_f = ref.astype(np.float32)
    live_sq, ref_sq = slice_squares(live), slice_squares(ref_f)

    def lum(img: np.ndarray) -> np.ndarray:
        return img @ np.float32([0.114, 0.587, 0.299])  # BGR -> luma

    light_level = _local_light_level([float(lum(s).mean()) for s in ref_sq])

    squares, confidence = [], []
    for i, (lv, rf) in enumerate(zip(live_sq, ref_sq)):
        changed = np.linalg.norm(lv - rf, axis=2) > PIXEL_DIFF
        frac = float(changed.mean())
        occ_conf = _margin_confidence(frac, OCCUPIED_FRAC, OCCUPIED_FRAC)
        if frac <= OCCUPIED_FRAC:
            squares.append(EMPTY)
            confidence.append(occ_conf)
            continue

        ratio = float(lum(lv)[changed].mean()) / max(light_level[i], 1.0)
        squares.append(WHITE if ratio > WHITE_RATIO else BLACK)
        confidence.append(min(occ_conf, _margin_confidence(ratio, WHITE_RATIO, 0.3)))
    return squares, confidence


def vote(frames: list[tuple[list[str], list[float]]]) -> tuple[list[str], list[float]]:
    """Per-square majority over several classified frames (CLAUDE.md §3.6). Confidence is the
    mean over frames of each frame's confidence in the winning label; a frame that voted
    otherwise contributes 0, so disagreement lowers confidence and can trigger a re-scan."""
    squares, confidence = [], []
    for i in range(64):
        labels = [f[0][i] for f in frames]
        winner = max(set(labels), key=labels.count)
        squares.append(winner)
        confidence.append(sum(f[1][i] for f in frames if f[0][i] == winner) / len(frames))
    return squares, confidence
