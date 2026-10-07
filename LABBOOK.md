# CXRA — Lab Book

**Itteba Gilani** — vision, backend, move inference, frontend integration  
Summer 2026 (Phase 0 + Phase 1) · compiled 2026-09-17

Everything's in one squashed commit (`b9a3360`), so there are no real per-day timestamps.
I've grouped this by workstream and used the planned schedule windows as headers — I should
fix those against my own notes before this gets handed in.

---

## What I was trying to do

Build the software half of the chess robot: camera reads the board, backend checks the
human's move is legal, Stockfish replies, gantry moves the piece. My part is everything
between the camera and the motion layer. Alex has the gantry, Akil has the Z actuator.

The thing that shaped the whole summer: three of us are building at once, so the interfaces
between our subsystems had to be nailed down before anyone wrote code against them.

---

## Phase 0 — locking the architecture (→ Aug 10)

Wrote `CLAUDE.md` and `INTERFACES.md`, ~980 lines between them. Eleven decisions recorded
with the reasoning, four interface contracts frozen: the `vision.scan()` return schema, the
backend→motion action list, the actuator serial commands, and the `state_update` the
frontend renders from.

The decisions that mattered most later:

- **Backend state is authoritative, never the camera.** The camera proposes a delta, chess
  rules decide. This means illegal moves, knocked-over pieces, someone messing with the
  board, and a misclassified square all collapse into one failure mode instead of four.
- **No piece identification — occupancy and colour only.** Figuring out the move from
  occupancy is much more reliable than classifying piece types. Piece-ID is a stretch goal.
- **Vision→backend is just a Python function call.** Same device, same language. There's no
  stream, only two scan moments per turn, so a network protocol would buy nothing and cost
  a process, a port, and a failure mode.
- **Backend emits high-level actions, not G-code.** Keeps it testable with no hardware.

I picked the square-addressing convention (`a1`..`h8`, row-major from a1) specifically
because it's byte-identical to `python-chess`'s own ordering — that makes a transposition
bug basically impossible rather than just unlikely.

---

## Phase 1 — backend (Aug 22–24)

Five modules, ~380 lines: inference, the state machine, the Stockfish wrapper, the FastAPI
WebSocket app, and a fake vision stand-in.

**Move inference works by brute force, on purpose.** Instead of writing a delta pattern for
each move type, `infer_move` pushes every legal move onto the board, converts the result to
an occupancy array, and keeps whichever ones match what the camera saw. ~30 moves × 64
comparisons is microseconds. The payoff is that castling, en passant and promotion need no
special handling at all — they fall out for free because I'm simulating real chess. Writing
per-move-type patterns by hand is where the bugs would have been.

`GameLoop` takes `scan_fn`, `engine` and `execute_fn` as arguments, which is what lets all
the tests run with no camera, no gantry, and mostly no Stockfish.

### Two things that bit me

**Promotion is genuinely ambiguous.** A pawn reaching the back rank as a queen, rook, bishop
or knight gives four legal moves with *byte-identical* occupancy arrays. Occupancy sensing
can't tell them apart even in principle. Problem is it looks exactly like a vision bug —
four matches reads as "ambiguous, error out". If I hadn't caught this now I'd have spent a
day in October debugging the wrong subsystem. Fixed by detecting the signature (same
from/to, all have a promotion piece) and routing it to a `PAUSE_FOR_PROMOTION` state that
asks the human which piece. Any *other* multi-match is still a real error.

**Stockfish has no actual beginner mode.** Skill Level 0 still plays around 1300–1400 Elo,
which would flatten anyone who wanders up at the demo. Skill level alone doesn't give a
usable range. So levels 1–6 use real Elo limiting (~1320→2220) and 7–10 use skill level. I
clamp the requested Elo against `engine.options["UCI_Elo"].min` read off the running binary,
since the floor moves between builds.

### Tests — 15, all passing

Covered every move type on its own, then castling/promotion/checkmate through the full
loop, then one continuous game that castles, promotes and ends in checkmate. Plus a real
Stockfish smoke test and a WebSocket round-trip.

The one I'm happiest with is the 100-game replay: `infer_move` recovers **every move of 100
complete games with no access to the move itself** — only the before and after occupancy
arrays. Across that many games it hits captures, castling, en passant, promotion and
checkmate at realistic rates.

One caveat I should be upfront about: the plan said replay 100 real PGN games. I didn't have
a PGN corpus available, so these are seeded random self-play games. Same move-type coverage,
but the *positions* aren't realistic. Worth swapping in real games later, and worth not
calling it a "PGN replay" in the report.

This closes the Phase 1 acceptance criterion — full game, castle, promotion, checkmate.
`scripts/demo_full_game.py` replays it printing the board so you can watch it.

