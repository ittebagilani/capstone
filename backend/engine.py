"""Stockfish wrapper — async API, opened once at app startup (CLAUDE.md §6.4).

Never spawn a process per move: SimpleEngine blocks, which would freeze the WebSocket for
the whole think time inside FastAPI's event loop.
"""
import shutil

import chess
import chess.engine

# /usr/games/stockfish (CLAUDE.md's snippet) is a Debian/Pi path; resolve from PATH instead
# so this also runs on a dev machine (Homebrew installs to /opt/homebrew/bin).
STOCKFISH_PATH = shutil.which("stockfish") or "/usr/games/stockfish"


class Engine:
    def __init__(self):
        self.transport = None
        self.engine: chess.engine.UciProtocol | None = None

    async def start(self) -> None:
        self.transport, self.engine = await chess.engine.popen_uci(STOCKFISH_PATH)

    async def stop(self) -> None:
        if self.engine is not None:
            await self.engine.quit()

    async def configure_difficulty(self, level: int) -> None:
        """level: 1..10 from frontend. Skill Level alone doesn't give a beginner mode —
        Skill Level 0 still plays ~1300-1400 Elo. Elo-limit the low end instead."""
        if level <= 6:
            elo = 1320 + (level - 1) * 180
            floor = self.engine.options["UCI_Elo"].min
            await self.engine.configure({"UCI_LimitStrength": True, "UCI_Elo": max(elo, floor)})
        else:
            await self.engine.configure({"UCI_LimitStrength": False, "Skill Level": (level - 6) * 5})

    async def play(self, board: chess.Board, think_time: float = 0.5) -> chess.Move:
        result = await self.engine.play(board, chess.engine.Limit(time=think_time))
        return result.move
