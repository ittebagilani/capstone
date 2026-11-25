#!/usr/bin/env python3
"""Gantry ESP32 (FluidNC) from the Pi over USB. No laptop, no web UI.

    python scripts/gantry.py ports                         # what's plugged in
    python scripts/gantry.py flash                         # install FluidNC (replaces Alex's sketch)
    python scripts/gantry.py upload                        # push firmware/gantry/config.yaml, reboot
    python scripts/gantry.py send '?' '$X' 'G91 G1 X10 F300' G90
    python scripts/gantry.py send --wait '$H'              # wait until Idle after the last line
    python scripts/gantry.py log                           # reboot and print the boot log
    python scripts/gantry.py term                          # interactive console (FluidTerm)

--port picks the device. Without it, the single connected USB serial device is used. With
both ESP32s plugged in, pass it, e.g. --port /dev/serial/by-id/usb-Silicon_Labs_CP2102...
--host 172.20.10.10 talks to FluidNC over WiFi instead (the Pi must be on the same network).
flash and term are USB-only.
"""
import argparse
import glob
import os
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "firmware" / "gantry" / "config.yaml"
FLUIDNC_VERSION = "v4.1.1"
FLUIDNC_URL = f"https://github.com/bdring/FluidNC/releases/download/{FLUIDNC_VERSION}/fluidnc-{FLUIDNC_VERSION}-posix.zip"
CACHE = Path.home() / ".cache" / "cxra" / f"fluidnc-{FLUIDNC_VERSION}"
BAUD = 115200

#: FluidNC real-time commands: single bytes, no newline, no `ok`.
REALTIME = {"!": b"!", "~": b"~", "reset": b"\x18"}


def find_port(port: str | None) -> str:
    if port:
        return port
    found = sorted(glob.glob("/dev/serial/by-id/*")) or sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    if not found:
        sys.exit("no USB serial device found. Is the gantry ESP32 plugged into the Pi?")
    if len(found) > 1:
        sys.exit("several serial devices; pick one with --port:\n  " + "\n  ".join(found))
    return found[0]


class SocketPort:
    """FluidNC over WiFi (raw telnet, port 23) with the small slice of the pyserial API this
    script uses: read(n) honouring `.timeout`, write(), close()."""

    def __init__(self, host: str, port: int = 23):
        import socket

        self._sock = socket.create_connection((host, port), timeout=5)
        self.timeout = 0.1

    def read(self, size: int = 1) -> bytes:
        import socket

        self._sock.settimeout(self.timeout)
        try:
            return self._sock.recv(size)
        except socket.timeout:
            return b""

    def write(self, data: bytes) -> int:
        self._sock.sendall(data)
        return len(data)

    def close(self) -> None:
        self._sock.close()


def connect(port: str | None = None, host: str | None = None):
    """USB serial, or WiFi when `host` is given (e.g. 172.20.10.10)."""
    if host:
        h, _, p = host.partition(":")
        try:
            s = SocketPort(h, int(p) if p else 23)
        except OSError as exc:
            sys.exit(f"can't reach FluidNC at {h}:{p or 23} ({exc.strerror or exc}). The ESP32 is "
                     f"172.20.10.10 on the hotspot; `hostname -I` shows the Pi's own address, which isn't it.")
        settle(s)
        return s
    return open_serial(find_port(port))


def open_serial(port: str):
    import serial

    import termios

    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, BAUD, 0.1
    # The ESP32's auto-reset circuit reboots it on a DTR/RTS transition. Leave both lines at
    # pyserial's default and clear HUPCL so closing doesn't drop them: then only the first
    # open after power-up resets the board, and homing survives between invocations.
    # (Setting dtr/rts False, the usual advice, resets it on every open here. Tested.)
    s.open()
    attrs = termios.tcgetattr(s.fd)
    attrs[2] &= ~termios.HUPCL
    termios.tcsetattr(s.fd, termios.TCSANOW, attrs)
    settle(s)
    return s


