# CXRA Interface Contract

This document is the single source of truth for how the vision, backend, motion, and actuator
subsystems talk to each other. If you're building against an assumption not written here,
stop and add it here first — this is what lets three people build in parallel without an
integration rewrite in October.

Owners: Itteba (vision + backend), Alex (gantry + motion + coordinate table), Akil (actuator + end effector)

**Companion document:** `CLAUDE.md` at the repo root holds the architectural rationale, the
reasons rejected alternatives were rejected, algorithm implementations, and the project
schedule. This file is the *contract*; that file is the *why*. Both are authoritative.

---

## 1. Board / square addressing convention

- Squares are addressed by standard algebraic notation: `"a1"` through `"h8"`.
- `a1` is the bottom-left square from White's perspective (standard chess convention).
- Any array of 64 values representing board state is ordered **row-major starting at a1**:
  index 0 = a1, index 1 = b1, ... index 7 = h1, index 8 = a2, ... index 63 = h8.
- This ordering is fixed. Do not transpose it, do not reorder it by camera row/column —
  convert camera-row-order to this convention inside the vision service before it ever
  leaves that module.

---

## 2. Vision → Backend

**Transport: DECIDED — in-process Python function call, not a network protocol.**

Both the vision code and the FastAPI backend run on the same Raspberry Pi and are both
Python. The backend calls `vision.scan() -> dict` directly and gets the payload below as a
return value. No HTTP server, no WebSocket, no serialization, no second process to start at
boot, no "is the vision service up?" failure mode.

The payload schema below is still the contract: vision returns exactly this dict, and the
backend must never reach into vision internals (no touching OpenCV objects, no reading
frames directly). Keeping the boundary at this dict means the backend can be unit-tested
against a fake `scan()` that returns canned board states with zero hardware attached — which
is exactly what makes the Phase 2 "digital-only full game" possible.

The call is **pull-based**: the backend requests a scan when its state machine reaches SCAN
or SCAN_VERIFY. Vision never pushes unprompted. This is why WebSocket was the wrong choice —
there's no stream, just two request/response moments per turn.

