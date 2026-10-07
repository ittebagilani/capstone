"""Protocol and executor tests, against scripted transports — no hardware.

The one behaviour worth protecting above all others here: the gantry must be physically
stopped before the actuator is told to go DOWN (CLAUDE.md §3.4).
"""
import pytest

from motion.actuator import ActuatorFault, ActuatorLink
from motion.config import MotionConfig
from motion.coordinates import CoordinateTable
from motion.executor import MotionExecutor, SimulatedExecutor
from motion.fluidnc import FluidNCLink, GantryError
from motion.transport import FakeTransport

TABLE = CoordinateTable.synthetic()
FAST = MotionConfig(idle_poll_interval_s=0.0, ack_timeout_s=0.5, actuator_timeout_s=0.5, idle_timeout_s=0.5)

E2E4 = [{"action": "pick_up", "square": "e2"}, {"action": "place", "square": "e4"}]


class FakeGantry(FakeTransport):
    """FluidNC that reports `Run` for `busy_polls` status queries after each move, then Idle."""

    def __init__(self, busy_polls: int = 2, alarm: bool = False, reject: bool = False):
        self.busy_polls = busy_polls
        self.alarm = alarm
        self.reject = reject
        self._remaining = 0
        #: Every line written, in order, with status polls marked — used to prove ordering.
        super().__init__(self._respond)

    def _respond(self, line: str) -> list[str]:
        if line == "?":
            if self.alarm:
                return ["<Alarm|MPos:0.000,0.000,0.000>"]
            if self._remaining > 0:
                self._remaining -= 1
                return ["<Run|MPos:1.000,2.000,0.000>"]
            return ["<Idle|MPos:1.000,2.000,0.000>"]
        if self.reject:
            return ["error:20"]
        self._remaining = self.busy_polls
        return ["ok"]


class FakeActuator(FakeTransport):
    def __init__(self, fault_on: str | None = None, reason: str = "limit_switch_fault"):
        self.fault_on = fault_on
        self.reason = reason
        super().__init__(self._respond)

    def _respond(self, line: str) -> list[str]:
        if line == self.fault_on:
            return [f"FAULT:{self.reason}"]
        return ["OK"]


def build(gantry=None, actuator=None):
    gantry = gantry or FakeGantry()
    actuator = actuator or FakeActuator()
    executor = MotionExecutor(TABLE, FluidNCLink(gantry, FAST), ActuatorLink(actuator, FAST), FAST)
    executor.homed = True
    return executor, gantry, actuator


# -- FluidNC handshake ------------------------------------------------------------------


async def test_move_waits_for_idle_not_for_ok():
    link = FluidNCLink(FakeGantry(busy_polls=3), FAST)
    await link.move_and_settle("G1 X10.000 Y10.000 F6000")
    # One move line, then polls until Idle: 3 Run replies plus the Idle one.
    assert link.transport.written == ["G1 X10.000 Y10.000 F6000", "?", "?", "?", "?"]


async def test_rejected_gcode_raises_with_a_short_reason():
    link = FluidNCLink(FakeGantry(reject=True), FAST)
    with pytest.raises(GantryError) as exc:
        await link.send("G1 X10.000 Y10.000 F6000")
    assert exc.value.code == "gcode_rejected"


async def test_alarm_does_not_wait_out_the_idle_timeout():
    link = FluidNCLink(FakeGantry(alarm=True), FAST)
    with pytest.raises(GantryError) as exc:
        await link.move_and_settle("G1 X10.000 Y10.000 F6000")
    assert exc.value.code == "limit_switch_fault"


async def test_status_ignores_unsolicited_chatter():
    transport = FakeTransport(lambda line: ["[MSG:INFO: something]", "<Idle|MPos:0.000,0.000,0.000>"])
    assert await FluidNCLink(transport, FAST).status() == "Idle"


# -- actuator protocol ------------------------------------------------------------------


