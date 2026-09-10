"""Generate the 4 ArUco corner markers to print and laminate (CLAUDE.md §7.2: DICT_4X4_50,
IDs 0-3, placed outside the 8x8 grid on the board's border).

Corner assignment (not decided anywhere else — fixing it here since homography needs it):
tied to the a1-bottom-left addressing convention (INTERFACES.md §1), not to camera-relative
"top-left/bottom-right" labels, since those flip if the camera or board orientation changes.

  ID 0 -> corner nearest a1   ID 1 -> corner nearest h1
  ID 3 -> corner nearest a8   ID 2 -> corner nearest h8

Run from repo root: PYTHONPATH=. python vision/generate_markers.py
Writes vision/markers/marker_0.png .. marker_3.png (individual, full-bleed),
vision/markers/print_sheet.png (all 4, labelled, laid out on one US-Letter page), and
vision/markers/print_sheet.pdf — **print the PDF, not the PNG.** A bare PNG has no
embedded DPI, so "100% scale" is ambiguous to a print dialog and can come out the wrong
physical size; the PDF has the page size (215.9x279.4mm) baked in, so any PDF viewer's
default/100%/actual-size print settings gets it right.
"""
from pathlib import Path

import cv2
import cv2.aruco as aruco
import numpy as np
from PIL import Image

DICT = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
MARKER_MM = 40
BORDER_MM = 8  # white quiet zone each side — required for reliable detection
DPI = 300
PAGE_MM = (215.9, 279.4)  # US Letter

CORNER_LABEL = {0: "ID 0 - a1 corner", 1: "ID 1 - h1 corner", 2: "ID 2 - h8 corner", 3: "ID 3 - a8 corner"}


def mm_to_px(mm: float) -> int:
    return round(mm / 25.4 * DPI)


def marker_with_border(marker_id: int, marker_px: int, border_px: int) -> np.ndarray:
    marker_img = aruco.generateImageMarker(DICT, marker_id, marker_px)
    canvas_px = marker_px + 2 * border_px
    canvas = np.full((canvas_px, canvas_px), 255, dtype=np.uint8)
    canvas[border_px:border_px + marker_px, border_px:border_px + marker_px] = marker_img
    return canvas


def main():
    out_dir = Path(__file__).parent / "markers"
    out_dir.mkdir(exist_ok=True)

    marker_px = mm_to_px(MARKER_MM)
    border_px = mm_to_px(BORDER_MM)
    tiles = {}

    for marker_id in range(4):
        tile = marker_with_border(marker_id, marker_px, border_px)
        tiles[marker_id] = tile
        path = out_dir / f"marker_{marker_id}.png"
        cv2.imwrite(str(path), tile)
        print(f"wrote {path} ({tile.shape[0]}x{tile.shape[0]}px)")

    # One US-Letter sheet, 2x2 grid, labelled, for a single print+cut job.
    page_w, page_h = mm_to_px(PAGE_MM[0]), mm_to_px(PAGE_MM[1])
    page = np.full((page_h, page_w), 255, dtype=np.uint8)
    tile_px = tiles[0].shape[0]
    label_h = 40
    cell_w, cell_h = tile_px + 60, tile_px + label_h + 60
    grid_w, grid_h = cell_w * 2, cell_h * 2
    x0, y0 = (page_w - grid_w) // 2, (page_h - grid_h) // 2

    positions = {0: (0, 0), 1: (1, 0), 2: (1, 1), 3: (0, 1)}
    for marker_id, (col, row) in positions.items():
        cx, cy = x0 + col * cell_w + 30, y0 + row * cell_h + label_h + 30
        page[cy:cy + tile_px, cx:cx + tile_px] = tiles[marker_id]
        cv2.putText(page, CORNER_LABEL[marker_id], (cx, cy - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, 0, 2, cv2.LINE_AA)
        cv2.rectangle(page, (cx - 4, cy - 4), (cx + tile_px + 4, cy + tile_px + 4), 0, 1)

    sheet_path = out_dir / "print_sheet.png"
    cv2.imwrite(str(sheet_path), page)
    print(f"wrote {sheet_path} ({page_w}x{page_h}px @ {DPI} DPI = {PAGE_MM[0]}x{PAGE_MM[1]}mm Letter)")

    pdf_path = out_dir / "print_sheet.pdf"
    Image.fromarray(page).save(pdf_path, "PDF", resolution=DPI)
    print(f"wrote {pdf_path} — print THIS one, default settings, then cut and laminate.")


if __name__ == "__main__":
    main()
