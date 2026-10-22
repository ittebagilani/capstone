"""Printable chess board with the 4 ArUco markers, split over 4 US-Letter pages to tape together.

Run from repo root: PYTHONPATH=. python vision/generate_board.py [--square-mm 30]
Writes vision/board/board_<N>mm.pdf (4 landscape pages, one quadrant each).

Print at 100% / "actual size" — never "fit to page". Each page has a 100 mm bar; measure it.
Cut each page along the board edge on its two inner (dashed) sides, butt the 4 quadrants
together, tape the back. Marker positions come from homography.MARKER_CENTERS_SQ, so the
printed board and the warp can't disagree.

Dark squares are green, not black: a black piece on a black square is the classic
empty/black confusion (CLAUDE.md §7.3). A saturated hue keeps empty squares separable from
both piece colours in HSV.
"""
import argparse
from pathlib import Path

import cv2
import cv2.aruco as aruco
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from vision.homography import DICT, MARKER_CENTERS_SQ, MARKER_SIZE_SQ

DPI = 300
PAGE_MM = (279.4, 215.9)  # US Letter, landscape
INNER_MM = 8              # paper left past each inner (cut) edge — above any printer's margin
LIGHT, DARK = (238, 238, 210), (118, 150, 86)
GUIDE = (150, 150, 150)


def render(sq_mm, bx0, by_top, w_mm, h_mm, dpi, keep=lambda x, y: True):
    """Draw the board plane in board-mm (origin a1 outer corner, y up) onto a w x h mm image
    whose top-left is board point (bx0, by_top). `keep(x_sq, y_sq)` filters items by centre."""
    s = dpi / 25.4
    img = np.full((round(h_mm * s), round(w_mm * s), 3), 255, np.uint8)
    px = lambda x: round((x * sq_mm - bx0) * s)      # square units -> image column
    py = lambda y: round((by_top - y * sq_mm) * s)   # square units -> image row

    for f in range(8):
        for r in range(8):
            if keep(f + 0.5, r + 0.5):
                img[max(py(r + 1), 0):py(r), max(px(f), 0):px(f + 1)] = DARK if (f + r) % 2 == 0 else LIGHT

    half = MARKER_SIZE_SQ / 2
    for marker_id, (cx, cy) in MARKER_CENTERS_SQ.items():
        if not keep(cx, cy):
            continue
        u0, u1, v0, v1 = px(cx - half), px(cx + half), py(cy + half), py(cy - half)
        assert u0 >= 0 and v0 >= 0 and u1 <= img.shape[1] and v1 <= img.shape[0], "marker off page"
        marker = cv2.resize(aruco.generateImageMarker(DICT, marker_id, 600), (u1 - u0, v1 - v0),
                            interpolation=cv2.INTER_NEAREST)
        img[v0:v1, u0:u1] = marker[..., None]
    return img


def page(sq_mm, right, top):
    w, h = PAGE_MM
    half = 4 * sq_mm
    bx0 = half - INNER_MM if right else half - (w - INNER_MM)
    by_top = half + (h - INNER_MM if top else INNER_MM)
    keep = lambda x, y: (x >= 4) == right and (y >= 4) == top
    img = render(sq_mm, bx0, by_top, w, h, DPI, keep)

    s = DPI / 25.4
    im = Image.fromarray(img)
    d = ImageDraw.Draw(im)
    # Cut guides: the two inner edge lines, drawn only where there's no board under them.
    cut_x, cut_y = round((half - bx0) * s), round((by_top - half) * s)
    for v in range(0, im.height, 30):
        if not 0 <= by_top - v / s <= 8 * sq_mm:
            d.line([(cut_x, v), (cut_x, v + 15)], GUIDE, 2)
    for u in range(0, im.width, 30):
        if not 0 <= bx0 + u / s <= 8 * sq_mm:
            d.line([(u, cut_y), (u + 15, cut_y)], GUIDE, 2)

    # Label + 100 mm scale bar in the outer margin.
    font = ImageFont.load_default(size=34)
    files, ranks = ("e-h" if right else "a-d"), ("5-8" if top else "1-4")
    n = {(0, 0): 1, (1, 0): 2, (1, 1): 3, (0, 1): 4}[(right, top)]
    text = (f"CXRA board {sq_mm:g}mm - page {n}/4 - files {files}, ranks {ranks}. "
            f"Print at 100%. Cut dashed edges, butt-join, tape the back.")
    y = round(4 * s) if top else im.height - round(13 * s)
    x = round(12 * s)
    d.text((x, y), text, fill=0, font=font)
    bar_y = y + round(6 * s)
    d.line([(x, bar_y), (x + round(100 * s), bar_y)], 0, 3)
    for t in (x, x + round(100 * s)):
        d.line([(t, bar_y - 15), (t, bar_y + 15)], 0, 3)
    d.text((x + round(102 * s), bar_y - 17), "must measure 100 mm", fill=0, font=font)
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--square-mm", type=float, default=30)
    sq = ap.parse_args().square_mm
    # 4 squares + marker tile reaching 1.5 squares out, plus >=6mm outer margin for the printer.
    assert 5.5 * sq + INNER_MM + 6 <= PAGE_MM[0], f"{sq}mm squares + markers don't fit on Letter"

    out = Path(__file__).parent / "board" / f"board_{sq:g}mm.pdf"
    out.parent.mkdir(exist_ok=True)
    pages = [page(sq, right, top) for right, top in ((0, 0), (1, 0), (1, 1), (0, 1))]
    pages[0].save(out, "PDF", resolution=DPI, save_all=True, append_images=pages[1:])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