def settle(s, quiet: float = 0.8, limit: float = 8.0) -> None:
    """Swallow output until the line goes quiet. If opening rebooted the board, that waits out
    the boot (~2 s) so the first command isn't lost in the bootloader."""
    start = last = time.monotonic()
    seen = b""
    while time.monotonic() - last < quiet and time.monotonic() - start < limit:
        chunk = s.read(4096)
        if chunk:
            seen += chunk
            last = time.monotonic()
    if b"rst:" in seen or b"[MSG:RST]" in seen:
        print("(ESP32 rebooted on connect: homing, if any, is lost)")


def read_line(s, deadline: float) -> str | None:
    buf = b""
    while time.monotonic() < deadline:
        c = s.read(1)
        if not c:
            continue
        if c == b"\n":
            return buf.decode(errors="replace").rstrip("\r")
        buf += c
    return buf.decode(errors="replace") or None


def drain(s, seconds: float) -> None:
    end = time.monotonic() + seconds
    while (line := read_line(s, end)) is not None:
        print(line)


def status(s, timeout: float = 2.0) -> str | None:
    s.write(b"?")
    end = time.monotonic() + timeout
    while (line := read_line(s, end)) is not None:
        if line.startswith("<"):
            return line
        print(line)
    return None


def send_line(s, line: str, timeout: float) -> bool:
    """Send one command and print replies until `ok` / `error`. Returns False on error."""
    if line in REALTIME:
        s.write(REALTIME[line])
        print(f"> {line} (real-time)")
        drain(s, 0.5)
        return True
    if line == "?":
        print("> ?")
        print(status(s) or "(no status reply)")
        return True
    print(f"> {line}")
    s.write(line.encode() + b"\n")
    end = time.monotonic() + timeout
    while (reply := read_line(s, end)) is not None:
        print(reply)
        if reply == "ok":
            return True
        if reply.startswith(("error", "ALARM")):
            return False
    print(f"(no ok within {timeout:.0f}s)")
    return False


