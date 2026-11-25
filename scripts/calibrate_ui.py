#!/usr/bin/env python3
"""Corner calibration page for the gantry. Jog the magnet onto each corner square, mark it,
save -> firmware/coordinates.json.

    python scripts/calibrate_ui.py [--port /dev/serial/by-id/... | --host 172.20.10.10] [--http-port 8001]

Then open http://<pi-ip>:8001 on a phone or laptop on the same network.
Talks to FluidNC over USB, so stop the backend (and any `gantry.py term`) first: two programs
on one serial port garble each other.

Board orientation (Alex's frame): H8 is nearest home, +X runs toward the a-file, +Y toward
rank 1. The jog buttons are labelled in those board terms.
"""
import argparse
import json
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import gantry
from motion.calibration import apply_graveyard_marks, build_table, clamp_graveyard, warnings_for
from motion.coordinates import DEFAULT_TABLE_PATH, CoordinateTable

ROOT = Path(__file__).resolve().parent.parent
MARKS_FILE = ROOT / "logs" / "calibration_marks.json"  # survives a restart mid-calibration
CORNERS = ("h8", "a8", "a1", "h1")
FEED = 600  # mm/min, gentle: a human is watching the magnet
#: Soft-limit ranges from firmware/gantry/config.yaml (mpos_mm .. mpos_mm + max_travel_mm).
SOFT_X, SOFT_Y = (5.0, 455.0), (10.0, 330.0)

app = FastAPI()
lock = threading.Lock()
ser = None
marks: dict[str, list[float]] = {}


def load_marks() -> None:
    global marks
    try:
        marks = json.loads(MARKS_FILE.read_text())
    except (OSError, ValueError):
        marks = {}


def save_marks() -> None:
    MARKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MARKS_FILE.write_text(json.dumps(marks, indent=2))


def parse_status(line: str | None) -> dict:
    if not line:
        return {"state": "no reply", "x": None, "y": None, "pn": ""}
    fields = line.strip("<>").split("|")
    out = {"state": fields[0], "x": None, "y": None, "pn": ""}
    for f in fields[1:]:
        if f.startswith("MPos:"):
            x, y, *_ = f[5:].split(",")
            out["x"], out["y"] = float(x), float(y)
        elif f.startswith("Pn:"):
            out["pn"] = f[3:]
    return out


def current() -> dict:
    return parse_status(gantry.status(ser))


def run(*lines: str, timeout: float = 15) -> dict:
    """Send lines, wait for Idle, return status. Raises 409 with FluidNC's reason on failure."""
    with lock:
        for line in lines:
            if not gantry.send_line(ser, line, timeout):
                st = current()
                raise HTTPException(409, f"FluidNC refused '{line}' (state {st['state']}). Home first if it says Alarm.")
        gantry.wait_idle(ser, 180)
        return current()


def table_point(square: str) -> list[float] | None:
    try:
        table = CoordinateTable.load()
    except Exception:
        return None
    for lookup in (table.square, table.graveyard_slot):  # "e4" or "white_3"
        try:
            return list(lookup(square))
        except Exception:
            pass
    return None


class Jog(BaseModel):
    dx: float = 0
    dy: float = 0


class Goto(BaseModel):
    square: str


@app.get("/api/status")
def api_status():
    if not lock.acquire(timeout=0.3):
        return {"state": "busy", "marks": marks}
    try:
        st = current()
    finally:
        lock.release()
    st["marks"] = marks
    st["targets"] = {c: marks.get(c) or table_point(c) for c in CORNERS}
    return st


@app.post("/api/home")
def api_home():
    return run("$H", timeout=180)


@app.post("/api/jog")
def api_jog(j: Jog):
    if abs(j.dx) > 50 or abs(j.dy) > 50:
        raise HTTPException(400, "jog step too large")
    return run(f"G91 G1 X{j.dx:.3f} Y{j.dy:.3f} F{FEED}", "G90")


@app.post("/api/goto")
def api_goto(g: Goto):
    sq = g.square.lower().strip()
    target = marks.get(sq) if sq in CORNERS else marks.get("slots", {}).get(sq)
    target = target or table_point(sq)
    if not target:
        raise HTTPException(404, f"no position for {sq!r}: no mark and no coordinates.json entry")
    return run(f"G90 G1 X{target[0]:.3f} Y{target[1]:.3f} F{FEED}")


@app.post("/api/mark/{corner}")
def api_mark(corner: str):
    corner = corner.lower()
    if corner not in CORNERS:
        raise HTTPException(400, "corner must be h8, a8, a1 or h1")
    with lock:
        st = current()
    if not st["state"].startswith("Idle") or st["x"] is None:
        raise HTTPException(409, f"can only mark when Idle and homed (state {st['state']})")
    marks[corner] = [round(st["x"], 3), round(st["y"], 3)]
    save_marks()
    return {"marks": marks}


SLOT_RE = re.compile(r"^(white|black)_([1-9]|1[0-6])$")


