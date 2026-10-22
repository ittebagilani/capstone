# AGENTS.md — CXRA Project Context

**Read this file fully before making changes.** It contains locked architectural decisions,
their rationale, and the conventions this codebase depends on. Decisions marked **LOCKED**
should not be revisited without an explicit request — they were made deliberately, often to
preserve parallel workstream independence between three team members, and re-opening them
costs schedule.

---

## 1. Project

**CXRA — Chess eXecution Robotic Assembly.** A capstone engineering project: a physical
autonomous chess-playing robot. A camera reads the board, a backend validates and chooses
moves, an XY gantry positions, and a telescopic actuator with an electromagnet picks and
places pieces.

**Deadline: December.** Deliverables include a demo day.

**Success definition:** reliable end-to-end loop — camera detects board state → backend
validates the human's move against chess rules → Stockfish replies → gantry positions →
actuator picks/places → repeat, for a full game without intervention.

### Team and ownership


| Person                                 | Owns                                                                 |
| -------------------------------------- | -------------------------------------------------------------------- |
| **Itteba** (primary user of this repo) | Vision system, backend, move inference, frontend integration         |
| **Alex (Alexandre Carpen)**            | XY gantry, motion control, FluidNC, square→mm coordinate table       |
| **Akil (Akil Srirama)**                | Telescopic Z actuator, end effector, electromagnet, actuator ESP32 firmware |


Because three people build in parallel, **interfaces are contracts.** Changing a payload
shape or a command string is a cross-team break, not a refactor.

---



## 2. System topology

```
Raspberry Pi  (backend + vision + motion sequencing — the sole orchestrator)
 │
 ├── in-process Python call ──→ vision.scan()          # no network layer
 ├── WiFi / serial ──────────→ ESP32 running FluidNC ─→ gantry XY
 ├── USB serial ─────────────→ ESP32 #2 (Akil) ─────────→ Z actuator + electromagnet
 └── WebSocket ─────────────→ React frontend (HMI)
```

The Pi sequences everything. The two ESP32s (gantry, actuator) never talk to each other.

---



## 3. Locked architectural decisions



### 3.1 Backend state is the source of truth — not the camera **[LOCKED]**

`python-chess` holds authoritative game state. Every camera observation is validated against
the legal move list of the stored position. The camera never overwrites state; it only
proposes a delta that must be explainable by exactly one legal move.

This one rule uniformly handles illegal moves, toppled pieces, mid-game tampering, and
misclassification — they all collapse into "observed delta matches no legal move" → ERROR.

**Do not add code paths that trust the camera over the stored board.**

### 3.2 Move inference, not piece identification **[LOCKED]**

Vision classifies each square as `empty` / `white` / `black` only. It never identifies piece
type. The move is *inferred* by simulating every legal move and finding which one produces
the observed occupancy pattern.

Piece-ID via ML is a **stretch goal**, explicitly off the critical path.

**Known blind spot (accepted):** same-color piece substitution (e.g. swapping a pawn for a
pocket queen) is undetectable by occupancy+color alone. It surfaces only indirectly when the
swapped piece later moves illegally. Documented, not fixed. Full detection requires piece-ID.

### 3.3 Vision → Backend is an in-process function call **[LOCKED]**

Both run on the same Pi, both are Python. `vision.scan() -> dict` is called directly. No
HTTP, no WebSocket, no serialization, no second process to supervise at boot.

The dict schema (§4.1) is still a hard boundary: **the backend must never reach into vision
internals** — no OpenCV objects, no raw frames. That boundary is what allows the backend to
be tested against a fake `scan()` with zero hardware, which is what makes the "full digital
game before any hardware arrives" milestone possible.

Rejected: HTTP/WebSocket between them. There is no stream — only two scan moments per turn —
so a push protocol buys nothing and costs a process, a port, and a failure mode.