*If* vision ever needs to move off the Pi (it shouldn't), swap this one function for an HTTP
GET behind the same signature. Nothing else changes.

**Payload:**

```json
{
  "type": "board_scan",
  "timestamp": "2026-08-15T14:32:01.123Z",
  "squares": ["empty", "white", "black", "empty", "..."],
  "confidence": [0.99, 0.97, 0.95, 0.99, "..."],
  "scan_id": "uuid-v4-string"
}
```

- `squares`: exactly 64 entries, each one of `"empty"`, `"white"`, `"black"`. Ordering per
  Section 1.
- `confidence`: exactly 64 floats (0.0–1.0), same index alignment as `squares`. Per-square
  classifier confidence, not an aggregate. Backend can flag low-confidence scans instead of
  blindly trusting them.
- `scan_id`: lets backend correlate a SCAN_VERIFY response back to the request that
  triggered it.

### Multi-frame voting (required, not optional)

`scan()` internally captures **3 consecutive frames** and majority-votes each square before
returning. Costs ~200ms, removes almost all single-frame noise (a hand shadow, a flicker, a
motion-blurred frame). `confidence[i]` is the mean confidence across the 3 frames for that
square. The backend sees one clean result and never knows this happened.

---

## 3. Backend → Motion (gantry)

**Transport: DECIDED — an in-process Python call on the Pi, into `/motion`.**

The backend does **not** emit raw G-code directly. It emits a high-level action; a thin
translation layer converts this into G-code + actuator serial commands. This keeps the
backend testable without hardware attached.

That translation layer is the `/motion` package. It runs on the Pi, not on the ESP32, for the
same reason §4 keeps the actuator on its own controller: FluidNC stays stock, so nothing here
requires recompiling Alex's firmware. The backend calls
`executor.execute(actions) -> move_complete`; swap `SimulatedExecutor` for `MotionExecutor`
and the same call drives real hardware. See `motion/README.md`.

Actions are compiled to a complete machine program *before* anything moves, so the exact
G-code for a move can be printed, hashed and diffed with no hardware attached
(`python scripts/show_gcode.py e2e4`). The same move against the same `coordinates.json`
always compiles to byte-identical G-code.

**Payload:**

```json
{
  "type": "execute_move",
  "move_id": "uuid-v4-string",
  "actions": [
    { "action": "pick_up", "square": "e7" },
    { "action": "move_to_graveyard", "color": "black" },
    { "action": "pick_up", "square": "e2" },
    { "action": "place", "square": "e4" }
  ]
}
```

`move_to_graveyard` carries an **optional** `color` (`"white"` | `"black"`): the colour of the
piece being removed. Slot *assignment* stays the motion layer's business — the backend still
never names a slot — but only the backend knows what colour the captured piece was, and
without it captured pieces cannot be filed into matching columns. The field is optional: if
it is absent the motion layer still allocates the next free slot, so an older sender keeps
working.

- A single chess move decomposes into one or more `pick_up` / `place` / `move_to_graveyard`
  steps. Examples:
  - Normal move: `pick_up(from)`, `place(to)`
  - Capture: `pick_up(to)` [remove captured piece first], `move_to_graveyard`, `pick_up(from)`, `place(to)`
  - Castling: two independent move sequences (king move + rook move), sent as one `actions`
    array so the motion layer executes them in order without waiting for a new command
  - En passant: same as capture, but the removed piece is on a different square than `to`
    — backend must resolve this before emitting actions (i.e., backend does the chess
    logic, motion layer just executes dumb pick/place steps)
  - Promotion: **out of scope for gantry** — backend detects promotion, sends a
    `pause_for_promotion` action instead, frontend prompts human to physically swap the piece

**Response (motion → backend), sent when the full action sequence completes:**

```json
{
  "type": "move_complete",
  "move_id": "uuid-v4-string",
  "status": "ok",
  "error": null
}
```

`status` is `"ok"` or `"error"`; if `"error"`, `error` is a short string (e.g.
`"pickup_failed"`, `"limit_switch_fault"`) the backend surfaces to the frontend.

An `"error"` puts the backend into `error` → `recovery`: the move stopped part-way, so the
physical board no longer matches the stored position and a human has to restore it. Stored
state stays authoritative either way. Strings the motion layer emits today:

| `error` | Cause |
|---|---|
| `invalid_actions` | The action list is not executable. Nothing moved. Backend bug. |
| `gcode_rejected` | FluidNC answered `error:N` to a line. |
| `limit_switch_fault` | FluidNC alarmed, or the actuator reported that fault. |
| `gantry_timeout` | No `ok`, or still not `Idle` inside the timeout. |
| `actuator_timeout` | No `OK` / `FAULT:` from the actuator inside the timeout. |
| `link_lost` | Serial or socket died mid-move. |
| *anything else* | Passed through verbatim from the actuator's `FAULT:<reason>`. |

---

## 4. Backend/Motion → Actuator (Z-axis + electromagnet)

**Transport: DECIDED — Akil's own ESP32 (not the FluidNC one) is a dumb serial slave, driven over USB
serial directly from the Pi. The Pi is the sole orchestrator.**

Rejected alternative: folding actuator control into FluidNC on the ESP32 via custom M-codes.
That means recompiling FluidNC with a custom plugin, and it welds Akil's subsystem to Alex's
firmware — neither can be worked on or debugged independently during Phase 1. Not worth it to
save one microcontroller.

The resulting topology:

```
Pi (backend + vision + motion translation)
 ├── WiFi  → ESP32 running FluidNC → gantry XY
 └── USB serial → ESP32 #2 (Akil) → Z actuator + electromagnet
```

The Pi sequences both. Critical consequence: **the motion layer must wait for the gantry to
finish moving before sending `DOWN`.** With FluidNC, send the G0, wait for `ok`, then poll
`?` status until the machine reports `Idle`. Do not use a fixed `sleep()` — that breaks the
moment you retune acceleration.

This also means Akil's existing button-driven code survives; he replaces the button
handler with a serial-line reader and everything else stays.

**Commands (plain strings, one per line, actuator ACKs each):**

| Command | Meaning |
|---|---|
| `DOWN` | Lower actuator to board-contact height |
| `MAG_ON` | Activate electromagnet |
| `UP` | Raise actuator above king-height clearance |
| `MAG_OFF` | Deactivate electromagnet (release piece) |
| `DROP` | Release sequence at graveyard position (may just be `MAG_OFF` with no coordinate — actuator doesn't need to know it's over the graveyard, gantry positions XY first) |

- Actuator ACKs each command with `OK` or `FAULT:<reason>` before the motion layer sends
  the next one. No fire-and-forget — every step is confirmed.
- Full pick sequence from motion layer's perspective: gantry moves to square XY → `DOWN` →
  `MAG_ON` → `UP` → gantry moves to destination XY → `DOWN` → `MAG_OFF` → `UP`.

---

## 5. Square → mm coordinate table

**Owner: Alex.** Format: JSON, one entry per square, plus graveyard slots.

```json
{
  "square_size_mm": 48,
  "coordinates": {
    "a1": [24, 24],
    "a2": [24, 72],
    "...": "...",
    "h8": [360, 360]
  },
  "graveyard": {
    "white_1": [420, 24],
    "white_2": [420, 72],
    "black_1": [420, 120],
    "...": "..."
  }
}
```

- Coordinates are the center of each square, in mm, relative to gantry home (0,0).
- ⚠️ `square_size_mm: 48` above is **provisional.** The 40mm vs 48mm decision is unresolved
  and depends on which gantry is purchased (see CLAUDE.md §8.3). Nothing should hardcode 48;
  read it from this file.
- Graveyard needs **30 slots** worst case (15 white + 15 black; kings are never captured).
  Slot assignment order is the motion layer's business — the backend only ever emits
  `move_to_graveyard` with no slot index.
- This file is generated/calibrated by Alex during Phase 1 gantry setup and checked into
  the repo at `/firmware/coordinates.json`. Backend and motion layer both read from this
  file — nobody hardcodes coordinates elsewhere.
- Regenerate this file if the physical board is ever repositioned relative to gantry home.

---

## 6. Backend → Frontend

**Transport:** WebSocket, backend pushes state changes as they happen.

```json
{
  "type": "state_update",
  "board_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "game_state": "waiting_human | scanning | thinking | executing | error | recovery",
  "message": "Your turn",
  "difficulty": 5
}
```

- `board_fen`: standard FEN string, frontend renders from this — do not send the raw
  64-array to the frontend, FEN is the display source of truth.
- `game_state`: drives what the frontend UI shows (spinner during `thinking`, "hands off"
  banner during `executing`, restore prompt during `recovery`).
- `message`: human-readable string for the status banner, backend decides exact wording.
- `human_color`: `"white"` | `"black"` — which side the human plays; the HMI flips the board
  for black. Added Oct 22.

The backend sends one `state_update` on connect (`idle` until a game starts).

**Frontend → backend messages** (Oct 22; `backend/app.py` docstring is the reference):

| Message | When | Effect |
|---|---|---|
| `{"type": "new_game", "human_color": "white", "difficulty": 5}` | any time | Reset, scan to check the start position (mismatch → `recovery`), robot moves first if human is black |
| `{"type": "attempt_move"}` | `waiting_human` | Human is done moving — scan and infer. Provisional trigger: HMI button now, physical button later |
| `{"type": "promotion_choice", "piece": "q"}` | `pause_for_promotion` | See below |
| `{"type": "recovery_confirm"}` | `recovery` | Board restored — re-scan |
| `{"type": "set_difficulty", "level": 7}` | any time | 1–10, next engine move |
| `{"type": "recalibrate"}` | `idle`, board **empty** | Re-detect markers, save homography + empty reference |
| `{"type": "debug_move", "uci": "e2e4"}` | `waiting_human`, fake vision only | Simulates the human moving a piece |

After every robot move the backend re-scans and compares against the stored position; a
mismatch is `error` → `recovery` naming the wrong squares (shown during `scanning` as
"Checking the robot's move..."). Game over → `idle`.

### State machine — canonical values

These strings are the contract. Frontend switches on them; don't rename without updating both
sides.

| State | Meaning | Frontend shows |
|---|---|---|
| `idle` | No game in progress | New-game / difficulty selector |
| `waiting_human` | Human's turn, board untouched | "Your turn" |
| `scanning` | `vision.scan()` running (incl. retries) | Brief spinner |
| `thinking` | Stockfish searching | Spinner |
| `executing` | Gantry/actuator moving | **"Hands off the board"** banner |
| `pause_for_promotion` | Human must place the promoted piece | Piece picker (Q/R/B/N) |
| `error` | Observed delta matched no legal move | Error message |
| `recovery` | Awaiting manual board restoration | Rendered stored position + "restore and confirm" |

Nominal loop: `waiting_human → scanning → thinking → executing → waiting_human`
Failure path: `scanning → error → recovery → scanning`

**A `pause_for_promotion` frontend response must include the chosen piece**, e.g.
`{"type": "promotion_choice", "piece": "q"}` — the backend cannot infer it (see below).

---

## 7. Confidence handling policy

**DECIDED — confidence is a retry trigger and a debug signal. It is never the thing that
decides whether a scan is valid.**

The validator is the rule already at the heart of the design: *every observed delta must
match exactly one legal move from the known state.* A 0.72-confidence scan that produces a
clean legal move is correct. A 0.99-confidence scan that produces an impossible board is
wrong. Confidence alone can't tell you which is which — the chess logic can.

Backend algorithm on entering SCAN:

1. Call `vision.scan()`.
2. If any square's confidence < **0.90**, discard and re-scan. Up to **3 attempts total**,
   ~300ms apart (lets a shadow or a hand pass).
3. After 3 attempts, proceed with the highest-mean-confidence scan anyway.
4. Run move inference: diff against stored board state, look for a match in
   `board.legal_moves`.

| Inference outcome | Meaning | Next state |
|---|---|---|
| Exactly one match | Move accepted. **Confidence is irrelevant at this point.** | `thinking` |
| Multiple matches, all same from/to, all with a promotion piece | **Not an error** — promotion is inherently ambiguous in occupancy space | `pause_for_promotion` |
| Zero matches | Illegal move, toppled piece, tampering, or misclassification | `error` → `recovery` |
| Multiple matches, any other shape | Genuine ambiguity | `error` → `recovery` |
| No change from previous | Human hasn't moved yet | stay in `waiting_human` |

The promotion row matters: a pawn reaching the back rank as queen, rook, bishop, or knight
produces four legal moves with **byte-identical** 64-arrays. If this isn't special-cased it
looks like a vision failure. Detection: all matches share `from_square`/`to_square` and every
one has a non-null `promotion`. Implementation in CLAUDE.md §6.3.

Always log the full 64-value confidence array with the scan, pass or fail. When a square
misclassifies in October, the log tells you *which* square and *how* borderline it was —
that's how you find the one board position with a bad shadow, and it's real data for the
report's accuracy metrics.

The 0.90 threshold is a starting value. Tune it once in Phase 1 after the CNN is trained and
you have real confidence distributions; put the tuned number here and stop touching it.

---

## Decision log

| Decision | Choice | Why |
|---|---|---|
| Vision→Backend transport | In-process Python call | Same device, same language; removes a process, a protocol, and a boot dependency. Backend stays mockable for hardware-free testing. |
| Scan initiation | Pull-based, backend requests | Only two scan moments per turn. No stream to justify a push model. |
| Frame handling | 3-frame majority vote inside `scan()` | ~200ms for a large reliability win; invisible to callers. |
| Actuator controller | Separate ESP32 (was Arduino), USB serial from Pi | Preserves Akil's working code; keeps his subsystem decoupled from Alex's FluidNC firmware during parallel Phase 1 work. |
| Orchestration | Pi sequences both controllers | One brain. No gantry↔actuator cross-talk to debug. |
| Actuator chip | ESP32 instead of Arduino (Oct 14) | Akil's switch. Same USB serial line protocol (§4), no Pi-side code change. Still a separate board from the FluidNC ESP32. |
| Gantry move completion | Poll FluidNC `?` until `Idle` | Fixed sleeps break the moment acceleration is retuned. |
| Scan validity | Legal-move match, not confidence | Chess logic is a stronger validator than classifier certainty. |
| Game state authority | Backend `python-chess` board | Camera proposes a delta; chess rules dispose. Handles illegal moves, topples, and tampering uniformly. |
| Piece identification | Not performed — occupancy + colour only | Move inference is sufficient and far more reliable. Piece-ID is a stretch goal, off the critical path. |
| Promotion | Human places the piece, prompted by HMI | Four promotion moves are indistinguishable in occupancy space; gantry piece-swapping is out of scope. |
| Motion command level | High-level actions, not raw G-code | Keeps the backend testable with no hardware and puts G-code generation behind one owner. |
| Motion layer location | Python package on the Pi (`/motion`), called in-process | FluidNC stays stock — no firmware recompile, same reasoning as the separate actuator controller. |
| G-code generation | Compile the whole move up front, then execute | A move can be printed, hashed and dry-run before anything moves; an unexecutable action list is rejected before the machine is holding a piece. |
| Move repeatability | Fixed-width coordinates + sequential graveyard slots + per-move checksum | Same move + same `coordinates.json` = byte-identical G-code, so a logged failure is reproducible. |
| Graveyard slot assignment | Motion layer allocates; backend supplies an optional `color` | Only the backend knows the captured piece's colour; only the motion layer knows which slots are free. |
| Gantry fault handling | Stop, report, route to `recovery`. No auto-retry. | The board is out of sync with stored state; a human restoring it is the existing, safe answer. |
| Gantry link drop (Nov 25) | Reconnect once and resend the same line; faults still stop | A dead WiFi socket is not a machine fault. Every gantry line is idempotent (`G21`, `G90`, absolute `G1`, `?`), so resending lands in the same place. Alarms, rejections and timeouts still go to `recovery`. |

**Open items blocking Phase 1 completion:**

- [ ] Square size: 40mm vs 48mm — depends on gantry purchase (CLAUDE.md §8.3)
- [ ] Gantry selection — OpenBuilds ACRO rejected (300mm X travel, insufficient); direction is
      a frame-only laser engraver kit retaining ESP32/FluidNC
- [ ] Confidence threshold: re-tune 0.90 against real distributions once the classifier is
      trained, then record the final value here