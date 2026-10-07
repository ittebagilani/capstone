"""Runs a MotionProgram. The only part of the motion layer that touches hardware.

Signature matches the `execute_fn` hook `backend.state.GameLoop` already takes, so wiring is
one argument at the call site: actions in, `move_complete` (INTERFACES.md §3) out.

Failure policy: stop where you are and report. No automatic retry, no "drop the piece and
re-home". A fault mid-move means the physical board no longer matches the stored position,
and the only safe resolution is the RECOVERY path the design already has — show the human the
stored position and let them restore it (CLAUDE.md §6.6). Dropping a held piece from
king-height clearance would scatter the board and make that restoration harder.
"""
import asyncio
import logging
from typing import Optional

from motion.actuator import ActuatorFault, ActuatorLink
from motion.config import MotionConfig
from motion.coordinates import CoordinateError, CoordinateTable, GraveyardAllocator
from motion.fluidnc import FluidNCLink, GantryError
from motion.program import (
    ActuatorCommand,
    GantryMove,
    MotionCompileError,
    MotionProgram,
    compile_actions,
)

log = logging.getLogger("cxra.motion")


def _envelope(move_id: str, status: str, error: Optional[str] = None) -> dict:
    return {"type": "move_complete", "move_id": move_id, "status": status, "error": error}


class MotionExecutor:
    """Compile an action list, then drive the gantry and actuator through it."""

    def __init__(
        self,
        table: CoordinateTable,
        gantry: FluidNCLink,
        actuator: ActuatorLink,
        config: Optional[MotionConfig] = None,
        allocator: Optional[GraveyardAllocator] = None,
    ):
        self.table = table
        self.gantry = gantry
        self.actuator = actuator
        self.config = config or MotionConfig()
        #: One allocator for the whole game, so captures fill the graveyard in order.
        self.allocator = allocator or GraveyardAllocator(table)
        self.homed = False

    def reset(self) -> None:
        """New game: empty the graveyard bookkeeping so slots fill from the first one again."""
        self.allocator = GraveyardAllocator(self.table)

    async def open(self, home: bool = True) -> None:
        await self.gantry.open()
        await self.actuator.open()
        if home:
            # Every coordinate in the table is relative to gantry home (INTERFACES.md §5),
            # so an un-homed machine puts every piece in the wrong place.
            await self.gantry.home()
            self.homed = True

    async def close(self) -> None:
        await self.gantry.close()
        await self.actuator.close()

    async def execute(self, actions: list[dict], move_id: Optional[str] = None) -> dict:
        """Compile and run one move. Never raises — faults come back in the envelope."""
        try:
            program = compile_actions(actions, self.table, self.config, self.allocator, move_id)
        except (MotionCompileError, CoordinateError) as exc:
            # A bad action list is a backend bug, not a hardware fault. Log loudly; nothing moved.
            log.error("refusing to execute: %s", exc)
            return _envelope(move_id or "unknown", "error", "invalid_actions")

        return await self.run(program)

    async def run(self, program: MotionProgram) -> dict:
        """Execute an already-compiled program."""
        if not self.homed:
            log.warning("running a program before homing — coordinates are relative to home")
        # The full listing and its checksum go out per move, pass or fail. When a piece lands
        # a square off in November, this is the record of exactly what was commanded.
        log.info("move %s checksum %s\n%s", program.move_id, program.checksum, program.listing())

        for index, step in enumerate(program.steps):
            try:
                if isinstance(step, GantryMove):
                    await self.gantry.move_and_settle(step.gcode())
                elif isinstance(step, ActuatorCommand):
                    await self.actuator.send(step.command)
            except (GantryError, ActuatorFault) as exc:
                log.error("move %s failed at step %d (%s): %s", program.move_id, index, step, exc)
                return _envelope(program.move_id, "error", exc.code)
            except asyncio.CancelledError:
                raise
            except OSError as exc:
                log.error("move %s: link failed at step %d: %s", program.move_id, index, exc)
                return _envelope(program.move_id, "error", "link_lost")

        return _envelope(program.move_id, "ok")


class SimulatedExecutor:
    """Compiles and logs every move, moves nothing. Same envelope as the real executor.

    This is the gantry-side twin of `FakeVision`: it keeps the whole backend loop runnable on
    a laptop, and because it compiles for real, a coordinate or decomposition bug still
    surfaces here rather than on the machine.
    """

    def __init__(
        self,
        table: Optional[CoordinateTable] = None,
        config: Optional[MotionConfig] = None,
        step_delay_s: float = 0.0,
    ):
        self.table = table or CoordinateTable.synthetic()
        self.config = config or MotionConfig()
        self.allocator = GraveyardAllocator(self.table)
        self.step_delay_s = step_delay_s
        #: Every program compiled this session, for assertions in tests.
        self.programs: list[MotionProgram] = []

    def reset(self) -> None:
        """New game: empty the graveyard bookkeeping so slots fill from the first one again."""
        self.allocator = GraveyardAllocator(self.table)
        self.programs = []

    async def execute(self, actions: list[dict], move_id: Optional[str] = None) -> dict:
        try:
            program = compile_actions(actions, self.table, self.config, self.allocator, move_id)
        except (MotionCompileError, CoordinateError) as exc:
            log.error("refusing to execute: %s", exc)
            return _envelope(move_id or "unknown", "error", "invalid_actions")

        self.programs.append(program)
        log.info("[sim] move %s checksum %s\n%s", program.move_id, program.checksum, program.listing())
        if self.step_delay_s:
            await asyncio.sleep(self.step_delay_s * len(program.steps))
        return _envelope(program.move_id, "ok")
