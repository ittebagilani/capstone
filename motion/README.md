# motion — actions in, machine movement out

This is the translation layer INTERFACES.md §3 describes: the backend emits high-level
actions, this package turns them into gantry G-code plus actuator serial commands and runs
them. It is the only code in the repo that reads millimetre coordinates.

Owner note: Alex owns `coordinates.json` and the machine itself. This package is the Python
side that consumes that file. It lives on the Pi as a module the backend calls, not on the
ESP32 — FluidNC stays stock, which is the point of the §3.4 decision.

---

## The chain, end to end

One human move produces one pass through this:

```
engine.play(board)          -> chess.Move          Stockfish's reply, as a python-chess object
GameLoop._actions_for(move) -> list[action dict]   chess logic ends here (backend/state.py)
compile_actions(actions)    -> MotionProgram       the exact G-code + serial lines
MotionExecutor.run(program) -> move_complete       the only step that touches hardware
```

Each arrow is a boundary you can stop at, inspect, and test. Nothing after `_actions_for`
knows the rules of chess; nothing before `compile_actions` knows a millimetre.

### 1. The move object

`Engine.play()` returns a `chess.Move`, not a string. Under the hood python-chess parses
Stockfish's UCI reply (`bestmove e7e5`) into `from_square`, `to_square` and an optional
`promotion`. If you ever take a move from somewhere else — a REST call, a log, a test
fixture — parse it the same way and validate it against the position before using it:

```python
move = chess.Move.from_uci("e7e5")      # raises ValueError on malformed text
if move not in board.legal_moves:       # the real check; from_uci only checks syntax
    raise ValueError("illegal in this position")
```

A UCI string carries only from-square, to-square and promotion. It does **not** say whether
the move is a capture, an en-passant capture, or castling — all three look like an ordinary
two-square move. That information lives in the board, which is why the next step needs the
position and not just the move.

### 2. Decomposition into actions

`GameLoop._actions_for(move)` asks the board what kind of move this is and emits the action
list from INTERFACES.md §3:

| Move | Actions |
|---|---|
| Quiet | `pick_up(from)`, `place(to)` |
| Capture | `pick_up(to)`, `move_to_graveyard`, `pick_up(from)`, `place(to)` |
| En passant | `pick_up(captured)`, `move_to_graveyard`, `pick_up(from)`, `place(to)` — the captured pawn is **not** on `to` |
| Castling | king `pick_up`/`place` then rook `pick_up`/`place`, one array |
| Promotion | never reaches here — the human places the piece (CLAUDE.md §3.7) |

This is the last place chess is understood. Everything downstream is dumb steps.

### 3. Compilation into a program

`compile_actions(actions, table, config, allocator)` returns a `MotionProgram`: a frozen
sequence of `GantryMove` and `ActuatorCommand` steps, the G-code, a printable listing, and a
checksum. It is a pure function — no ports, no clock, no randomness beyond the move id, which
is deliberately excluded from the checksum.

**Repeatability.** The same actions, the same `coordinates.json` and the same `MotionConfig`
always produce byte-identical output. Coordinates render at fixed three-decimal width so
float formatting can't drift the hash. Graveyard slots are assigned sequentially, so a
replayed game files its captures into the same slots in the same order.

That gives you three things worth having in November:

- `program.checksum` in the log per move. Two runs of e2e4 that differ mean the table changed.
- `python scripts/show_gcode.py e2e4` prints the program for any move with no hardware.
- A failed move is reproducible from the log alone — you have the exact lines that were sent.

**Rejection before movement.** The compiler tracks whether the magnet is holding a piece and
refuses a list that picks up twice in a row, places with nothing held, or ends mid-carry.
That is a backend decomposition bug, and it is far cheaper to catch as a `ValueError` than as
a piece dragged across the board. An unknown square fails here too.

**The safety prologue.** Every program opens with `UP`. A program has to be safe to start
from any machine state, and an XY move with the actuator lowered rakes the magnet across the
pieces. The actuator treats an `UP` it is already at as a no-op.

