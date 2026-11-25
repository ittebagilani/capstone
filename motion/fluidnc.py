"""FluidNC link: send a line, wait for `ok`, then poll `?` until the machine reports Idle.

The wait-for-Idle is the load-bearing part (CLAUDE.md §3.4, INTERFACES.md §4). FluidNC
answers `ok` when it has *accepted* a G-code line into its planner buffer, not when the
machine has finished moving. Sending `DOWN` on `ok` lowers the magnet into a moving board.
The only correct completion signal is the real-time status report going Idle — and never a
fixed `sleep()`, which silently breaks the first time acceleration is retuned.
"""
import asyncio
import logging
import re

from motion.config import MotionConfig
from motion.transport import LineTransport, TransportError

log = logging.getLogger("cxra.motion.gantry")

#: `<Idle|MPos:0.000,0.000,0.000|FS:0,0>` — the state is everything up to the first `|`.
STATUS_RE = re.compile(r"^<([A-Za-z]+)[:|>]")

#: States that mean the machine is not going to become Idle on its own.
HALTED_STATES = {"Alarm", "Door", "Sleep"}


def _link_down(exc: BaseException) -> bool:
    """A dead connection (WiFi drop, ESP32 reconnect, unplugged cable), as opposed to FluidNC
    answering badly. Timeouts are excluded: those are converted to GantryError where they
    happen and mean the machine itself didn't respond in time."""
    return isinstance(exc, (ConnectionError, TransportError, OSError)) and not isinstance(exc, TimeoutError)


class GantryError(RuntimeError):
    """FluidNC rejected a line, alarmed, or stopped answering."""

    def __init__(self, message: str, code: str = "gantry_fault"):
        super().__init__(message)
        #: Short machine-readable reason for the `move_complete` envelope (INTERFACES.md §3).
        self.code = code


class FluidNCLink:
    def __init__(self, transport: LineTransport, config: MotionConfig | None = None):
        self.transport = transport
        self.config = config or MotionConfig()
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        await self.transport.open()
        # FluidNC greets with a banner on connect; drain it so the first `ok` isn't misread.
        await self._drain()

    async def close(self) -> None:
        await self.transport.close()

    async def _reconnect(self) -> None:
        try:
            await self.transport.close()
        except Exception:
            pass
        await self.transport.open()
        await self._drain()

    async def _with_reconnect(self, what: str, op):
        """Run `op()`; if the link turns out to be dead, reconnect once and run it again.

        Safe because everything sent through here is idempotent: unit/mode lines, absolute
        `G90 G1` moves to a fixed point, and `?`. Resending lands in the same place. This is
        a link repair, not a fault retry: alarms, rejections and timeouts are GantryErrors
        and still stop the move (the "no auto-retry" decision, INTERFACES.md).
        Over WiFi the one long-lived telnet socket can die between moves; without this,
        every later move fails with "Connection reset by peer" (seen 2026-11-25).
        """
        try:
            return await op()
        except Exception as exc:
            if not _link_down(exc):
                raise
            log.warning("gantry link lost during %s (%s); reconnecting once", what, exc)
        await self._reconnect()
        return await op()

    async def _drain(self) -> None:
        while True:
            try:
                line = await self.transport.read_line(0.2)
            except (asyncio.TimeoutError, TimeoutError):
                return
            log.debug("gantry banner: %s", line)

    async def send(self, line: str) -> None:
        """Write one line and block until FluidNC acknowledges it with `ok`.

        Push messages (`<...>` status, `[...]` feedback) can arrive at any moment and are
        skipped — only `ok` and `error:N` end the wait.
        """
        async def op():
            log.debug("gantry > %s", line)
            await self.transport.write_line(line)
            await self._await_ok(line)

        async with self._lock:
            await self._with_reconnect(repr(line), op)

    async def _await_ok(self, sent: str, timeout: float | None = None) -> None:
        timeout = self.config.ack_timeout_s if timeout is None else timeout
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise GantryError(f"no `ok` for {sent!r} within {timeout}s", "gantry_timeout")
            try:
                reply = await self.transport.read_line(remaining)
            except (asyncio.TimeoutError, TimeoutError):
                raise GantryError(f"no `ok` for {sent!r} within {timeout}s", "gantry_timeout") from None
            log.debug("gantry < %s", reply)
            if reply == "ok":
                return
            if reply.startswith("error:"):
                raise GantryError(f"FluidNC rejected {sent!r}: {reply}", "gcode_rejected")
            if reply.startswith("ALARM:"):
                raise GantryError(f"alarm while running {sent!r}: {reply}", "limit_switch_fault")
            # `<...>` status and `[...]` feedback are unsolicited; keep waiting.

    async def status(self) -> str:
        """One real-time status query. `?` is a real-time command — no newline, no `ok`."""
        async def op():
            await self.transport.write_line("?")
            deadline = asyncio.get_running_loop().time() + self.config.ack_timeout_s
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise GantryError("no status report from FluidNC", "gantry_timeout")
                try:
                    reply = await self.transport.read_line(remaining)
                except (asyncio.TimeoutError, TimeoutError):
                    raise GantryError("no status report from FluidNC", "gantry_timeout") from None
                match = STATUS_RE.match(reply)
                if match:
                    return match.group(1)

        async with self._lock:
            return await self._with_reconnect("status query", op)

    async def wait_idle(self) -> None:
        """Block until the machine has actually finished moving."""
        deadline = asyncio.get_running_loop().time() + self.config.idle_timeout_s
        while True:
            state = await self.status()
            if state == "Idle":
                return
            if state in HALTED_STATES:
                raise GantryError(
                    f"gantry is in {state} and will not complete the move",
                    "limit_switch_fault" if state == "Alarm" else "gantry_fault",
                )
            if asyncio.get_running_loop().time() > deadline:
                raise GantryError(
                    f"gantry still {state} after {self.config.idle_timeout_s}s", "gantry_timeout"
                )
            await asyncio.sleep(self.config.idle_poll_interval_s)

    async def move_and_settle(self, gcode: str) -> None:
        """Send one move and return only once the machine is physically stopped."""
        await self.send(gcode)
        await self.wait_idle()

    async def home(self) -> None:
        """`$H`. Run once per session before the first move — machine coordinates are
        meaningless until then, and every value in `coordinates.json` is relative to home."""
        async with self._lock:
            await self.transport.write_line("$H")
            # FluidNC only answers `ok` once the homing cycle has finished, which is far
            # longer than a normal line ack — a 5s ack timeout would fail every time.
            await self._await_ok("$H", timeout=self.config.home_timeout_s)
        await self.wait_idle()

    async def unlock(self) -> None:
        """`$X`. Clears an alarm. Only after a human has confirmed the board is clear."""
        await self.send("$X")
