"""Tunables for the motion layer. One place, so a retune is one diff.

Nothing here is a coordinate — coordinates live only in `coordinates.json` (INTERFACES.md §5).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class MotionConfig:
    """Feeds, timeouts and protocol strings for one gantry + actuator pair.

    `frozen` on purpose: a MotionProgram records the config it was compiled with, and a
    program whose config mutated underneath it is not reproducible.
    """

    #: Travel feed in mm/min for every XY move, emitted as `G1 F<feed>`.
    #: G0 rapids run at FluidNC's configured max rate, which is tuned for a ~200g laser head;
    #: the pick-and-place head is 400-600g (CLAUDE.md §8.2) and will skip steps there. G1 with
    #: an explicit feed makes the speed a number in this file rather than a firmware setting,
    #: and makes the emitted G-code self-describing.
    travel_feed_mm_min: float = 6000.0

    #: Seconds to wait for a single `ok` from FluidNC before declaring the link dead.
    ack_timeout_s: float = 5.0

    #: Seconds to wait for the machine to leave Run state and report Idle. Generous: a full
    #: board-width move at a slow feed plus acceleration is still well under this.
    idle_timeout_s: float = 30.0

    #: Seconds between `?` status polls while waiting for Idle. Never a fixed sleep instead
    #: of polling — that breaks the moment acceleration is retuned (CLAUDE.md §3.4).
    idle_poll_interval_s: float = 0.05

    #: Seconds to wait for the actuator's `OK` / `FAULT:<reason>` line. Covers a full
    #: mechanical down-or-up stroke, which is the slowest thing in the loop.
    actuator_timeout_s: float = 10.0

    #: Emitted once at the top of every program. G21 = mm, G90 = absolute positioning.
    #: Re-asserted per program rather than assumed, so a program is valid from any prior state.
    preamble: tuple[str, ...] = ("G21", "G90")