@app.post("/api/mark_slot/{slot}")
def api_mark_slot(slot: str):
    slot = slot.lower()
    if not SLOT_RE.match(slot):
        raise HTTPException(400, "slot must be white_1..white_16 or black_1..black_16")
    with lock:
        st = current()
    if not st["state"].startswith("Idle") or st["x"] is None:
        raise HTTPException(409, f"can only mark when Idle and homed (state {st['state']})")
    marks.setdefault("slots", {})[slot] = [round(st["x"], 3), round(st["y"], 3)]
    save_marks()
    return {"marks": marks}


@app.post("/api/clear_slots")
def api_clear_slots():
    marks["slots"] = {}
    save_marks()
    return {"marks": marks}


@app.post("/api/save")
def api_save():
    missing = [c for c in CORNERS if c not in marks]
    if missing:
        raise HTTPException(400, f"mark {', '.join(missing)} first")
    # The printed board's split graveyard: 2x8 white beyond the far file, 2x8 black on the
    # home side. Slots past the soft limits (gantry config) are pulled inside them.
    data = build_table({c: tuple(marks[c]) for c in CORNERS}, graveyard_columns=2, graveyard_side="both",
                       method="bilinear from four corner centres, marked in calibrate_ui",
                       note="split graveyard: 2x8 white beyond the far file, 2x8 black on the home side")
    try:
        offsets = apply_graveyard_marks(data, {s: tuple(v) for s, v in marks.get("slots", {}).items()})
        moved = clamp_graveyard(data, SOFT_X, SOFT_Y)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    DEFAULT_TABLE_PATH.write_text(json.dumps(data, indent=2) + "\n")
    c = data["coordinates"]
    notes = warnings_for(data) + ([f"{len(moved)} graveyard slots pulled inside travel"] if moved else [])
    notes += [f"{c} graveyard shifted {dx:+.1f}, {dy:+.1f} mm" for c, (dx, dy) in offsets.items()]
    return {"written": str(DEFAULT_TABLE_PATH), "square_size_mm": data["square_size_mm"],
            "e4": c["e4"], "d5": c["d5"], "warnings": notes}