### 4. Execution

`MotionExecutor` walks the steps. For each `GantryMove` it sends the line, waits for `ok`,
then polls `?` until the status report says `Idle` — never a fixed `sleep()`. `ok` means
FluidNC accepted the line into its planner, not that the machine stopped; sending `DOWN` on
`ok` lowers the magnet into a board that is still moving. For each `ActuatorCommand` it sends
the verb and waits for `OK` or `FAULT:<reason>` before the next.

On any fault it stops where it is and returns `{"status": "error", "error": "<reason>"}`. No
retry, no automatic recovery move. The board no longer matches the stored position, and the
design already has the right answer for that: the backend goes to RECOVERY and shows the
human the stored position to restore. Dropping a held piece from clearance height to "reset"
would scatter the board and make that restoration harder.

---

## Files

| File | What it holds |
|---|---|
| `coordinates.py` | Reads `coordinates.json`; strict validation at load. Graveyard slot allocation. |
| `program.py` | `compile_actions` and the `MotionProgram` type. Pure. |
| `config.py` | Feeds, timeouts, preamble. One place to retune. |
| `fluidnc.py` | `ok` handshake, `?`-until-`Idle`, `$H`, alarm handling. |
| `actuator.py` | `OK` / `FAULT:<reason>` line protocol. |
| `transport.py` | Serial, telnet, and a scripted fake for tests. |
| `executor.py` | `MotionExecutor` (hardware) and `SimulatedExecutor` (compiles, moves nothing). |
| `factory.py` | Builds the right executor from `CXRA_MOTION` and friends. |

---

## Running it

Simulated is the default, so the backend runs on a laptop unchanged:

```bash
uvicorn backend.app:app --reload          # CXRA_MOTION=sim, compiles and logs every move
```

On the Pi with hardware attached:

```bash
export CXRA_MOTION=real
export CXRA_GANTRY=172.20.10.10           # FluidNC over WiFi (hotspot `itteba`, fixed IP), or /dev/serial/by-id/... over USB
export CXRA_ACTUATOR=/dev/ttyACM0          # or `sim`: real gantry, actuator commands only logged (dry run)
export CXRA_COORDINATES=firmware/coordinates.json   # optional, this is the default
uvicorn backend.app:app
```

Real mode homes the machine on startup. Every coordinate in the table is relative to gantry
home, so an un-homed machine puts every piece in the wrong place.

---

## Things to know before this drives a real machine

- **`CoordinateTable.synthetic()` is for tests and dry runs only.** It is a perfect grid. A
  real board is never mounted exactly square to the gantry, which is the whole reason
  calibration produces a measured table. Driving hardware from the synthetic grid puts the
  magnet in the gaps between squares.
- **`coordinates.json` does not exist yet.** It is generated during gantry calibration and is
  Alex's to produce (INTERFACES.md §5). Nothing here fabricates one. Until it lands, `sim`
  falls back to the synthetic grid. To generate it: home the gantry, jog over the centres of
  a1, h1, a8, h8, then `python scripts/make_coordinates.py --a1 X Y --h1 X Y --a8 X Y --h8 X Y`
  (`motion/calibration.py`: bilinear interpolation, graveyard beyond the far file).
- **`travel_feed_mm_min` is a guess at 6000.** These frames are sold for a ~200g laser
  module; the pick-and-place head is 400–600g. Set it from what the machine can actually
  carry without skipping steps, then write the measured number into `config.py`.
- **One `DOWN` height for every piece.** CLAUDE.md §8.1 notes that with steel in the crowns
  the pickup height varies by piece type, which would need a height argument the actuator
  command set does not have. Out of scope for now, and it needs a contract change plus a
  backend piece-height table when it is in scope.
- **Alarm recovery is manual.** `$X` exists on the link but nothing calls it automatically. A
  limit-switch alarm means something hit something; a human should look before the machine
  moves again.