Three deliberate deviations from the spec: Stockfish is found via `shutil.which()` instead
of the hardcoded `/usr/games/stockfish` (that's Debian-only and broke on my Mac); I added
`IDLE` and `PAUSE_FOR_PROMOTION` states; and I added two WebSocket message types that
**aren't in INTERFACES.md yet** — see the open questions.

---

## Phase 1 — vision (Aug 25–29)

The problem: an overhead camera is never properly perpendicular, so the board shows up as a
skewed trapezoid, and slicing that into 64 fixed regions makes the boundaries drift and the
edge squares misclassify. Made worse by the fact that the gantry's bridge bar crosses over
the board, so the camera *can't* be mounted centred overhead. The angled view isn't a defect
to engineer out, it's a given.

Fix is four printed ArUco markers. OpenCV detects them regardless of angle or lighting,
`findHomography` + `warpPerspective` unwarps to a clean top-down image, and then slicing into
64 squares is exact. One camera, four bits of laminated paper, and it self-corrects if the
camera gets bumped — just re-run calibration.

Four modules, ~280 lines: marker generation, homography + caching, and calibration scripts
for a USB webcam and for the Pi camera.

Decisions I made while building it:

- **Markers go on the border, outside the 8×8 grid.** At the grid corners a piece sitting on
  a1 covers one up and the homography dies mid-game.
- **Corner assignment is tied to chess addressing, not the camera.** ID 0 is the a1 corner,
  and so on. "Top-left" flips if the board gets turned around; a1 doesn't.
- **Homography is computed once at calibration and cached to disk**, never per frame. A hand
  passing over a marker must not be able to break the board read.
- **Warp to 800×800** — exactly 100px per square, so slicing is clean integer maths.
- **Print the PDF, not the PNG.** A bare PNG has no embedded DPI so "100%" means nothing to
  a print dialog and the markers come out the wrong physical size. Annoying trap, worth
  remembering.

**Thing that cost me time:** the Pi Camera Module goes over a ribbon cable and is completely
invisible to `cv2.VideoCapture` — it needs `picamera2`, which only installs on the Pi via
apt, not pip, so it can't be part of my normal dev setup. I pulled the calibration loop out
into one function taking a `get_frame` callable, so the two entry points differ only in how
they grab a frame and the shared interaction is written once.

---

## Where I actually am

**Done:** architecture and interfaces locked; backend complete and tested; ArUco markers,
homography, caching and calibration tooling for both camera types.

**Not done:** basically the entire second half of vision. No `scan()`. No 64-crop slicing —
the warp produces the image, nothing cuts it up yet. No HSV baseline classifier, no
auto-labelling script, no dataset, no CNN, no Pi benchmark. No lens distortion correction
(homography doesn't fix barrel distortion). No 3-frame voting, since that lives inside
`scan()`. Confidence threshold still sits at the provisional 0.90 with nothing real to tune
it against. No 20-game human test. Frontend is a README.

So: the vision pipeline is complete from camera to a clean top-down image and stops dead
there. That's the critical path and it's about a week behind.

**Blocked on hardware, not my call:** gantry still isn't chosen — the OpenBuilds ACRO got
recommended and then rejected, because its advertised "500×500" is the footprint and the
actual travel is 300×300mm, nowhere near the ~480mm X we need. Square size (40 vs 48mm) is
still open and gates the board geometry, which gates final piece geometry — which gates my
dataset, because the classifier learns the piece tops. If Akil reprints pieces with steel
discs on the crowns, I re-collect the whole dataset.

---

## Open questions

1. **0.90 confidence threshold is a guess.** Tune it once against real distributions after
   the classifier exists, write down the number, then stop touching it.
2. **Report board-level accuracy, not per-square.** 99% per-square is only a 52% chance of
   getting a full 64-square board right. Target is ≥99.9% per-square, ~93% board-level.
3. **Nothing decides when to scan.** Pull-based scanning needs some "the human is done
   moving" signal and INTERFACES.md doesn't pick one — sensor, poll loop, or a button. I
   added `attempt_move` and `recovery_confirm` to the backend as placeholders and flagged
   them as provisional in the source. Needs resolving in the contract.
4. **Known blind spot, accepted.** Swapping a pawn for a pocket queen is invisible to
   occupancy + colour. It only shows up later when that piece moves illegally. Not fixing
   it — would need piece-ID.

---

## Next

1. Slice the warped image into 64 crops, converting to a1-row-major inside the vision module
   and nowhere else.
2. Build the HSV baseline classifier and measure it honestly on ~10 hand-labelled boards. On
   an already-warped image under controlled light this might just be good enough, which
   would make the CNN a validation exercise instead of a dependency.
3. Auto-labelling script — set up a known position, capture, label all 64 crops from the
   FEN. Step through real PGN games taking every 10th position rather than hand-picking;
   hand-picked positions are all openings where the middle ranks are always empty.
4. Collect the test set **in a separate session** — camera re-mounted, different time of day.
   A same-session split will hand me a fake 99.9%.
5. Write `scan()` with 3-frame voting and swap out the fake. If that swap takes more than one
   line, the vision/backend boundary has leaked and that's the bug.
6. Wire the React frontend to the WebSocket.