**Call pattern is pull-based.** The backend requests a scan when its state machine reaches
SCANNING or SCAN_VERIFY. Vision never pushes unprompted.

### 3.4 Actuator stays a dumb serial slave on its own microcontroller **[LOCKED]**

Akil's own ESP32 (was an Arduino until Oct 14) stays a separate dumb slave, driven over USB
serial from the Pi. It does **not** run FluidNC and never talks to the gantry ESP32.

Rejected: folding actuator control into FluidNC on the ESP32 via custom M-codes. That
requires recompiling FluidNC with a plugin and welds Akil's subsystem to Alex's firmware
during the exact weeks they need to work independently. Not worth saving one $5
microcontroller.

**Consequence that must be respected in code:** the Pi must wait for the gantry to finish
moving before sending `DOWN`. With FluidNC: send the G0, wait for `ok`, then poll `?` status
until the machine reports `Idle`. **Never use a fixed** `sleep()` — it breaks the moment
acceleration is retuned.

### 3.5 Confidence is a retry trigger, never a validator **[LOCKED]**

A 0.72-confidence scan that yields a clean legal move is correct. A 0.99-confidence scan that
yields an impossible board is wrong. Confidence cannot distinguish these; chess logic can.

Algorithm on entering SCANNING:

1. Call `vision.scan()`.
2. If any square's confidence < **0.90**, discard and re-scan. Up to **3 attempts**, ~300ms
  apart (lets a shadow or a hand pass).
3. After 3 attempts, proceed with the highest-mean-confidence scan anyway.
4. Run move inference. One match → accept. Zero or multiple → ERROR/RECOVERY.

**Always log the full 64-value confidence array, pass or fail.** It is the debugging trail
for the one board position with a bad shadow, and it is real accuracy data for the report.

The 0.90 threshold is provisional — tune once against real confidence distributions after the
classifier is trained, write the tuned number here, then stop touching it.

### 3.6 Multi-frame voting lives inside `scan()` **[LOCKED]**

`scan()` captures **3 consecutive frames** and majority-votes each square before returning.
~200ms cost, removes most single-frame noise (shadow, flicker, motion blur). `confidence[i]`
is the mean across the 3 frames. Callers never know this happened.

### 3.7 Promotion is a human action, not a gantry action **[LOCKED]**

The gantry does not swap pieces. Backend detects promotion, emits `pause_for_promotion`, the
frontend prompts the human to physically place the new piece.

---



## 4. Interface contracts



### 4.0 Square addressing — the convention everything depends on

- Algebraic notation, `"a1"` .. `"h8"`. `a1` is bottom-left from White's perspective.
- Any 64-element array is **row-major starting at a1**: index 0 = a1, 1 = b1, … 7 = h1,
8 = a2, … 63 = h8.
- **This is identical to** `python-chess`**'s** `chess.SQUARES` **ordering** (`A1=0 … H8=63`), so
conversion is direct and no transposition bug is possible.
- Camera row/column order is converted to this convention **inside the vision module**,
before anything leaves it. Nowhere else.



### 4.1 Vision → Backend

Return value of `vision.scan()`:

```json
{
  "type": "board_scan",
  "timestamp": "2026-08-15T14:32:01.123Z",
  "squares": ["empty", "white", "black", "..."],
  "confidence": [0.99, 0.97, 0.95, "..."],
  "scan_id": "uuid-v4-string"
}
```

- `squares`: exactly 64 entries of `"empty"` | `"white"` | `"black"`, ordering per §4.0
- `confidence`: exactly 64 floats 0.0–1.0, index-aligned to `squares`. Per-square, not
aggregate.
- `scan_id`: correlates a SCAN_VERIFY response to the request that triggered it.



### 4.2 Backend → Motion

