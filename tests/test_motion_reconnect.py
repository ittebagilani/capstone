"""Gantry link repair: a dead WiFi socket is reconnected once; real faults still stop."""
import asyncio

import pytest

from motion.fluidnc import FluidNCLink, GantryError


class DroppingTransport:
    """Behaves like FluidNC, but the connection is dead until reopened `drops` times."""

    def __init__(self, drops=1, reply="ok"):
        self.drops, self.reply = drops, reply
        self.opens, self.written, self._out = 0, [], []

    async def open(self):
        self.opens += 1

    async def close(self):
        pass

    async def write_line(self, line):
        if self.opens <= self.drops:
            raise ConnectionResetError(104, "Connection reset by peer")
        self.written.append(line)
        self._out.append("<Idle|MPos:0,0,0|FS:0,0>" if line == "?" else self.reply)

    async def read_line(self, timeout):
        if not self._out:
            raise asyncio.TimeoutError
        return self._out.pop(0)


@pytest.mark.asyncio
async def test_dead_socket_reconnects_and_resends_once():
    t = DroppingTransport(drops=1)
    link = FluidNCLink(t)
    await link.open()
    await link.send("G1 X195.796 Y75.122 F6000")
    assert t.opens == 2 and t.written == ["G1 X195.796 Y75.122 F6000"]   # sent exactly once on the live link
    assert await link.status() == "Idle"


@pytest.mark.asyncio
async def test_link_still_dead_after_reconnect_raises():
    link = FluidNCLink(DroppingTransport(drops=5))
    await link.open()
    with pytest.raises(ConnectionResetError):
        await link.send("G21")


@pytest.mark.asyncio
async def test_rejection_is_not_retried():
    t = DroppingTransport(drops=0, reply="error:20")
    link = FluidNCLink(t)
    await link.open()
    with pytest.raises(GantryError):
        await link.send("G1 X999")
    assert t.opens == 1 and t.written == ["G1 X999"]