def wait_idle(s, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = status(s)
        if st:
            state = st[1:].split("|", 1)[0]
            if state.startswith("Idle"):
                print(st)
                return True
            if state.startswith(("Alarm", "Door")):
                print(st)
                return False
        time.sleep(0.2)
    print("(still not Idle)")
    return False


def cmd_ports(args) -> int:
    for p in sorted(glob.glob("/dev/serial/by-id/*")):
        print(f"{p} -> {os.path.realpath(p)}")
    for p in sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")):
        print(p)
    return 0


def cmd_send(args) -> int:
    s = connect(args.port, args.host)
    ok = True
    for line in args.lines:
        timeout = args.timeout or (180 if line.upper().startswith("$H") else 10)
        if not send_line(s, line, timeout):
            ok = False
            break
    if ok and args.wait:
        ok = wait_idle(s, 180)
    s.close()
    return 0 if ok else 1


def restart(s) -> None:
    # Some alarms (e.g. after a failed homing) lock out every `$` command, $Bye included,
    # with error:9. A Ctrl-X soft reset clears that lock first; then $Bye does the full reboot.
    s.write(b"\x18")
    time.sleep(1.0)
    settle(s)
    s.write(b"$Bye\n")
    time.sleep(0.5)
    drain(s, 6)


def cmd_log(args) -> int:
    s = connect(args.port, args.host)
    restart(s)
    s.close()
    return 0


def cmd_upload(args) -> int:
    from xmodem import XMODEM

    path = Path(args.file)
    if not path.exists():
        sys.exit(f"{path} not found")
    s = connect(args.port, args.host)
    print(f"uploading {path} as config.yaml")
    s.write(b"$Xmodem/Receive=config.yaml\n")
    time.sleep(0.3)

    def getc(size, timeout=1):
        s.timeout = timeout
        return s.read(size) or None

    def putc(data, timeout=1):
        return s.write(data)

    with open(path, "rb") as f:
        sent = XMODEM(getc, putc).send(f)
    s.timeout = 0.1
    drain(s, 1)
    if not sent:
        print("XModem upload failed")
        return 1
    print("upload ok, restarting. Check the log below for config errors.")
    restart(s)
    s.close()
    return 0


def fetch_fluidnc() -> Path:
    tree = CACHE / f"fluidnc-{FLUIDNC_VERSION}-posix"
    if tree.exists():
        return tree
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / "fluidnc.zip"
    print(f"downloading {FLUIDNC_URL}")
    urllib.request.urlretrieve(FLUIDNC_URL, archive)
    with zipfile.ZipFile(archive) as z:
        z.extractall(CACHE)
    return tree


def cmd_flash(args) -> int:
    port = find_port(args.port)
    if not args.yes:
        reply = input(f"Flash FluidNC {FLUIDNC_VERSION} to {port}? This erases the sketch on that ESP32. [y/N] ")
        if reply.strip().lower() != "y":
            return 1
    tree = fetch_fluidnc()
    py = fluidnc_venv(tree)
    # Same steps as FluidNC's install-wifi.sh + install-fs.sh (tools.sh), minus the
    # interactive FluidTerm it opens at the end, which hangs a non-interactive run.
    esptool = [py, "-m", "esptool", "--chip", "esp32", "--port", port, "--baud", "230400"]
    fuses = tree / "SecurityFuses.bin"
    if subprocess.call(esptool + ["dump-mem", "0x3ff5a018", "4", str(fuses)], cwd=tree):
        return 1
    ok_fuses = {(tree / "common" / n).read_bytes() for n in ("SecurityFusesOK.bin", "SecurityFusesOK0.bin")}
    secure = fuses.read_bytes() not in ok_fuses
    fuses.unlink()
    if secure:
        print("Secure boot is enabled on this ESP32. Flashing FluidNC would probably brick it. Aborted.")
        return 1
    write = esptool + ["--before", "default-reset", "--after", "hard-reset", "write-flash", "-z",
                       "--flash-mode", "dio", "--flash-freq", "80m", "--flash-size", "detect"]
    rc = subprocess.call(write + ["0x1000", "wifi/bootloader.bin", "0xe000", "common/boot_app0.bin",
                                  "0x10000", "wifi/firmware.bin", "0x8000", "wifi/partitions.bin",
                                  "0x3d0000", "wifi/littlefs.bin"], cwd=tree)
    if rc == 0:
        print("flashed. Next: python scripts/gantry.py upload")
    return rc


def fluidnc_venv(tree: Path) -> str:
    """~/.fluidnc_venv with esptool (bundled in the release), as FluidNC's tools.sh makes it."""
    venv = Path.home() / ".fluidnc_venv"
    py = venv / "bin" / "python3"
    if not py.exists():
        subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
        subprocess.check_call([str(py), "-m", "pip", "install", "-q", "xmodem", "pyserial",
                               str(tree / "common" / "esptool-source.zip")])
    return str(py)


def cmd_term(args) -> int:
    tree = fetch_fluidnc()
    venv_py = Path.home() / ".fluidnc_venv" / "bin" / "python3"
    py = str(venv_py) if venv_py.exists() else sys.executable
    print("FluidTerm: Ctrl-U uploads a file, Ctrl-] quits.")
    return subprocess.call([py, "common/fluidterm.py", find_port(args.port), str(BAUD)], cwd=tree)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="serial device (default: the only one connected)")
    p.add_argument("--host", help="FluidNC over WiFi instead of USB, e.g. 172.20.10.10 (send/log/upload)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports", help="list serial devices")
    f = sub.add_parser("flash", help=f"install FluidNC {FLUIDNC_VERSION} (wifi build)")
    f.add_argument("-y", "--yes", action="store_true")
    u = sub.add_parser("upload", help="upload config.yaml and restart")
    u.add_argument("file", nargs="?", default=str(CONFIG))
    sd = sub.add_parser("send", help="send command lines, print replies")
    sd.add_argument("lines", nargs="+", help="G-code / $ commands; also '?', '!', '~', 'reset'")
    sd.add_argument("--wait", action="store_true", help="after the last line, poll ? until Idle")
    sd.add_argument("--timeout", type=float, help="seconds to wait for ok (default 10, 180 for $H)")
    sub.add_parser("log", help="restart FluidNC and print the boot log")
    sub.add_parser("term", help="interactive FluidTerm console")
    args = p.parse_args()
    return {"ports": cmd_ports, "flash": cmd_flash, "upload": cmd_upload, "send": cmd_send,
            "log": cmd_log, "term": cmd_term}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
