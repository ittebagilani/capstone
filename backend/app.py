"""FastAPI + WebSocket entrypoint. Wire contract: INTERFACES.md §6.

On connect the backend sends one state_update (game_state "idle" until a game starts).

Inbound message types:
  {"type": "new_game", "human_color": "white", "difficulty": 5}  - checks the start position
  {"type": "attempt_move"}                    - human is done moving: scan + infer now
  {"type": "promotion_choice", "piece": "q"}   - resolve a pending promotion (INTERFACES.md §6)
  {"type": "recovery_confirm"}                 - board physically restored, retry the scan
  {"type": "set_difficulty", "level": 7}       - 1..10, takes effect on the next engine move
  {"type": "recalibrate"}                      - idle only, board EMPTY: redo homography + reference
  {"type": "debug_move", "uci": "e2e4"}        - fake vision only: "physically" play a move

`attempt_move` is the provisional "human is done" trigger — HMI button now, a physical
button later; how that's triggered is still open in INTERFACES.md.

Env: CXRA_VISION=fake|real (default fake), CXRA_MOTION (see motion/factory.py),
CXRA_SCAN_LOG (default logs/scans.jsonl). Serves the built HMI from frontend/dist at /.
"""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import chess
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from backend.engine import Engine
from backend.fake_vision import FakeVision
from backend.state import GameLoop, GameState
from motion.executor import MotionExecutor
from motion.factory import build_executor

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT / "frontend" / "dist"
SCAN_LOG = Path(os.environ.get("CXRA_SCAN_LOG", ROOT / "logs" / "scans.jsonl"))

engine = Engine()
#: sim by default; CXRA_MOTION=real drives the gantry. See motion/factory.py.
motion = build_executor()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.start()
    if isinstance(motion, MotionExecutor):
        await motion.open()
    try:
        yield
    finally:
        await engine.stop()
        if isinstance(motion, MotionExecutor):
            await motion.close()


app = FastAPI(lifespan=lifespan)


def make_vision():
    """CXRA_VISION=fake (default) or real — the real camera scan() is the one-line swap of
    CLAUDE.md §3.3. Anything with a .scan() -> dict works; vision.live is a module.

    The fake holds its own board: it models the *physical* board, which only changes when the
    human moves a piece (debug_move) or the robot finishes a move — not when the backend's
    stored position changes."""
    if os.environ.get("CXRA_VISION", "fake").lower() == "real":
        import vision.live  # Pi only: picamera2
        return vision.live
    return FakeVision(chess.Board())


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    vision = make_vision()
    fake = vision if isinstance(vision, FakeVision) else None
    loop: GameLoop

    async def execute(actions):
        result = await motion.execute(actions)
        if fake is not None and (result or {}).get("status") != "error":
            fake.set_board(loop.board.copy())  # the robot moved the "physical" pieces
        return result

    loop = GameLoop(chess.Board(), vision.scan, engine, execute_fn=execute,
                    verify_robot_moves=True, scan_log=SCAN_LOG)
    loop.state = GameState.IDLE
    await websocket.send_json(loop.snapshot("Start a new game"))

    async def stream(updates):
        async for update in updates:
            await websocket.send_json(update)

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")

            if msg_type == "new_game":
                motion.reset()  # graveyard fills from the first slot again
                if fake is not None:
                    fake.set_board(chess.Board())  # pretend the human set the pieces up
                color = chess.BLACK if msg.get("human_color") == "black" else chess.WHITE
                await stream(loop.new_game(color, msg.get("difficulty")))
            elif msg_type == "attempt_move":
                await stream(loop.play_human_turn())
            elif msg_type == "promotion_choice":
                await stream(loop.resolve_promotion(msg["piece"]))
            elif msg_type == "recovery_confirm":
                if fake is not None:
                    fake.set_board(loop.board.copy())  # pretend the human restored it
                await stream(loop.retry_scan())
            elif msg_type == "set_difficulty":
                await loop.set_difficulty(msg["level"])
                await websocket.send_json(loop.snapshot(f"Difficulty set to {loop.difficulty}"))
            elif msg_type == "recalibrate":
                await websocket.send_json(loop.snapshot(await _recalibrate(vision, loop)))
            elif msg_type == "debug_move":
                await stream(_debug_move(fake, loop, msg.get("uci", "")))
    except WebSocketDisconnect:
        pass


async def _recalibrate(vision, loop: GameLoop) -> str:
    if loop.state != GameState.IDLE:
        return "Recalibrate only between games"
    if not hasattr(vision, "recalibrate"):
        return "Fake vision — nothing to calibrate"
    try:
        return await asyncio.to_thread(vision.recalibrate)
    except RuntimeError as e:
        return f"Recalibration failed: {e}"


async def _debug_move(fake: FakeVision | None, loop: GameLoop, uci: str):
    """Stand in for a human moving a piece, so the whole loop runs with no camera or pieces."""
    if fake is None:
        yield loop.snapshot("debug_move needs CXRA_VISION=fake")
        return
    if loop.state != GameState.WAITING_HUMAN:
        yield loop.snapshot("Not your turn right now")
        return
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        move = None
    if move is None or move not in loop.board.legal_moves:
        yield loop.snapshot(f"Not a legal move: {uci!r}")
        return
    after = loop.board.copy()
    after.push(move)
    fake.set_board(after)
    async for update in loop.play_human_turn():
        yield update


if FRONTEND_DIST.exists():
    # Last, so /ws wins. One process serves the HMI and the socket — nothing extra at boot.
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
