"""Motion layer: turns backend actions (INTERFACES.md §3) into gantry G-code + actuator serial.

The backend never emits G-code (CLAUDE.md §4.2). It emits `pick_up` / `place` /
`move_to_graveyard` actions; everything in this package is the translation layer that turns
those into motion, and it is the only place mm coordinates are read.

Two halves, deliberately separated:

  compile_actions()  pure, no I/O — actions -> MotionProgram (the exact G-code + serial lines)
  MotionExecutor     the only part that touches hardware — runs a MotionProgram

That split is what makes moves repeatable and testable: the same actions and the same
`coordinates.json` always compile to byte-identical G-code, which you can diff, log, hash,
and replay without a gantry attached.
"""
from motion.config import MotionConfig
from motion.coordinates import CoordinateTable, GraveyardAllocator
from motion.executor import MotionExecutor, SimulatedExecutor
from motion.program import (
    ActuatorCommand,
    GantryMove,
    MotionProgram,
    compile_actions,
)

__all__ = [
    "ActuatorCommand",
    "CoordinateTable",
    "GantryMove",
    "GraveyardAllocator",
    "MotionConfig",
    "MotionExecutor",
    "MotionProgram",
    "SimulatedExecutor",
    "compile_actions",
]
