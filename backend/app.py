"""FastAPI + WebSocket entrypoint. Wire contract: INTERFACES.md §6.

Inbound message types:
  {"type": "attempt_move"}                    - ask the backend to scan+infer now
  {"type": "promotion_choice", "piece": "q"}   - resolve a pending promotion (INTERFACES.md §6)
  {"type": "recovery_confirm"}                 - board physically restored, retry the scan

`attempt_move`/`recovery_confirm` aren't in INTERFACES.md yet — pull-based scanning needs
*some* trigger for "human is done moving" and the doc doesn't decide one (sensor? poll
loop? HMI button?). Treat these as provisional until that's resolved there.
"""
import os
from contextlib import asynccontextmanager

import chess
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from backend.engine import Engine
from backend.fake_vision import FakeVision
from backend.state import GameLoop
from motion.executor import MotionExecutor
from motion.factory import build_executor

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


def make_vision(board: chess.Board):
    """CXRA_VISION=fake (default) or real — the real camera scan() is the one-line swap of
    CLAUDE.md §3.3. Anything with a .scan() -> dict works; vision.live is a module."""
    if os.environ.get("CXRA_VISION", "fake").lower() == "real":
        import vision.live  # Pi only: picamera2
        return vision.live
    return FakeVision(board)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    board = chess.Board()
    vision = make_vision(board)
    motion.reset()  # new connection is a new game — graveyard fills from the first slot
    loop = GameLoop(board, vision.scan, engine, execute_fn=motion.execute)

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")

            if msg_type == "attempt_move":
                async for update in loop.play_human_turn():
                    await websocket.send_json(update)
            elif msg_type == "promotion_choice":
                async for update in loop.resolve_promotion(msg["piece"]):
                    await websocket.send_json(update)
            elif msg_type == "recovery_confirm":
                async for update in loop.retry_scan():
                    await websocket.send_json(update)
    except WebSocketDisconnect:
        pass
