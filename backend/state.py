"""Game state machine — one turn per INTERFACES.md §6/§7.

GameState values are the wire contract frontend switches on (INTERFACES.md §6) — don't
rename without updating INTERFACES.md and the frontend together.

`scan_fn` and `engine`/`execute_fn` are injected so this stays testable with zero hardware
(CLAUDE.md §3.3): swap FakeVision and a stub executor for the real ones at the call site.
"""
import asyncio
from enum import Enum
from typing import AsyncIterator, Awaitable, Callable, Optional

import chess

from backend.inference import board_to_squares, infer_move

CONFIDENCE_THRESHOLD = 0.90  # provisional — tune after CNN training (INTERFACES.md §7)
MAX_SCAN_ATTEMPTS = 3
SCAN_RETRY_DELAY_S = 0.3

PROMOTION_PIECES = {"q": chess.QUEEN, "r": chess.ROOK, "b": chess.BISHOP, "n": chess.KNIGHT}


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
    ):
        self.board = board
        self.scan_fn = scan_fn
        self.engine = engine
        self.execute_fn = execute_fn
        self.difficulty = difficulty
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
        }

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

    async def _scan_with_retry(self) -> dict:
        best = None
        for attempt in range(MAX_SCAN_ATTEMPTS):
            scan = self.scan_fn()
            if min(scan["confidence"]) >= CONFIDENCE_THRESHOLD:
                return scan
            if best is None or sum(scan["confidence"]) > sum(best["confidence"]):
                best = scan
            if attempt < MAX_SCAN_ATTEMPTS - 1:
                await asyncio.sleep(SCAN_RETRY_DELAY_S)
        return best

    async def play_human_turn(self) -> AsyncIterator[dict]:
        """WAITING_HUMAN -> SCANNING -> THINKING | PAUSE_FOR_PROMOTION | ERROR -> RECOVERY."""
        self.state = GameState.SCANNING
        yield self._update("Reading board...")

        scan = await self._scan_with_retry()
        move_or_matches, status = infer_move(self.prev_squares, scan["squares"], self.board)

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

    async def retry_scan(self) -> AsyncIterator[dict]:
        """Human confirmed the board has been physically restored after RECOVERY."""
        self.state = GameState.WAITING_HUMAN
        async for update in self.play_human_turn():
            yield update

    async def _engine_turn(self) -> AsyncIterator[dict]:
        self.state = GameState.THINKING
        yield self._update("Thinking...")

        if self.board.is_game_over():
            self.state = GameState.WAITING_HUMAN
            yield self._update(f"Game over: {self.board.outcome().termination.name}")
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

        if self.board.is_game_over():
            self.state = GameState.WAITING_HUMAN
            yield self._update(f"Game over: {self.board.outcome().termination.name}")
            return

        self.state = GameState.WAITING_HUMAN
        yield self._update("Your turn")
