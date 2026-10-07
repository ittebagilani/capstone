"""The compiler: backend actions -> the exact machine program that executes them.

Pure. No serial ports, no sockets, no clock. Given the same actions, the same
`CoordinateTable` and the same `MotionConfig`, `compile_actions` returns byte-identical
output every time — that is what "each move has repeatable G-code" means here, and
`MotionProgram.checksum` lets you prove it in a log or a test.

Why compile first and execute second, rather than streaming commands as you walk the action
list: a program can be inspected, diffed, hashed and dry-run before anything moves, the whole
translation is testable with no hardware, and an action list that would drag a piece across
the board is rejected at compile time instead of halfway through.
"""
import hashlib
from dataclasses import dataclass, field
from typing import Optional, Sequence, Union
from uuid import uuid4

from motion.config import MotionConfig
from motion.coordinates import CoordinateTable, GraveyardAllocator

#: Actuator verbs, INTERFACES.md §4. The actuator has no coordinates and no chess knowledge.
DOWN, MAG_ON, UP, MAG_OFF, DROP = "DOWN", "MAG_ON", "UP", "MAG_OFF", "DROP"


class MotionCompileError(ValueError):
    """The action list is not executable — reject before the machine moves, not during."""


@dataclass(frozen=True)
class GantryMove:
    """One XY positioning move for FluidNC."""

    x: float
    y: float
    feed: float
    #: What this move is going to, e.g. "e4" or "graveyard:black_3". Logging and G-code
    #: comments only — never parsed.
    target: str

    def gcode(self) -> str:
        # Three decimals: 1 micron of resolution, far finer than the mechanics, and fixed
        # width so the same coordinate always renders as the same string. Float repr drift
        # would silently change the checksum.
        return f"G1 X{self.x:.3f} Y{self.y:.3f} F{self.feed:.0f}"


@dataclass(frozen=True)
class ActuatorCommand:
    """One line for Akil's Arduino. ACKed with `OK` or `FAULT:<reason>` before the next."""

    command: str
    #: Why this command is here, e.g. "grip e2". Logging only.
    target: str = ""


Step = Union[GantryMove, ActuatorCommand]


@dataclass(frozen=True)
class MotionProgram:
    """A compiled, ready-to-run sequence for exactly one chess move."""

    move_id: str
    steps: tuple[Step, ...]
    actions: tuple[dict, ...]
    config: MotionConfig
    #: Graveyard slots this program consumes, in order. Recorded so a replay reproduces them.
    graveyard_slots: tuple[str, ...] = field(default_factory=tuple)

    @property
    def gcode(self) -> tuple[str, ...]:
        """Just the gantry lines, preamble included — what FluidNC receives."""
        return tuple(self.config.preamble) + tuple(s.gcode() for s in self.steps if isinstance(s, GantryMove))

    def listing(self) -> str:
        """The whole program as text, gantry and actuator interleaved in execution order.

        This is the artefact to log per move and to paste into the report. It is also what
        `checksum` hashes, so two runs of the same move are provably identical.
        """
        lines = [f"; move {self.move_id}"]
        lines += [f"; action {i}: {a}" for i, a in enumerate(self.actions)]
        lines += list(self.config.preamble)
        for step in self.steps:
            if isinstance(step, GantryMove):
                lines.append(f"{step.gcode()}  ; -> {step.target}")
                lines.append("; wait for Idle")
            else:
                suffix = f"  ; {step.target}" if step.target else ""
                lines.append(f">{step.command}{suffix}")
        return "\n".join(lines)

    @property
    def checksum(self) -> str:
        """sha256 of the listing with the move_id line stripped.

        The move_id is a fresh uuid per move, so it must not feed the hash — otherwise two
        runs of the same move never match and the checksum proves nothing.
        """
        body = "\n".join(line for line in self.listing().split("\n") if not line.startswith("; move "))
        return hashlib.sha256(body.encode()).hexdigest()

    def __len__(self) -> int:
        return len(self.steps)