Backend does **not** emit raw G-code. It emits high-level actions; a thin translation layer
(Alex's) converts to G-code plus actuator serial. Keeps the backend testable headless.

```json
{
  "type": "execute_move",
  "move_id": "uuid-v4-string",
  "actions": [
    { "action": "pick_up", "square": "e7" },
    { "action": "move_to_graveyard" },
    { "action": "pick_up", "square": "e2" },
    { "action": "place", "square": "e4" }
  ]
}
```

Decomposition rules — **the backend does all chess logic; motion just executes dumb steps:**

- Normal: `pick_up(from)`, `place(to)`
- Capture: `pick_up(to)`, `move_to_graveyard`, `pick_up(from)`, `place(to)`
- Castling: king sequence + rook sequence in **one** `actions` array
- En passant: captured pawn is on a different square than `to` — backend resolves this before
emitting
- Promotion: emit `pause_for_promotion` instead

Response:

```json
{ "type": "move_complete", "move_id": "...", "status": "ok", "error": null }
```

`status` ∈ `"ok"` | `"error"`; on error, `error` is a short string (`"pickup_failed"`,
`"limit_switch_fault"`) surfaced to the frontend.

### 4.3 Backend/Motion → Actuator (serial, line-delimited)


| Command   | Meaning                                                                        |
| --------- | ------------------------------------------------------------------------------ |
| `DOWN`    | Lower to board-contact height                                                  |
| `MAG_ON`  | Energise electromagnet                                                         |
| `UP`      | Raise above king-height clearance                                              |
| `MAG_OFF` | De-energise (release)                                                          |
| `DROP`    | Release at graveyard (gantry positions XY first; actuator needs no coordinate) |


Each command is ACKed with `OK` or `FAULT:<reason>` before the next is sent. **No
fire-and-forget.**

Full pick sequence: gantry→XY, `DOWN`, `MAG_ON`, `UP`, gantry→XY, `DOWN`, `MAG_OFF`, `UP`.

### 4.4 Square → mm coordinate table

**Owner: Alex.** Lives at `/firmware/coordinates.json`. Generated during gantry calibration.
Backend and motion both read it. **Nobody hardcodes coordinates anywhere else.**

```json
{
  "square_size_mm": 48,
  "coordinates": { "a1": [24, 24], "a2": [24, 72], "h8": [360, 360] },
  "graveyard":   { "white_1": [420, 24], "black_1": [420, 120] }
}
```

Coordinates are square centres, mm, relative to gantry home (0,0). Regenerate if the board
moves relative to home.

### 4.5 Backend → Frontend (WebSocket, backend pushes)

```json
{
  "type": "state_update",
  "board_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "game_state": "waiting_human | scanning | thinking | executing | error | recovery",
  "message": "Your turn",
  "difficulty": 5
}
```

Frontend renders from **FEN**, not from the raw 64-array. FEN is the display source of truth.

---



## 4.6 Commands

```bash
source .venv/bin/activate
pip install -r requirements.txt        # sync deps after pulling changes to requirements.txt

pytest                                  # full suite
pytest tests/test_inference.py          # one file
pytest tests/test_inference.py::test_en_passant   # one test
pytest -k "not stockfish"               # skip anything needing the stockfish binary on PATH

uvicorn backend.app:app --reload        # run the backend standalone (ws://localhost:8000/ws)
```

`test_engine.py` and `test_app_websocket.py` skip themselves if `stockfish` isn't on `PATH`
(Homebrew: `/opt/homebrew/bin/stockfish`; `backend/engine.py` resolves it via `shutil.which`,
not the hardcoded `/usr/games/stockfish` path from earlier drafts of this doc).

---

## 5. Repo layout

Single monorepo — not three repos. Integration phases need PRs that touch multiple folders.

```
/vision       # camera, ArUco, homography, classifier, scan()
/backend      # state machine, inference, Stockfish, FastAPI
/frontend     # React HMI (standalone chess bot app already exists, needs wiring)
/firmware     # FluidNC config, actuator ESP32 sketch, coordinates.json
/cad          # piece models, mounts, frame
/tests
INTERFACES.md # the contract — treat as authoritative alongside this file
AGENTS.md     # this file
```

Backend module breakdown:

```
/backend
  state.py        # GameState enum + transitions
  inference.py    # infer_move — the algorithmic core
  engine.py       # Stockfish wrapper, difficulty mapping
  app.py          # FastAPI + WebSocket
  fake_vision.py  # stand-in scan() for hardware-free testing
```

---



## 6. Core algorithms and known pitfalls



### 6.1 Board → occupancy array

```python
import chess

EMPTY, WHITE, BLACK = "empty", "white", "black"

def board_to_squares(board: chess.Board) -> list[str]:
    out = []
    for sq in chess.SQUARES:            # already a1..h8 row-major — see §4.0
        p = board.piece_at(sq)
        out.append(EMPTY if p is None else (WHITE if p.color else BLACK))
    return out
```



### 6.2 Move inference — brute force, deliberately

```python
def infer_move(prev: list[str], observed: list[str], board: chess.Board):
    """Returns (result, status). status in {'ok','no_match','ambiguous','no_change'}"""
    if observed == prev:
        return None, "no_change"

    matches = []
    for move in board.legal_moves:
        board.push(move)
        predicted = board_to_squares(board)
        board.pop()
        if predicted == observed:
            matches.append(move)

    if len(matches) == 1:
        return matches[0], "ok"
    if len(matches) == 0:
        return None, "no_match"
    return matches, "ambiguous"
```

**Why brute force:** ~30 legal moves × 64 comparisons is microseconds. Castling, en passant,
and promotion fall out for free because real chess is being simulated.

**Do not hand-write per-move-type delta patterns.** That is where the bugs live.

### 6.3 The promotion ambiguity trap — handle explicitly

A pawn reaching e8 as queen/rook/bishop/knight produces **four legal moves with byte-identical
64-arrays.** `matches` will always have four entries on a promotion. This is not a vision
failure.

```python
def is_promotion_ambiguity(matches) -> bool:
    return (len(matches) > 1
            and len({(m.from_square, m.to_square) for m in matches}) == 1
            and all(m.promotion for m in matches))
```

Same from/to, differing only in promotion piece → route to `pause_for_promotion` and prompt
the frontend. **Any other ambiguity is a genuine error.**

If this isn't special-cased, promotion looks like a vision bug in October and costs a day.

### 6.4 Stockfish — two things that are easy to get wrong

**Use the async API.** `SimpleEngine` blocks; inside FastAPI's event loop it freezes the
WebSocket for the whole think time.

```python
transport, engine = await chess.engine.popen_uci("/usr/games/stockfish")
result = await engine.play(board, chess.engine.Limit(time=0.5))
```

Open the engine once at app startup, close on shutdown. **Never spawn a process per move.**

`Skill Level` **alone does not give a beginner mode.** Stockfish at Skill Level 0 still plays
~1300–1400 Elo and will crush a casual demo-day visitor. Use Elo limiting for the low end:

```python
async def configure_difficulty(engine, level: int):   # level 1..10 from frontend
    if level <= 6:
        elo = 1320 + (level - 1) * 180      # ~1320 → ~2220
        await engine.configure({"UCI_LimitStrength": True, "UCI_Elo": elo})
    else:
        await engine.configure({"UCI_LimitStrength": False,
                                "Skill Level": (level - 6) * 5})
```

Check the actual floor with `engine.options["UCI_Elo"].min` — it varies by build; newer ones
bottom out around 1320.

### 6.5 Fake vision — the hardware-free test harness

```python
class FakeVision:
    def __init__(self):
        self.next_board = None
    def scan(self):
        return {"squares": board_to_squares(self.next_board),
                "confidence": [1.0] * 64,
                "timestamp": ..., "scan_id": str(uuid4())}
```

Same dict shape as §4.1. **When real** `scan()` **lands it must be a one-line swap.** If it isn't,
the boundary leaked and that is the bug to fix.

### 6.6 State machine

`WAITING_HUMAN → SCANNING → (infer) → THINKING → EXECUTING → WAITING_HUMAN`

`no_match` / non-promotion `ambiguous` → `ERROR → RECOVERY`. In RECOVERY the HMI displays the
stored position so the human can physically restore the board.

Push a `state_update` on **every** transition.

---



## 7. Vision pipeline



### 7.1 Why ArUco markers

An overhead camera is never perfectly perpendicular — slight tilt, lens distortion, mounting
play — so the board images as a skewed trapezoid. Slicing that into 64 fixed-pixel regions
makes boundaries drift and edge squares misclassify. (This was the original "finicky light
math" failure.)

Four ArUco markers give four known reference points that OpenCV detects reliably regardless of
angle or lighting. `getPerspectiveTransform` + `warpPerspective` then unwarps to a clean
top-down image, after which slicing into 64 equal squares is exact.

This replaces the "use more cameras" feedback with one camera, four printed markers, and ~10
lines of OpenCV — and it self-corrects if the camera is bumped.

### 7.2 Implementation requirements

- `cv2.aruco.DICT_4X4_50`, IDs 0–3. Print and **laminate** — they get handled.
- **Place markers outside the 8×8 grid, on the border.** At the board corners, a piece on a1
occludes one and the homography dies mid-game.
- If the lens is wide-angle, run `cv2.calibrateCamera` once with a printed checkerboard and
undistort *before* warping. Homography does not fix barrel distortion. Test: if board edges
look curved after warping, this step was skipped.
- Warp to **800×800** → 100px per square → clean integer slicing.
- **Compute the homography once at calibration and cache it to disk.** Do not recompute per
frame — a hand over a marker mid-game must not break the read. Expose an explicit
`recalibrate()` the HMI can call.
- Convert to a1-row-major ordering here, once.



### 7.3 Accuracy targets — read this before quoting a number

**Per-square accuracy is a misleading metric.** 0.99^64 = **52%** chance of a fully correct
board read. A coin flip every scan.

- **Target: ≥99.9% per-square** (≈93% board-level).
- **Report board-level accuracy as the headline metric.** It is the honest number and the one
that shows up on demo day.
- Always report both, plus a confusion matrix — empty↔black confusion is a shadow problem,
white↔black is a lighting problem, and they have different fixes.



### 7.4 Classifier strategy

1. **Baseline first:** HSV/brightness thresholds. Measure honestly on ~10 hand-labelled
  boards. Under controlled LED lighting on an already-warped top-down image this may hit
   99.5%+, in which case the CNN becomes a validation exercise rather than a dependency.
   **Do not let CNN training block integration.**
2. **CNN:** 3 conv layers is sufficient for 3-class 64×64 crops. MobileNet-v3-small is overkill
  and slower on the Pi. Augment with brightness/contrast jitter, ±5° rotation, slight
   translation — that is what buys robustness to lighting drift.
3. Benchmark on the Pi: 64 crops × 3 frames = **192 inferences per scan**. Batch them.



### 7.5 Dataset collection

- Auto-label from FEN: set up a known position physically, capture, label all 64 crops from
the FEN. Physical setup is the bottleneck, not capture.
- **Do not hand-pick positions.** Step through real PGN games with `python-chess` and take
every 10th position — gives realistic occupancy distributions instead of 30 opening
positions where the middle four ranks are always empty.
- 3–5 frames per setup with lighting variation. ~30 setups × 64 × 4 ≈ 7,600 crops.
- **Collect the held-out test set in a separate session** — camera re-mounted, different time
of day, blinds open vs closed. A same-session split will hand you a fake 99.9%.

**Dependency:** the classifier learns the piece tops. If Akil reprints pieces with steel discs
on the crowns, the appearance changes and the dataset must be re-collected (one afternoon).
Get final piece geometry before dataset collection.

---



## 8. Hardware decisions and open questions



### 8.1 Magnetics

Ferromagnetic material is embedded in the **tops (crowns)** of pieces, not the bases.
Consequences already identified:

- Pickup Z height varies per piece type → **backend needs a piece-height lookup table**
- Increased wobble risk during transit
- Pieces need consistent flat crown geometry with matching disc diameters
- Steel disc diameter should be checked against the electromagnet pole face — a disc smaller
than the pole makes pickup unreliable



### 8.2 Gantry — UNRESOLVED, blocking

Required travel with 48mm squares and two graveyard columns: **~480mm X × ~400mm Y.**

**Correction on record:** the OpenBuilds ACRO was recommended and then withdrawn. Its
advertised "500×500" is the *footprint*; the Acro 55 has only **300mm × 300mm of travel**, the
Acro 510 is still 300mm in X, and the first model exceeding 300mm X is the Acro 1010 at
800×800 travel with a 1000×1000mm footprint. OpenBuilds also lists the system as unavailable.
**The ACRO is out.**

Current direction: a frame-only diode-laser-engraver kit (belt-driven GT2, NEMA 17, aluminium
extrusion), with the supplied controller discarded — **keep the ESP32 and FluidNC**, buy
mechanics only.

Selection criteria in priority order:

1. **X travel ≥ 480mm** — read *work area*, not machine dimensions. This is what eliminated
  the ACRO.
2. Y travel ≥ 400mm
3. **Two Y motors**, not one — single-motor cantilevers rack under the heavier pick-and-place
  head (~400–600g vs the ~200g laser module these are designed for)
4. Gantry-to-bed clearance ≥ retracted actuator + tallest king + board thickness
5. Belt-driven GT2, NEMA 17 (what FluidNC expects)

**Canadian retailers to check (stock must be verified live — cannot be confirmed from
training data):** Elmwood Electronics (Toronto), BC Robotics, Spool3D, Filaments.ca.

### 8.3 Square size — UNRESOLVED, gates everything

- 48mm squares + two graveyard columns = 480mm X. Requires a larger, pricier frame.
- 40mm squares + one graveyard column = 360mm X, 320mm Y. **Fits every cheap 400×400 machine
on the market.** Cost: tighter piece spacing, higher risk the electromagnet grabs a
neighbour. Partly mitigated by smaller steel discs and a narrower magnet pole.

Since a premade frame is likely, **the machine's work area should gate square size**, not the
reverse. Resolve before ordering. Magnet-vs-spacing math at 40mm is the outstanding analysis.

### 8.4 Camera mounting constraint

A bridge gantry bar crossing over the board means the camera **cannot** be mounted centred
overhead on the gantry frame's centreline. Either mount on a separate arm forward of the
gantry, or accept an angled view — which ArUco handles by design. Alex needs to know this
before finalising the frame.

### 8.5 Other locked physical decisions

- Graveyard positioned **opposite gantry home**, so a capture never drags a piece across the
board
- Thin plywood or acrylic playing surface — a thick board eats Z clearance
- Order 20% extra on belts, limit switches, and steel discs; two electromagnets at different
pull ratings for testing

---



## 9. Schedule


| Phase   | Window        | Content                                                             |
| ------- | ------------- | ------------------------------------------------------------------- |
| Phase 0 | → Aug 10 (M1) | Lock decisions, INTERFACES.md, BOM, order parts, repo + board setup |
| Phase 1 | → Sep 6       | Subsystem hardening in parallel — vision+backend, gantry, actuator  |
| Phase 2 | Sep–Oct       | Pairwise integration                                                |
| Phase 3 | Oct–Nov       | Full-system reliability                                             |
| Phase 4 | Nov           | Polish, deliverables, demo prep                                     |




### Itteba's Phase 1 (Aug 22 → Sep 6)

Software first — it is the only part not blocked by hardware or teammates.

- **Aug 22–24:** backend skeleton, `infer_move`, unit tests all move types, 100-game PGN
replay, Stockfish wired, fake vision. *Acceptance: full game vs Stockfish over WebSocket
with fake scans, including a castle and a promotion, ending in checkmate.*
- **Aug 25–29:** camera rig, ArUco, homography + caching, 64-crop slicing, baseline HSV
classifier measured honestly, auto-labelling script
- **Aug 30–31:** train CNN, evaluate on separate-session test set, benchmark on Pi
- **Sep 1–5:** swap real `scan()` in, 3-frame voting, tune confidence threshold, 20 full games
with a human moving pieces. *Milestone metric: moves correctly inferred / total moves.*
Sep 5 is buffer and will be needed.

---



## 10. Working conventions

- **Be concrete. Make the call.** When there is a decision to make, pick one and give the
rationale — do not present an option menu without a recommendation.
- **Acknowledge corrections explicitly.** If a previous recommendation was wrong (see the ACRO
reversal in §8.2), say so plainly rather than quietly redirecting.
- Terse, dense, actionable. No hedging.
- **Decisions get logged with rationale** — append to the table in INTERFACES.md.
- INTERFACES.md and this file are **living documents.** Update them when a decision changes;
a decision that lives only in someone's head will be re-litigated in week 10.

---



## 11. Decision log


| Decision                 | Choice                                                | Rationale                                                                                             |
| ------------------------ | ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Game state authority     | `python-chess` backend state                          | Camera proposes, chess rules dispose. Uniformly handles illegal moves, topples, tampering.            |
| Piece identification     | Not done — occupancy + colour only                    | Move inference is sufficient and far more reliable. Piece-ID is a stretch goal.                       |
| Vision→Backend transport | In-process Python call                                | Same device, same language. Removes a process, a protocol, a boot dependency. Backend stays mockable. |
| Scan initiation          | Pull-based                                            | Two scan moments per turn; no stream to justify push.                                                 |
| Frame handling           | 3-frame majority vote inside `scan()`                 | ~200ms for a large reliability win; invisible to callers.                                             |
| Actuator controller      | Separate ESP32 (was Arduino), USB serial from Pi                  | Preserves Akil's working code; decouples his subsystem from Alex's FluidNC during parallel work.      |
| Orchestration            | Pi sequences both controllers                         | One brain; no gantry↔actuator cross-talk to debug.                                                      |
| Actuator chip            | ESP32 instead of Arduino (Oct 14)                     | Akil's switch. Same USB serial line protocol, no Pi code change. Still separate from the FluidNC ESP32. |
| Gantry move completion   | Poll FluidNC `?` until `Idle`                         | Fixed sleeps break when acceleration is retuned.                                                      |
| Scan validity            | Legal-move match, not confidence                      | Chess logic is a stronger validator than classifier certainty.                                        |
| Move inference method    | Brute-force simulate all legal moves                  | Microseconds; handles castling/en passant/promotion for free.                                         |
| Promotion                | Human physically swaps, prompted by HMI               | Four promotion moves are indistinguishable in occupancy space; gantry piece-swapping is out of scope. |
| Distortion correction    | ArUco + homography, single camera                     | Same result as multi-camera at a fraction of the complexity; self-correcting if bumped.               |
| Accuracy metric          | Board-level, ≥99.9% per-square target                 | 99% per-square = 52% board-level. Per-square alone is misleading.                                     |
| Gantry: OpenBuilds ACRO  | **Rejected**                                          | Advertised 500×500 is footprint; actual travel 300×300. Insufficient X. Also unavailable.             |
| Gantry direction         | Frame-only laser engraver kit, ESP32/FluidNC retained | Mass-produced mechanics at low cost; preserves Alex's firmware investment.                            |


