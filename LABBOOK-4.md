# CXRA — Lab Book 4

**Itteba Gilani** — vision, backend, move inference, frontend integration
Sep 25 – Oct 1, 2026

---

## Built the motion layer — `/motion`

Nine modules, ~1,090 lines, plus a 160-line README written for Alex and Akil. This is the
translation layer INTERFACES.md §3 always described but nobody had written: high-level
actions in, G-code and actuator serial out.

The design is one split:

- `compile_actions()` — pure function. Actions plus a coordinate table in, the exact machine
  program out. No ports, no clock, no randomness.
- `MotionExecutor` — the only part that touches hardware.

Decisions and why:

- **Python on the Pi, not firmware on the ESP32.** FluidNC stays stock. The moment my code
  needs a FluidNC recompile, Alex can't work on the gantry without me.
- **Compile the whole move before executing any of it.** Streaming commands as you walk the
  action list is less code, but compiling first means a move can be printed, diffed, hashed
  and dry-run before anything moves, and an impossible action list is rejected while the
  magnet is still empty rather than halfway through a capture.
- **`G1` with an explicit feed, not `G0`.** `G0` runs at whatever max rate the firmware holds,
  and these frames are tuned for a ~200g laser module. Our head is 400–600g. A gantry that
  silently loses steps misplaces every piece after it and nothing in software can detect it.
- **Every program opens with `UP`.** A program must be safe to start from any machine state,
  and an XY move with the actuator lowered rakes the magnet across the pieces. Costs one
  no-op command per move.
- **The compiler tracks whether the magnet is holding something.** It refuses a list that
  picks up twice without placing, places with nothing held, or ends mid-carry. A `ValueError`
  at compile time beats finding out by dragging a knight across the board.
- **On a fault: stop, report, go to RECOVERY.** No retry, no drop, no re-home. Releasing a
  held piece from clearance height scatters the board and makes the human's restoration job
  harder.
- **`CoordinateTable` validates at load, not at use.** A table missing `g7` should fail when
  the backend starts, not on move 31 of the demo.
- **I did not fabricate `coordinates.json`.** The loader errors out naming whose file it is.
  There's a `synthetic()` grid for tests, documented as never for hardware — a real board is
  never mounted perfectly square to the machine, which is why calibration measures it.

### Moves are repeatable and printable

Same actions, same `coordinates.json`, same config gives byte-identical G-code every time.
Coordinates render at fixed three-decimal width so float formatting can't drift the output,
and graveyard slots are allocated sequentially so a replayed game files captures into the same
slots in the same order. Each program carries a sha256 of its own listing, with the per-move
UUID excluded — otherwise two runs of the same move never match and the checksum proves
nothing.

`python scripts/show_gcode.py e2e4` prints the program for any move with no hardware attached.

### Simulated executor

`SimulatedExecutor` is the gantry-side twin of `FakeVision`: compiles every move for real,
logs it, moves nothing. It's the default, so the backend runs end to end on my laptop, and
because it compiles for real a coordinate or decomposition bug still surfaces there instead of
on the machine. `CXRA_MOTION=real` switches to hardware.

---

## Two contract changes

Both are in INTERFACES.md and the decision logs now.

1. **`move_to_graveyard` carries an optional `color`.** Slot assignment stays the motion
   layer's business, but only the backend knows the captured piece's colour, and without it
   pieces can't be filed into matching columns. Optional, so an older sender still works.
2. **A `move_complete` with `status: "error"` now routes to ERROR → RECOVERY.** This was a
   genuine bug: the state machine was discarding the executor's return value entirely, so a
   gantry that failed mid-move left stored state claiming the move happened.

---

## Tests — 53 new, 68 total, all passing

- 26 on the compiler: every move type's decomposition compiles, the G-code matches the
  coordinate table exactly, graveyard slots never collide across a game, slot order is numeric
  so `white_10` doesn't fill before `white_2`, and every malformed action list is rejected.
- 13 on execution, against a scripted fake transport. The one that matters asserts the gantry
  reported `Idle` before the actuator was told to descend, by counting the status polls between
  the move line and the `DOWN`.
- 12 on coordinate loading: a missing square, a non-square key, a three-axis coordinate and a
  bad graveyard slot name all fail at load with a message saying what's wrong.
- 2 on the state machine for the new failure path, plus one that drives 40 moves of a game
  through the real compiler so a decomposition-versus-compiler mismatch can't hide in a rare
  move type.

Caveat worth stating: the executor has never seen hardware. The FluidNC handshake, the actuator
ACK loop and the serial read path are tested against a fake transport I wrote, which means
tested against my own assumptions about how those devices behave. The serial line-buffering in
particular is hand-rolled and has never met a real port.

---

## Found a bug in my own homography work

The marker placement rule and the warp maths disagree. I place the markers outside the 8×8 grid
so a piece on a1 can't occlude one. But `homography.py` maps the four marker *centres* to the
corners of the 800×800 warp, and the plan is to slice that into 64 equal 100px cells. The warp
therefore spans marker-to-marker, which is wider than the board, so every cell is oversized and
shifted. The middle of the board is nearly fine; the edges are badly off.

| Marker inset outside board edge | Sampling error at the a- and h-files |
| --- | --- |
| 20 mm | 0.44 squares |
| 30 mm | 0.66 squares |
| 40 mm | 0.88 squares |

Fix is to carry the physical marker inset as a calibration constant and map the markers onto an
outer margin so the inner 800×800 is exactly the playing surface. Not done yet.

The calibration screen already draws a grid over the warped preview, so this would have shown
up as visibly misaligned lines the first time I pointed it at a real board. I wrote that
overlay and never got to use it, because there's no board to point it at. A visual check I
haven't run is not a check.

---

## Topology changed: two ESP32s, not an ESP32 and an Arduino

The team is using two ESP32s, one for the gantry and one for the actuator. CLAUDE.md §2 and
§3.4 and INTERFACES.md §4 still say Arduino, and I haven't corrected them yet.

Costs nothing functionally — both links sit on the same line-transport interface, so serial or
WiFi is a constructor argument. But two identical USB-serial chips enumerate as `ttyUSB0` and
`ttyUSB1` in whatever order the kernel sees them, and that order can swap on reboot. Send a
`DOWN` to FluidNC and it rejects the line; send a `G1` to the actuator and it faults. Needs
udev rules keyed on each chip's serial number, which needs both boards in hand.

---

## Wrote the team-facing explainer

A plain-language page for Alex and Akil: the ten steps of one turn, the marker problem with a
diagram, and an explicit list of what the software needs from each of them. The asks list is
the useful part — the coordinate file and the square size block everything, and two actuator
answers could still force a contract change: whether one `DOWN` height grabs both a pawn and a
king, and whether an `UP` sent to an already-raised actuator is a no-op rather than a fault.
My safety prologue assumes it is.

---

## Next

1. Fix the homography inset and verify it with the grid overlay against a printed board.
2. Commit this week's work in reviewable pieces — it's all sitting uncommitted on `main`.
3. Slice the warped image into 64 crops, a1-row-major, converted inside the vision module and
   nowhere else.
4. HSV baseline classifier, measured honestly on ~10 hand-labelled boards.
5. Correct the Arduino references in both docs, and write the udev rules once I have both
   boards.
6. Auto-labelling script, then the dataset — not before Akil's piece geometry is final, or I
   collect it twice.
