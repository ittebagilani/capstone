"""Actuator link: one command per line, one ACK before the next. No fire-and-forget.

Command set and protocol: INTERFACES.md §4. The actuator ACKs every command with `OK` or
`FAULT:<reason>`. `<reason>` is passed straight through to the `move_complete` envelope and
onto the frontend, so Akil's fault strings become the error strings the operator reads.
"""
import asyncio
import logging

from motion.config import MotionConfig
from motion.transport import LineTransport

log = logging.getLogger("cxra.motion.actuator")

VALID_COMMANDS = frozenset({"DOWN", "MAG_ON", "UP", "MAG_OFF", "DROP"})


class ActuatorFault(RuntimeError):
    """The actuator answered `FAULT:<reason>`, or didn't answer at all."""

    def __init__(self, message: str, code: str = "actuator_fault"):
        super().__init__(message)
        self.code = code


class ActuatorLink:
    def __init__(self, transport: LineTransport, config: MotionConfig | None = None):
        self.transport = transport
        self.config = config or MotionConfig()
        self._lock = asyncio.Lock()
        #: Best-effort mirror of magnet state, for logging and post-fault triage. Not trusted
        #: for control — the actuator is authoritative.
        self.magnet_on = False

    async def open(self) -> None:
        await self.transport.open()

    async def close(self) -> None:
        await self.transport.close()

    async def send(self, command: str) -> None:
        if command not in VALID_COMMANDS:
            raise ActuatorFault(f"unknown actuator command {command!r}", "invalid_command")
        async with self._lock:
            log.debug("actuator > %s", command)
            await self.transport.write_line(command)
            await self._await_ack(command)
        if command == "MAG_ON":
            self.magnet_on = True
        elif command in ("MAG_OFF", "DROP"):
            self.magnet_on = False

    async def _await_ack(self, command: str) -> None:
        deadline = asyncio.get_running_loop().time() + self.config.actuator_timeout_s
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ActuatorFault(f"no ACK for {command} within {self.config.actuator_timeout_s}s", "actuator_timeout")
            try:
                reply = await self.transport.read_line(remaining)
            except (asyncio.TimeoutError, TimeoutError):
                raise ActuatorFault(
                    f"no ACK for {command} within {self.config.actuator_timeout_s}s", "actuator_timeout"
                ) from None
            log.debug("actuator < %s", reply)
            if reply == "OK":
                return
            if reply.startswith("FAULT:"):
                reason = reply.split(":", 1)[1].strip() or "unknown"
                raise ActuatorFault(f"{command} faulted: {reason}", reason)
            # Anything else is the sketch's own debug chatter; ignore and keep waiting.
