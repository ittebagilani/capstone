"""Line-oriented transports. Both controllers speak newline-delimited text, so both links
sit on one small interface — which is also what makes them fakeable in tests.

Nothing here knows about chess, G-code, or the actuator command set.
"""
import asyncio
from typing import Optional, Protocol


class TransportError(IOError):
    """The link is unusable: not open, closed underneath us, or write failed."""


class LineTransport(Protocol):
    """Async newline-delimited byte pipe."""

    async def open(self) -> None: ...

    async def write_line(self, line: str) -> None: ...

    async def read_line(self, timeout: float) -> str:
        """One line, `\\r\\n` stripped. Raises `asyncio.TimeoutError` if none arrives."""
        ...

    async def close(self) -> None: ...


class SerialLineTransport:
    """USB serial, for the actuator ESP32 (INTERFACES.md §4) and for a USB-tethered ESP32.

    pyserial is blocking, so reads and writes go to a thread. One shared lock keeps
    concurrent callers from interleaving half-lines on the wire.
    """

    def __init__(self, port: str, baudrate: int = 115200, *, reset_delay_s: float = 2.0):
        self.port = port
        self.baudrate = baudrate
        # An Arduino or ESP32 dev board reboots when the port opens and ignores everything
        # sent during the bootloader window. Skipping this delay loses the first command of the game.
        self.reset_delay_s = reset_delay_s
        self._serial = None
        self._lock = asyncio.Lock()
        #: Bytes read past the end of the last line. pyserial has no pushback, so the
        #: remainder of a chunk is held here and prepended to the next read.
        self._pending = b""

    async def open(self) -> None:
        try:
            import serial  # lazy: the dev machine has no hardware and needn't install it
        except ImportError:
            raise TransportError("pyserial is not installed — `pip install -r requirements.txt`") from None
        loop = asyncio.get_running_loop()
        self._serial = await loop.run_in_executor(
            None, lambda: serial.Serial(self.port, self.baudrate, timeout=0)
        )
        if self.reset_delay_s:
            await asyncio.sleep(self.reset_delay_s)
        await loop.run_in_executor(None, self._serial.reset_input_buffer)

    async def write_line(self, line: str) -> None:
        if self._serial is None:
            raise TransportError(f"{self.port} is not open")
        async with self._lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._serial.write, (line + "\n").encode())

    async def read_line(self, timeout: float) -> str:
        if self._serial is None:
            raise TransportError(f"{self.port} is not open")
        return await asyncio.wait_for(self._read_line(), timeout)

    async def _read_line(self) -> str:
        buffer = bytearray(self._pending)
        self._pending = b""
        loop = asyncio.get_running_loop()
        while True:
            if b"\n" in buffer:
                line, _, rest = bytes(buffer).partition(b"\n")
                self._pending = rest
                return line.decode(errors="replace").strip()
            chunk = await loop.run_in_executor(None, self._serial.read, 256)
            if not chunk:
                # timeout=0 makes reads non-blocking; yield instead of spinning the CPU.
                await asyncio.sleep(0.005)
                continue
            buffer += chunk

    async def close(self) -> None:
        if self._serial is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._serial.close)
            self._serial = None


class TelnetLineTransport:
    """FluidNC over WiFi. It exposes a raw telnet-style socket on port 23."""

    def __init__(self, host: str, port: int = 23):
        self.host = host
        self.port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def open(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)

    async def write_line(self, line: str) -> None:
        if self._writer is None:
            raise TransportError(f"{self.host}:{self.port} is not open")
        self._writer.write((line + "\n").encode())
        await self._writer.drain()

    async def read_line(self, timeout: float) -> str:
        if self._reader is None:
            raise TransportError(f"{self.host}:{self.port} is not open")
        raw = await asyncio.wait_for(self._reader.readline(), timeout)
        if not raw:
            raise TransportError("connection closed by FluidNC")
        return raw.decode(errors="replace").strip()

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            self._reader = self._writer = None


class FakeTransport:
    """Scripted transport for tests and dry runs.

    `responder(line) -> list[str]` returns the lines the device would emit for that command.
    Every written line is recorded in `.written`, which is how the tests assert on the exact
    G-code that reached the wire.
    """

    def __init__(self, responder):
        self._responder = responder
        self.written: list[str] = []
        self._inbox: list[str] = []
        self.opened = False
        self.closed = False

    async def open(self) -> None:
        self.opened = True

    async def write_line(self, line: str) -> None:
        if self.closed:
            raise TransportError("transport is closed")
        self.written.append(line)
        self._inbox.extend(self._responder(line))

    async def read_line(self, timeout: float) -> str:
        if not self._inbox:
            raise asyncio.TimeoutError(f"no response within {timeout}s")
        return self._inbox.pop(0)

    async def close(self) -> None:
        self.closed = True


class SimActuatorTransport:
    """Stands in for Akil's actuator ESP32: logs each command and answers `OK`.

    `CXRA_ACTUATOR=sim` with `CXRA_MOTION=real` drives the real gantry through every move while
    the magnet commands only go to the log. That's the dry run for coordinates and move
    sequencing before the actuator is attached.
    """

    def __init__(self):
        import logging

        self._log = logging.getLogger("cxra.motion.actuator")
        self._replies: list[str] = []

    async def open(self) -> None:
        self._log.info("actuator: SIMULATED (CXRA_ACTUATOR=sim). Magnet commands are logged, not sent")

    async def write_line(self, line: str) -> None:
        self._log.info("actuator (sim) > %s", line)
        self._replies.append("OK")

    async def read_line(self, timeout: float) -> str:
        if not self._replies:
            raise asyncio.TimeoutError
        return self._replies.pop(0)

    async def close(self) -> None:
        pass
