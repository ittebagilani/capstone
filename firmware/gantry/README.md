# Gantry firmware: FluidNC

`config.yaml` is Alex's `ChessGantryBoard.ino` (PR #1) ported to stock FluidNC: same pins,
TMC5160 current and microstepping, reed-switch homing (Y then X) and the same coordinate frame.
The sketch stays useful as a bench tool. The Pi only talks FluidNC (CLAUDE.md §3.4, §11).

## Bring-up from the Pi, in this order. Keep a hand on the power.

Everything runs on the Pi with the gantry ESP32 on USB (`source .venv/bin/activate` first).
`G` below is shorthand for `python scripts/gantry.py`. With the actuator ESP32 also plugged
in, add `--port /dev/serial/by-id/...` (`G ports` lists them).

1. **Flash FluidNC:** `G flash`. This downloads FluidNC v4.1.1 and runs its own installer,
   which **erases Alex's sketch** on that ESP32. The installer ends in FluidTerm; Ctrl-] exits.
2. **Config:** `G upload`. Pushes `config.yaml` and prints the boot log. Both TMC5160s must
   come up without a driver error (an error means SPI wiring, like the sketch's
   `Driver A/B connection` check). Any config error is shown here too. After editing the
   yaml, run `G upload` again.
3. **Switches:** hold each reed switch closed and run `G send '?'`. The status must show `Pn:X`
   / `Pn:Y`. If it shows while open, drop the `:low` on that `limit_neg_pin`.
4. **Direction, before homing:** `G send '$X' 'G91 G1 X10 F300' G90`. The head must move
   the way the sketch's `D L 10` did. Then `G send 'G91 G1 Y10 F300' G90` must match `D F 10`.
   Moving diagonally means one motor is wrong: flip `:low` on one `direction_pin`. Moving
   backwards means both are wrong: flip both. Then `G upload`.
5. **Homing:** `G send --wait '$H'`. Y homes first, then X, and it ends `Idle` near `MPos:3,3`
   (the pull-off).
6. **Distance:** `G send --wait 'G90 G1 X100 Y3 F600'` and measure. More than ~1 mm off means
   `steps_per_mm` is wrong (pulley teeth or belt pitch).
7. **Travel:** jog (`G send --wait 'G90 G1 X300 Y3 F600'` …) to find the real travel, set
   `max_travel_mm`, `G upload`.
8. **Board:** jog the magnet over the centre of H8 (`G send --wait 'G90 G1 X35 Y35 F600'`,
   nudge with `G91` moves), read `MPos` with `G send '?'`, then regenerate the table:
   `python scripts/make_coordinates.py --h8 X Y --pitch 35`. Better: do all four corners
   (`--a1 … --h1 … --a8 … --h8 …`).
9. **Play:** `CXRA_MOTION=real CXRA_GANTRY=/dev/ttyUSB0 CXRA_ACTUATOR=... uvicorn backend.app:app --host 0.0.0.0`
   (see motion/README.md). Run `G term` for an interactive console at any time.

## Not carried over

- The sketch's `BOARD` / `MARK H8` / `SQUARE` commands. Square positions now live only in
  `firmware/coordinates.json` (CLAUDE.md §4.4), and the Pi sends plain mm.
- `S` / `X` stop keys. In FluidNC: `!` feed hold, `~` resume, Ctrl-X reset.