@app.post("/api/stop")
def api_stop():
    # Real-time bytes bypass the lock on purpose: STOP must work mid-move.
    ser.write(b"!")
    ser.write(b"\x18")
    return {"stopped": True, "note": "reset sent; FluidNC is now unhomed. Home again."}


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gantry Calibration</title>
<style>
:root{--bg:#f6f6f4;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#ddd;--acc:#2f6fdb;--ok:#1f8a4c;--bad:#c23b2b}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#20201f;--ink:#ececea;--mute:#9a9a95;--line:#353533;--acc:#6b9bff;--ok:#4cc27e;--bad:#ff6b5b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 system-ui,sans-serif}
main{max-width:560px;margin:0 auto;padding:16px}
h1{font-size:18px;margin:0 0 12px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:12px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button{font:inherit;padding:10px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer}
button.pri{background:var(--acc);border-color:var(--acc);color:#fff}button.stop{background:var(--bad);border-color:var(--bad);color:#fff;font-weight:600}
button:disabled{opacity:.4}
.pos{font:600 22px ui-monospace,monospace}.mute{color:var(--mute);font-size:13px}
.pad{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}.pad button{padding:14px 4px}
table{width:100%;border-collapse:collapse}td{padding:6px 4px;border-top:1px solid var(--line)}td.num{font-family:ui-monospace,monospace}
.ok{color:var(--ok)}.bad{color:var(--bad)}select,input{font:inherit;padding:8px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
#msg{min-height:1.4em}
</style></head><body><main>
<h1>Gantry corner calibration</h1>
<div class="card"><div class="row" style="justify-content:space-between">
 <div><div class="pos" id="pos">-</div><div class="mute" id="state">connecting...</div></div>
 <div class="row"><button onclick="act('/api/home')">Home</button><button class="stop" onclick="act('/api/stop')">STOP</button></div>
</div><div id="msg" class="mute"></div></div>

<div class="card"><b>1. Go to a corner</b>
 <div class="row" style="margin-top:8px" id="gotos"></div></div>

<div class="card"><b>2. Nudge until the magnet is centred</b>
 <div class="row" style="margin-top:8px"><span class="mute">Step</span>
  <select id="step"><option>0.2</option><option>0.5</option><option selected>1</option><option>2</option><option>5</option><option>10</option></select><span class="mute">mm</span></div>
 <div class="pad">
  <span></span><button onclick="jog(0,-1)">-Y<br><span class="mute">toward rank 8</span></button><span></span>
  <button onclick="jog(-1,0)">-X<br><span class="mute">toward h-file</span></button><span></span>
  <button onclick="jog(1,0)">+X<br><span class="mute">toward a-file</span></button>
  <span></span><button onclick="jog(0,1)">+Y<br><span class="mute">toward rank 1</span></button><span></span>
 </div></div>

<div class="card"><b>3. Mark it</b>
 <table id="marks"></table>
 <div class="row" style="margin-top:10px"><button class="pri" id="save" onclick="save()">Save coordinates.json</button></div>
 <div id="saved" class="mute" style="margin-top:8px"></div></div>

<div class="card"><b>Graveyard slots</b>
 <div class="mute">Pick a slot, Go, nudge with the pad above until centred, Mark. Each side's whole strip shifts to match;
 one mark per side is enough. Use the inner column (white_1-8, black_1-8): the outer black column is past the X limit.</div>
 <div class="row" style="margin-top:8px"><select id="slot"></select>
  <button onclick="act('/api/goto',{square:document.getElementById('slot').value})">Go</button>
  <button class="pri" onclick="markSlot()">Mark slot</button>
  <button onclick="clearSlots()">Clear marks</button></div>
 <table id="slotmarks" style="margin-top:8px"></table>
 <div class="mute">Then press Save coordinates.json above.</div></div>

<div class="card"><b>4. Check any square</b>
 <div class="row" style="margin-top:8px"><input id="sq" value="e4" size="4"><button onclick="act('/api/goto',{square:document.getElementById('sq').value})">Go</button></div>
 <div class="mute">Uses the saved coordinates.json. The magnet should land centred.</div></div>
</main><script>
const C=["h8","a8","a1","h1"];let busy=false;
document.getElementById('gotos').innerHTML=C.map(c=>`<button onclick="act('/api/goto',{square:'${c}'})">${c.toUpperCase()}</button>`).join('');
function msg(t,bad){const m=document.getElementById('msg');m.textContent=t;m.className=bad?'bad':'mute'}
async function act(url,body){if(busy)return;busy=true;msg('moving...');
 try{const r=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body||{})});
  const j=await r.json();if(!r.ok)msg(j.detail||'error',true);else msg(j.note||'done');return j}
 catch(e){msg(String(e),true)}finally{busy=false;poll()}}
function jog(sx,sy){const s=parseFloat(document.getElementById('step').value);act('/api/jog',{dx:sx*s,dy:sy*s})}
async function mark(c){const j=await act('/api/mark/'+c);if(j)render(j.marks)}
const S=[...Array(16)].flatMap((_,i)=>[`white_${i+1}`,`black_${i+1}`]).sort((a,b)=>a[0]===b[0]?parseInt(a.split('_')[1])-parseInt(b.split('_')[1]):a<b?1:-1);
document.getElementById('slot').innerHTML=S.map(s=>`<option>${s}</option>`).join('');
async function markSlot(){const s=document.getElementById('slot').value;const j=await act('/api/mark_slot/'+s);if(j)render(j.marks)}
async function clearSlots(){const j=await act('/api/clear_slots');if(j)render(j.marks)}
async function save(){const j=await act('/api/save');if(!j||!j.written)return;
 document.getElementById('saved').innerHTML=`Saved. Square size ${j.square_size_mm} mm. e4 = ${j.e4.join(', ')}. `+
 (j.warnings.length?`<span class="bad">${j.warnings.join('; ')}</span>`:'<span class="ok">No warnings.</span>')}
let lastMarks={},targets={};
function renderSlots(m){const sl=(m&&m.slots)||{};const k=Object.keys(sl);
 document.getElementById('slotmarks').innerHTML=k.length?k.map(s=>`<tr><td><b>${s}</b></td><td class="num ok">${sl[s][0].toFixed(2)}, ${sl[s][1].toFixed(2)}</td></tr>`).join(''):'<tr><td class="mute">no slot marks yet</td></tr>'}
function render(m){renderSlots(m);lastMarks=m||{};document.getElementById('marks').innerHTML=C.map(c=>{const v=lastMarks[c],t=targets[c];
 return `<tr><td><b>${c.toUpperCase()}</b></td><td class="num">${v?`<span class="ok">${v[0].toFixed(2)}, ${v[1].toFixed(2)}</span>`:`<span class="mute">${t?'was '+t[0].toFixed(1)+', '+t[1].toFixed(1):'not marked'}</span>`}</td>
 <td style="text-align:right"><button onclick="mark('${c}')">Mark here</button></td></tr>`}).join('');
 document.getElementById('save').disabled=!C.every(c=>lastMarks[c])}
async function poll(){try{const s=await (await fetch('/api/status')).json();
 if(s.targets)targets=s.targets;
 if(s.x!=null)document.getElementById('pos').textContent=`X ${s.x.toFixed(2)}  Y ${s.y.toFixed(2)}`;
 const st=document.getElementById('state');st.textContent=s.state+(s.pn?'  | switch: '+s.pn:'')+(s.state.startsWith('Alarm')?'  - press Home':'');
 st.className=s.state.startsWith('Alarm')?'bad':'mute';render(s.marks)}catch(e){}}
poll();setInterval(()=>{if(!busy)poll()},1500);
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def page():
    return PAGE


def main() -> int:
    global ser
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="gantry serial device (default: the only one connected)")
    p.add_argument("--host", help="FluidNC over WiFi instead of USB, e.g. 172.20.10.10")
    p.add_argument("--http-port", type=int, default=8001)
    args = p.parse_args()
    ser = gantry.connect(args.port, args.host)
    load_marks()
    print(f"open http://<pi-ip>:{args.http_port}  (Ctrl-C to quit)")
    uvicorn.run(app, host="0.0.0.0", port=args.http_port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
