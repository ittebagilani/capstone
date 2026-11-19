# Handoff — Mac → Pi (2026-11-19)

Context from a Claude Code session on the Mac that isn't in CLAUDE.md / INTERFACES.md yet.
Read CLAUDE.md first, then this. Delete this file once its contents are folded into the
living docs or done.

## Where things stand

- Branch: work is on `motion-layer` (pushed). The Pi also has a local branch `vision-scan`
  that has never been pushed — contents unknown. `origin/motion-layer` was merged into it to
  pick up `vision/capture_samples.py`. Check `git log origin/motion-layer..vision-scan`; if it
  holds real work, push it (`git push -u origin vision-scan`).
- Test suite: 69 passed on the Mac (2026-11-18), fake vision + simulated motion.
- Nothing in `/vision` classifies squares yet. Done: ArUco detection, homography + caching
  (`vision/homography.py`), calibration (`calibrate.py` USB / `calibrate_picam.py` CSI),
  printable board (`generate_board.py`), sample capture (`capture_samples.py`). No `scan()`.

## Physical setup right now

- Board squares are **32.5 mm** (board 260 mm). Not yet confirmed as final — CLAUDE.md §8.3
  still says unresolved. If it's final: log it in INTERFACES.md + CLAUDE.md and tell Alex
  (coordinates.json depends on it). Vision code is unaffected: homography works in square
  units.
- Camera ~45 cm above the board; whole board in frame with a small margin.
- **Only white pieces available.** Black pieces come later. Build the classifier 3-class
  anyway so black is just more samples.
- No ArUco markers on the board today. For a fixed camera, click the 4 board corners once per
  session to get the homography. Markers are for the final self-correcting setup.
- Markers for a 32.5 mm board: black area 0.75 sq ≈ 24.4 mm, centre 1 square (32.5 mm)
  outside the left/right board edge, level with the centre of rank 1 (ID0 by a1, ID1 by h1) or
  rank 8 (ID3 by a8, ID2 by h8) — per `homography.MARKER_CENTERS_SQ`. Easiest source:
  `PYTHONPATH=. python vision/generate_board.py --square-mm 32.5`, cut the tiles out. The loose
  40 mm markers from `generate_markers.py` are wrong (superseded).
- Printed-board PDF page layout, from White's side: page 1 bottom-left (a–d, 1–4), 2
  bottom-right, 3 top-right, 4 top-left. All quadrants look identical colour-wise; only the
  marker ID tells them apart.

## Next steps, in order

1. **Capture samples** (in progress): `python3 vision/capture_samples.py` with system Python
   (picamera2 is apt-installed, not in the venv). Untested on hardware — fix if it errors.
   Shot list (white only): `00_empty`, `01_start`, `02_light` (white on light squares — the
   hard case), `03_dark`, `04_mixed`..`06_mixed` (incl. edges/corners), `07_shadow`. Output:
   `vision/samples/*.jpg` + `labels.jsonl` (`{"file", "white": [...], "black": [...]}`).
2. **Corner-click warp:** click 4 board corners on one sample → homography → 800×800 warp →
   64 crops in a1-row-major order (CLAUDE.md §4.0). Same for every image in the session.
3. **HSV baseline classifier** (CLAUDE.md §7.4): measure honestly against `labels.jsonl`.
   Report per-square **and** board-level accuracy plus a confusion matrix (§7.3).
4. Then `scan()` per §4.1 / §3.6 (3-frame vote), swapped in for `FakeVision` as a one-liner.

These samples are for prototyping, not the final dataset — §7.5 wants final piece geometry,
lighting, and a separate-session test set.

## Also pending

- **ESP32 smoke tests** (both boards available): drive the gantry through
  `motion.fluidnc.FluidNCLink` (`status()`, `home()` only if limit switches are configured,
  `move_and_settle("G1 X50 Y50 F1000")`) and the actuator through `motion.actuator.ActuatorLink`
  (`UP DOWN MAG_ON UP MAG_OFF`, each must ACK `OK`). A full real move is blocked on Alex's
  `firmware/coordinates.json` — don't substitute the synthetic grid on hardware.
- **Headless access:** school wifi blocks SSH. While a keyboard is attached, add the phone
  hotspot so the Pi joins it automatically:
  `sudo nmcli connection add type wifi ifname wlan0 con-name hotspot ssid "<name>" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<pass>" connection.autoconnect yes connection.autoconnect-priority 10`
  (iPhone: Maximize Compatibility on.)

## User preference

- Lab book entries: last 7 days of work only, numbered, no open-questions or status sections.
