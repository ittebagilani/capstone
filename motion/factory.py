"""Build the executor the environment asks for, so `app.py` holds no wiring decisions.

    CXRA_MOTION=sim    (default) compile and log, move nothing — laptop, CI, dry runs
    CXRA_MOTION=real   drive the actual gantry and actuator

Real mode reads:

    CXRA_GANTRY=192.168.1.50        FluidNC over WiFi (telnet, port 23)
    CXRA_GANTRY=/dev/ttyUSB0        FluidNC over USB serial
    CXRA_ACTUATOR=/dev/ttyACM0      Akil's actuator ESP32
    CXRA_COORDINATES=/path.json     defaults to firmware/coordinates.json
"""
import os
from typing import Optional

from motion.actuator import ActuatorLink
from motion.config import MotionConfig
from motion.coordinates import CoordinateTable
from motion.executor import MotionExecutor, SimulatedExecutor
from motion.fluidnc import FluidNCLink
from motion.transport import SerialLineTransport, TelnetLineTransport


def _gantry_transport(target: str):
    """A path is serial, anything else is a hostname or IP."""
    if target.startswith("/dev/") or target.upper().startswith("COM"):
        return SerialLineTransport(target, reset_delay_s=0.0)  # the ESP32 needs no reset wait
    host, _, port = target.partition(":")
    return TelnetLineTransport(host, int(port) if port else 23)


def build_executor(mode: Optional[str] = None, config: Optional[MotionConfig] = None):
    """Return something with `.execute(actions) -> move_complete`."""
    mode = (mode or os.environ.get("CXRA_MOTION", "sim")).lower()
    config = config or MotionConfig()

    if mode == "sim":
        path = os.environ.get("CXRA_COORDINATES")
        # A real table if one has been calibrated, otherwise the synthetic grid. Dry runs
        # should exercise the real numbers the moment they exist.
        try:
            table = CoordinateTable.load(path)
        except Exception:
            table = CoordinateTable.synthetic()
        return SimulatedExecutor(table, config)

    if mode == "real":
        gantry_target = os.environ.get("CXRA_GANTRY")
        actuator_port = os.environ.get("CXRA_ACTUATOR")
        if not gantry_target or not actuator_port:
            raise RuntimeError("CXRA_MOTION=real needs CXRA_GANTRY and CXRA_ACTUATOR set")
        table = CoordinateTable.load(os.environ.get("CXRA_COORDINATES"))
        return MotionExecutor(
            table,
            FluidNCLink(_gantry_transport(gantry_target), config),
            ActuatorLink(SerialLineTransport(actuator_port), config),
            config,
        )

    raise RuntimeError(f"unknown CXRA_MOTION={mode!r} — expected 'sim' or 'real'")