def compile_actions(
    actions: Sequence[dict],
    table: CoordinateTable,
    config: Optional[MotionConfig] = None,
    allocator: Optional[GraveyardAllocator] = None,
    move_id: Optional[str] = None,
) -> MotionProgram:
    """Compile one `execute_move` action list (INTERFACES.md §3) into a MotionProgram.

    `allocator` carries graveyard state across the whole game; pass the same one every move.
    Omitting it gives a throwaway allocator that always starts at the first slot, which is
    fine for a single compile in a test but wrong for a running game.
    """
    config = config or MotionConfig()
    allocator = allocator or GraveyardAllocator(table)

    if not actions:
        raise MotionCompileError("empty action list")

    steps: list[Step] = [
        # Safety prologue. A program must be safe to start from any machine state, and an XY
        # move with the actuator still lowered rakes the magnet across the board. The Arduino
        # treats an UP it is already at as a no-op.
        ActuatorCommand(UP, "safety: clear the board before any XY move"),
    ]
    slots: list[str] = []
    carrying = False

    for index, action in enumerate(actions):
        if not isinstance(action, dict) or "action" not in action:
            raise MotionCompileError(f"action {index}: expected a dict with an 'action' key, got {action!r}")
        kind = action["action"]

        if kind == "pick_up":
            if carrying:
                raise MotionCompileError(
                    f"action {index}: pick_up while already holding a piece — the previous "
                    "pick_up was never placed. Check the backend's move decomposition."
                )
            square = _require_square(action, index)
            x, y = table.square(square)
            steps += [
                GantryMove(x, y, config.travel_feed_mm_min, square),
                ActuatorCommand(DOWN, f"descend over {square}"),
                ActuatorCommand(MAG_ON, f"grip {square}"),
                ActuatorCommand(UP, f"lift from {square}"),
            ]
            carrying = True

        elif kind == "place":
            if not carrying:
                raise MotionCompileError(f"action {index}: place with nothing held — no preceding pick_up")
            square = _require_square(action, index)
            x, y = table.square(square)
            steps += [
                GantryMove(x, y, config.travel_feed_mm_min, square),
                ActuatorCommand(DOWN, f"descend over {square}"),
                ActuatorCommand(MAG_OFF, f"release on {square}"),
                ActuatorCommand(UP, f"retract from {square}"),
            ]
            carrying = False

        elif kind == "move_to_graveyard":
            if not carrying:
                raise MotionCompileError(
                    f"action {index}: move_to_graveyard with nothing held — a capture must "
                    "pick_up the captured piece first"
                )
            color = action.get("color")  # optional hint; see motion/README.md
            slot = allocator.allocate(color)
            slots.append(slot)
            x, y = table.graveyard_slot(slot)
            steps += [
                GantryMove(x, y, config.travel_feed_mm_min, f"graveyard:{slot}"),
                ActuatorCommand(DOWN, f"descend over {slot}"),
                ActuatorCommand(DROP, f"release into {slot}"),
                ActuatorCommand(UP, f"retract from {slot}"),
            ]
            carrying = False

        elif kind == "pause_for_promotion":
            raise MotionCompileError(
                f"action {index}: pause_for_promotion must never reach the motion layer — the "
                "human places the promoted piece (CLAUDE.md §3.7). The backend should have "
                "stopped at PAUSE_FOR_PROMOTION."
            )

        else:
            raise MotionCompileError(f"action {index}: unknown action {kind!r}")

    if carrying:
        raise MotionCompileError(
            "action list ends while still holding a piece — the gantry would park with a "
            "piece stuck to the magnet"
        )

    return MotionProgram(
        move_id=move_id or str(uuid4()),
        steps=tuple(steps),
        actions=tuple(actions),
        config=config,
        graveyard_slots=tuple(slots),
    )


def _require_square(action: dict, index: int) -> str:
    square = action.get("square")
    if not isinstance(square, str):
        raise MotionCompileError(f"action {index}: {action['action']} needs a 'square' string")
    return square
