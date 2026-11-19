"""Capture labelled board photos with the Pi Camera Module, for building the occupancy
classifier. Run ON THE PI with a monitor attached, using system Python (picamera2 is an
apt package, not in the venv):

    sudo apt install -y python3-picamera2
    python3 vision/capture_samples.py

A preview window opens — frame the board, then press Enter in the terminal. Focus, exposure
and white balance are locked after that, so every shot in the session matches (auto
exposure re-metering between shots would teach the classifier the camera's moods, not the
board). For each shot you type a name and the occupied squares; everything unlisted is
empty. Writes vision/samples/<name>.jpg and appends to vision/samples/labels.jsonl.
"""
import json
import re
import time
from pathlib import Path

from picamera2 import Picamera2, Preview

OUT = Path(__file__).parent / "samples"
SQUARE = re.compile(r"^[a-h][1-8]$")


def ask_squares(colour: str) -> list[str]:
    while True:
        squares = input(f"  {colour} squares (space-separated, blank for none): ").lower().split()
        bad = [s for s in squares if not SQUARE.match(s)]
        if not bad:
            return squares
        print(f"  not squares: {bad}, try again")


def main():
    OUT.mkdir(exist_ok=True)
    cam = Picamera2()
    cam.configure(cam.create_still_configuration())
    cam.start_preview(Preview.QTGL)
    cam.start()

    input("Frame the whole board in the preview, then press Enter to lock the camera...")
    if "AfMode" in cam.camera_controls:  # Camera Module 3 autofocus; v2 is fixed-focus
        cam.autofocus_cycle()
    time.sleep(1)
    md = cam.capture_metadata()
    cam.set_controls({"AfMode": 0} if "AfMode" in cam.camera_controls else {})
    cam.set_controls({"AeEnable": False, "AwbEnable": False,
                      "ExposureTime": md["ExposureTime"], "AnalogueGain": md["AnalogueGain"],
                      "ColourGains": md["ColourGains"]})
    time.sleep(0.5)  # let the locked values take effect before the first shot
    print(f"Locked: exposure {md['ExposureTime']}us, gain {md['AnalogueGain']:.2f}. "
          f"Don't move the camera or change the lighting from here.")

    try:
        while True:
            name = input("\nshot name (e.g. 02_light), or q to quit: ").strip()
            if name == "q":
                break
            if not name:
                continue
            white, black = ask_squares("white"), ask_squares("black")
            if set(white) & set(black):
                print(f"  {sorted(set(white) & set(black))} listed as both colours, shot skipped")
                continue
            path = OUT / f"{name}.jpg"
            cam.capture_file(str(path))
            with open(OUT / "labels.jsonl", "a") as f:
                f.write(json.dumps({"file": path.name, "white": white, "black": black}) + "\n")
            print(f"  saved {path.name} ({len(white)} white, {len(black)} black)")
    finally:
        cam.stop()


if __name__ == "__main__":
    main()
