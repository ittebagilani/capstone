"""Game state machine — one turn per INTERFACES.md §6/§7.

GameState values are the wire contract frontend switches on (INTERFACES.md §6) — don't
rename without updating INTERFACES.md and the frontend together.

`scan_fn` and `engine`/`execute_fn` are injected so this stays testable with zero hardware
(CLAUDE.md §3.3): swap FakeVision and a stub executor for the real ones at the call site.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional

import chess

from backend.inference import board_to_squares, infer_move

CONFIDENCE_THRESHOLD = 0.90  # provisional — tune after CNN training (INTERFACES.md §7)
MAX_SCAN_ATTEMPTS = 3
SCAN_RETRY_DELAY_S = 0.3

PROMOTION_PIECES = {"q": chess.QUEEN, "r": chess.ROOK, "b": chess.BISHOP, "n": chess.KNIGHT}

log = logging.getLogger("cxra.backend")


class GameState(str, Enum):
    IDLE = "idle"
    WAITING_HUMAN = "waiting_human"
    SCANNING = "scanning"
    THINKING = "thinking"
    EXECUTING = "executing"
    PAUSE_FOR_PROMOTION = "pause_for_promotion"
    ERROR = "error"
    RECOVERY = "recovery"


async def _simulate_execute(actions: list[dict]) -> dict:
    """Default motion executor: ack immediately, move nothing.

    Kept as the zero-dependency default for unit tests. For anything resembling a real run
    use `motion.SimulatedExecutor().execute`, which compiles the actions into real G-code and
    so catches decomposition bugs; `motion.MotionExecutor` is the same signature on hardware.
    """
    return {"type": "move_complete", "status": "ok", "error": None}


class GameLoop:
    def __init__(
        self,
        board: chess.Board,
        scan_fn: Callable[[], dict],
        engine,
        execute_fn: Callable[[list[dict]], Awaitable[dict]] = _simulate_execute,
        difficulty: int = 5,
        human_color: Optional[chess.Color] = None,
        verify_robot_moves: bool = False,
        scan_log: Optional[Path] = None,
    ):
        """`verify_robot_moves`: re-scan after every gantry move and route a mismatch to
        RECOVERY. Off by default only because unit-test fakes don't model the robot moving
        pieces; app.py turns it on. `scan_log`: JSONL file every scan attempt is appended to."""
        self.board = board
        self.scan_fn = scan_fn
        self.engine = engine
        self.execute_fn = execute_fn
        self.difficulty = difficulty
        self.human_color = board.turn if human_color is None else human_color
        self.verify_robot_moves = verify_robot_moves
        self.scan_log = scan_log
        self.state = GameState.WAITING_HUMAN
        self.prev_squares = board_to_squares(board)
        self._pending_matches: Optional[list[chess.Move]] = None

    def _update(self, message: str) -> dict:
        return {
            "type": "state_update",
            "board_fen": self.board.fen(),
            "game_state": self.state.value,
            "message": message,
            "difficulty": self.difficulty,
            "human_color": "white" if self.human_color == chess.WHITE else "black",
        }

    def snapshot(self, message: str) -> dict:
        """Current state as a state_update, without changing anything — for a new connection."""
        return self._update(message)

    def _log(self, record: dict) -> None:
        """Append one JSON line. Every scan attempt is logged pass or fail, with all 64
        confidences (INTERFACES.md §7) — the debugging trail and the report's accuracy data."""
        record = {"time": datetime.now(timezone.utc).isoformat(), "fen": self.board.fen(), **record}
        log.debug("%s", record)
        if self.scan_log is not None:
            self.scan_log.parent.mkdir(parents=True, exist_ok=True)
            with self.scan_log.open("a") as f:
                f.write(json.dumps(record) + "\n")

    def _color_at(self, square: int) -> str:
        """Colour of the piece standing on `square` right now, for the graveyard hint.

        Optional field on `move_to_graveyard` (INTERFACES.md §3): the motion layer needs it
        to file a captured piece in the matching graveyard column, and it is the only side
        that cannot work it out — the backend is the one holding the position.
        """
        piece = self.board.piece_at(square)
        return "white" if piece.color else "black"

    def _actions_for(self, move: chess.Move) -> list[dict]:
        """Decompose one chess move into gantry/actuator steps (INTERFACES.md §3)."""
        from_sq, to_sq = chess.square_name(move.from_square), chess.square_name(move.to_square)

        if self.board.is_castling(move):
            rank = chess.square_rank(move.to_square)
            kingside = self.board.is_kingside_castling(move)
            rook_from = chess.square(7 if kingside else 0, rank)
            rook_to = chess.square(5 if kingside else 3, rank)
            return [
                {"action": "pick_up", "square": from_sq},
                {"action": "place", "square": to_sq},
                {"action": "pick_up", "square": chess.square_name(rook_from)},
                {"action": "place", "square": chess.square_name(rook_to)},
            ]

        if self.board.is_en_passant(move):
            captured = chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square))
            return [
                {"action": "pick_up", "square": chess.square_name(captured)},
                {"action": "move_to_graveyard", "color": self._color_at(captured)},
                {"action": "pick_up", "square": from_sq},
                {"action": "place", "square": to_sq},
            ]

        if self.board.is_capture(move):
            return [
                {"action": "pick_up", "square": to_sq},
                {"action": "move_to_graveyard", "color": self._color_at(move.to_square)},
                {"action": "pick_up", "square": from_sq},
                {"action": "place", "square": to_sq},
            ]

        return [{"action": "pick_up", "square": from_sq}, {"action": "place", "square": to_sq}]

    async def _scan_with_retry(self, purpose: str) -> dict:
        """`purpose` (human_move / verify_robot / sync) only labels the log line."""
        best = None
        for attempt in range(MAX_SCAN_ATTEMPTS):
            # A real scan is ~200ms of camera I/O; off the event loop so the socket stays live.
            scan = await asyncio.to_thread(self.scan_fn)
            passed = min(scan["confidence"]) >= CONFIDENCE_THRESHOLD
            self._log({"event": "scan", "purpose": purpose, "attempt": attempt + 1,
                       "passed_threshold": passed, "scan_id": scan.get("scan_id"),
                       "squares": scan["squares"], "confidence": scan["confidence"]})
            if passed:
                return scan
            if best is None or sum(scan["confidence"]) > sum(best["confidence"]):
                best = scan
            if attempt < MAX_SCAN_ATTEMPTS - 1:
                await asyncio.sleep(SCAN_RETRY_DELAY_S)
        return best

    async def play_human_turn(self) -> AsyncIterator[dict]:
        """WAITING_HUMAN -> SCANNING -> THINKING | PAUSE_FOR_PROMOTION | ERROR -> RECOVERY."""
        if self.state != GameState.WAITING_HUMAN or self.board.turn != self.human_color:
            yield self._update("Not your turn right now")
            return

        self.state = GameState.SCANNING
        yield self._update("Reading board...")

        scan = await self._scan_with_retry("human_move")
        move_or_matches, status = infer_move(self.prev_squares, scan["squares"], self.board)
        self._log({"event": "inference", "scan_id": scan.get("scan_id"), "status": status,
                   "move": move_or_matches.uci() if status == "ok" else None})

        if status == "no_change":
            self.state = GameState.WAITING_HUMAN
            yield self._update("Your turn")
            return

        if status in ("no_match", "ambiguous"):
            self.state = GameState.ERROR
            yield self._update("Board doesn't match any legal move")
            self.state = GameState.RECOVERY
            yield self._update("Restore the board to the last known position and confirm")
            return

        if status == "promotion_ambiguous":
            self._pending_matches = move_or_matches
            self.state = GameState.PAUSE_FOR_PROMOTION
            yield self._update("Choose promotion piece")
            return

        self.board.push(move_or_matches)
        self.prev_squares = board_to_squares(self.board)
        async for update in self._engine_turn():
            yield update

    async def resolve_promotion(self, piece: str) -> AsyncIterator[dict]:
        """Client sent {"type": "promotion_choice", "piece": "q"} (INTERFACES.md §6)."""
        if not self._pending_matches:
            raise RuntimeError("resolve_promotion called with no pending promotion")
        wanted = PROMOTION_PIECES[piece]
        move = next(m for m in self._pending_matches if m.promotion == wanted)
        self._pending_matches = None
        self.board.push(move)
        self.prev_squares = board_to_squares(self.board)
        async for update in self._engine_turn():
            yield update

    async def new_game(self, human_color: chess.Color = chess.WHITE,
                       difficulty: Optional[int] = None) -> AsyncIterator[dict]:
        """IDLE (or anything) -> check the physical start position -> human's turn, or the
        robot's first move when the human plays black."""
        self.board.reset()  # in place: FakeVision/app may hold a reference to this board
        self.prev_squares = board_to_squares(self.board)
        self.human_color = human_color
        self._pending_matches = None
        if difficulty is not None:
            await self.set_difficulty(difficulty)
        async for update in self._sync("Set up the starting position, then confirm"):
            yield update

    async def set_difficulty(self, level: int) -> None:
        self.difficulty = max(1, min(10, int(level)))
        if hasattr(self.engine, "configure_difficulty"):
            await self.engine.configure_difficulty(self.difficulty)

    async def retry_scan(self) -> AsyncIterator[dict]:
        """Human confirmed the board has been physically restored after RECOVERY."""
        if self.board.turn == self.human_color:
            # The human may have restored the board *and* made their move — accept either.
            self.state = GameState.WAITING_HUMAN
            async for update in self.play_human_turn():
                yield update
        else:
            async for update in self._sync("Board still doesn't match — fix it, then confirm"):
                yield update

    async def _sync(self, mismatch_message: str) -> AsyncIterator[dict]:
        """Physical board must equal the stored position exactly; then whoever is to move
        moves. Used at game start and after a recovery when it's the robot's turn."""
        self.state = GameState.SCANNING
        yield self._update("Checking the board...")
        scan = await self._scan_with_retry("sync")
        wrong = self._mismatched_squares(scan)
        if wrong:
            self.state = GameState.RECOVERY
            yield self._update(f"{mismatch_message} (check {', '.join(wrong)})")
            return
        if self.board.turn == self.human_color:
            self.state = GameState.WAITING_HUMAN
            yield self._update("Your turn")
        else:
            async for update in self._engine_turn():
                yield update

    def _mismatched_squares(self, scan: dict) -> list[str]:
        return [chess.square_name(i) for i, (want, saw)
                in enumerate(zip(self.prev_squares, scan["squares"])) if want != saw]

    def _game_over_update(self) -> dict:
        self.state = GameState.IDLE
        outcome = self.board.outcome()
        return self._update(f"Game over: {outcome.termination.name} ({outcome.result()})")

    async def _engine_turn(self) -> AsyncIterator[dict]:
        self.state = GameState.THINKING
        yield self._update("Thinking...")

        if self.board.is_game_over():
            yield self._game_over_update()
            return

        move = await self.engine.play(self.board)
        actions = self._actions_for(move)
        self.board.push(move)
        self.prev_squares = board_to_squares(self.board)

        self.state = GameState.EXECUTING
        yield self._update("Hands off the board")
        result = await self.execute_fn(actions) or {}

        if result.get("status") == "error":
            # The gantry stopped mid-move, so the physical board no longer matches the stored
            # position. Stored state stays authoritative (CLAUDE.md §3.1) and RECOVERY shows
            # it to the human to restore — same path as a failed scan.
            self.state = GameState.ERROR
            yield self._update(f"Move failed: {result.get('error') or 'unknown'}")
            self.state = GameState.RECOVERY
            yield self._update("Restore the board to the displayed position and confirm")
            return

        if self.verify_robot_moves:
            # A dropped or misplaced piece would otherwise surface a turn later as a baffling
            # "illegal move" against the human. Same RECOVERY path as a gantry fault.
            self.state = GameState.SCANNING
            yield self._update("Checking the robot's move...")
            wrong = self._mismatched_squares(await self._scan_with_retry("verify_robot"))
            if wrong:
                self.state = GameState.ERROR
                yield self._update(f"Robot move didn't land as expected: {', '.join(wrong)}")
                self.state = GameState.RECOVERY
                yield self._update("Fix the board to match the displayed position, then confirm")
                return

        if self.board.is_game_over():
            yield self._game_over_update()
            return

        self.state = GameState.WAITING_HUMAN
        yield self._update("Your turn")
