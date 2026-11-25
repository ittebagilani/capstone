"""CXRA_ACTUATOR=sim: real-gantry dry runs before Akil's actuator is attached."""
import pytest

from motion.actuator import ActuatorLink
from motion.factory import build_executor
from motion.transport import SimActuatorTransport


@pytest.mark.asyncio
async def test_sim_actuator_acks_every_command():
    link = ActuatorLink(SimActuatorTransport())
    await link.open()
    for cmd in ("UP", "DOWN", "MAG_ON", "UP", "MAG_OFF", "DROP"):
        await link.send(cmd)
    assert link.magnet_on is False


def test_factory_selects_sim_actuator(monkeypatch):
    monkeypatch.setenv("CXRA_GANTRY", "/dev/null")
    monkeypatch.setenv("CXRA_ACTUATOR", "sim")
    ex = build_executor("real")
    assert isinstance(ex.actuator.transport, SimActuatorTransport)
