# firmware

Owner: Alex (gantry/FluidNC, `coordinates.json`), Akil (actuator ESP32 sketch, actuator). See
CLAUDE.md §4.4 and INTERFACES.md §5 for the `coordinates.json` schema, and INTERFACES.md
§4 for the actuator serial command set.

`coordinates.json` is generated during gantry calibration — not fabricated here.

## Generating `coordinates.json`

1. Home the gantry (`H` in `ChessGantryBoard.ino`).
2. Jog the magnet over the **centre** of a1, h1, a8 and h8 in turn; read each position in mm (`P`).
3. On the Pi: `python scripts/make_coordinates.py --a1 X Y --h1 X Y --a8 X Y --h8 X Y`
4. Jog to the spot-check squares it prints (e4, d5) and confirm the magnet is centred.
5. Commit `firmware/coordinates.json`. Regenerate whenever the board moves relative to home.

The script interpolates all 64 centres from the corners and places 32 graveyard slots
(2 white + 2 black columns) beyond whichever file is farther from home. It prints the X/Y
extent; check by hand that it fits inside gantry travel.