async def test_actuator_fault_reason_is_passed_through():
    link = ActuatorLink(FakeActuator(fault_on="MAG_ON", reason="pickup_failed"), FAST)
    with pytest.raises(ActuatorFault) as exc:
        await link.send("MAG_ON")
    assert exc.value.code == "pickup_failed"


async def test_actuator_silence_times_out():
    link = ActuatorLink(FakeTransport(lambda line: []), FAST)
    with pytest.raises(ActuatorFault) as exc:
        await link.send("DOWN")
    assert exc.value.code == "actuator_timeout"


async def test_unknown_actuator_command_never_reaches_the_wire():
    transport = FakeActuator()
    with pytest.raises(ActuatorFault):
        await ActuatorLink(transport, FAST).send("SPIN")
    assert transport.written == []


# -- executor ---------------------------------------------------------------------------


async def test_successful_move_returns_an_ok_envelope():
    executor, gantry, actuator = build()
    result = await executor.execute(E2E4, move_id="m1")
    assert result == {"type": "move_complete", "move_id": "m1", "status": "ok", "error": None}
    assert actuator.written == ["UP", "DOWN", "MAG_ON", "UP", "DOWN", "MAG_OFF", "UP"]


async def test_gantry_is_idle_before_the_actuator_is_told_to_descend():
    """The whole reason the Pi polls `?` — DOWN into a moving board wrecks pieces."""
    order = []
    gantry = FakeGantry(busy_polls=2)
    actuator = FakeActuator()
    gantry._responder = _record(gantry._respond, order, "gantry")
    actuator._responder = _record(actuator._respond, order, "actuator")
    executor = MotionExecutor(TABLE, FluidNCLink(gantry, FAST), ActuatorLink(actuator, FAST), FAST)
    executor.homed = True
    await executor.execute(E2E4)

    first_down = order.index(("actuator", "DOWN"))
    move = order.index(("gantry", "G1 X216.000 Y72.000 F6000"))
    polls_between = [i for i, item in enumerate(order) if item == ("gantry", "?") and move < i < first_down]
    assert move < first_down
    assert len(polls_between) == 3  # two Run replies, then Idle


def _record(responder, log, name):
    def wrapped(line):
        log.append((name, line))
        return responder(line)

    return wrapped


async def test_actuator_fault_becomes_an_error_envelope_and_stops_the_program():
    executor, gantry, actuator = build(actuator=FakeActuator(fault_on="MAG_ON", reason="pickup_failed"))
    result = await executor.execute(E2E4, move_id="m2")
    assert result == {
        "type": "move_complete", "move_id": "m2", "status": "error", "error": "pickup_failed",
    }
    # Stopped at the fault: the destination move was never sent.
    assert "G1 X216.000 Y168.000 F6000" not in gantry.written


async def test_bad_action_list_is_refused_without_moving_anything():
    executor, gantry, actuator = build()
    result = await executor.execute([{"action": "place", "square": "e4"}])
    assert result["status"] == "error"
    assert result["error"] == "invalid_actions"
    assert gantry.written == []
    assert actuator.written == []


async def test_simulated_executor_keeps_the_compiled_programs():
    sim = SimulatedExecutor(TABLE)
    result = await sim.execute(E2E4)
    assert result["status"] == "ok"
    assert sim.programs[0].gcode[2:] == ("G1 X216.000 Y72.000 F6000", "G1 X216.000 Y168.000 F6000")


async def test_reset_refills_the_graveyard_from_the_first_slot():
    sim = SimulatedExecutor(TABLE)
    capture = [
        {"action": "pick_up", "square": "e5"},
        {"action": "move_to_graveyard", "color": "white"},
        {"action": "pick_up", "square": "d4"},
        {"action": "place", "square": "e5"},
    ]
    await sim.execute(capture)
    await sim.execute(capture)
    assert [p.graveyard_slots for p in sim.programs] == [("white_1",), ("white_2",)]
    sim.reset()
    await sim.execute(capture)
    assert sim.programs[0].graveyard_slots == ("white_1",)
