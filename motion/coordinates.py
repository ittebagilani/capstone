"""The square -> mm lookup, and graveyard slot assignment.

`coordinates.json` is Alex's file (INTERFACES.md §5, CLAUDE.md §4.4), generated during gantry
calibration and checked in at `/firmware/coordinates.json`. This module reads it. It is the
only module in the repo allowed to know a millimetre value, and it never writes the file.

Validation is strict and happens at load, not at use. A table missing `g7` should fail when
the backend starts, not on move 31 of the demo.
"""
import json
import re
from pathlib import Path
from typing import Iterable, Iterator, Optional

FILES = "abcdefgh"
RANKS = "12345678"
#: All 64 names in the a1..h8 row-major order everything else uses (INTERFACES.md §1).
ALL_SQUARES = tuple(f"{f}{r}" for r in RANKS for f in FILES)

DEFAULT_TABLE_PATH = Path(__file__).resolve().parent.parent / "firmware" / "coordinates.json"

_SLOT_RE = re.compile(r"^(white|black)_(\d+)$")


class CoordinateError(ValueError):
    """The coordinate table is missing, malformed, or incomplete."""


def _as_xy(name: str, value) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise CoordinateError(f"{name}: expected [x, y] in mm, got {value!r}")
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        raise CoordinateError(f"{name}: coordinates must be numbers, got {value!r}") from None


class CoordinateTable:
    """Immutable view of `coordinates.json`."""

    def __init__(
        self,
        square_size_mm: float,
        coordinates: dict[str, tuple[float, float]],
        graveyard: dict[str, tuple[float, float]],
        source: Optional[str] = None,
    ):
        self.square_size_mm = square_size_mm
        self._coordinates = coordinates
        self._graveyard = graveyard
        self.source = source or "<memory>"

    # -- construction ----------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict, source: Optional[str] = None) -> "CoordinateTable":
        if not isinstance(data, dict):
            raise CoordinateError("coordinate table must be a JSON object")

        try:
            square_size = float(data["square_size_mm"])
        except KeyError:
            raise CoordinateError("missing 'square_size_mm'") from None
        except (TypeError, ValueError):
            raise CoordinateError(f"'square_size_mm' must be a number, got {data['square_size_mm']!r}") from None

        raw_squares = data.get("coordinates")
        if not isinstance(raw_squares, dict):
            raise CoordinateError("missing 'coordinates' object")

        missing = [sq for sq in ALL_SQUARES if sq not in raw_squares]
        if missing:
            raise CoordinateError(
                f"coordinate table is missing {len(missing)} square(s): {', '.join(missing[:8])}"
                + (" ..." if len(missing) > 8 else "")
            )
        unknown = sorted(set(raw_squares) - set(ALL_SQUARES))
        if unknown:
            raise CoordinateError(f"coordinate table has non-square keys: {', '.join(unknown)}")

        coordinates = {sq: _as_xy(sq, raw_squares[sq]) for sq in ALL_SQUARES}

        raw_graveyard = data.get("graveyard")
        if not isinstance(raw_graveyard, dict) or not raw_graveyard:
            raise CoordinateError("missing or empty 'graveyard' object")
        bad_slots = [k for k in raw_graveyard if not _SLOT_RE.match(k)]
        if bad_slots:
            raise CoordinateError(
                f"graveyard slot names must look like 'white_1'/'black_2', got: {', '.join(sorted(bad_slots))}"
            )
        graveyard = {k: _as_xy(k, v) for k, v in raw_graveyard.items()}

        return cls(square_size, coordinates, graveyard, source=source)

    @classmethod
    def load(cls, path: Optional[Path | str] = None) -> "CoordinateTable":
        path = Path(path) if path is not None else DEFAULT_TABLE_PATH
        if not path.exists():
            raise CoordinateError(
                f"{path} does not exist. It is generated during gantry calibration "
                "(INTERFACES.md §5) and is owned by Alex — do not fabricate one. "
                "For hardware-free work use CoordinateTable.synthetic()."
            )
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise CoordinateError(f"{path} is not valid JSON: {exc}") from None
        return cls.from_dict(data, source=str(path))

    @classmethod
    def synthetic(
        cls,
        square_size_mm: float = 48.0,
        origin_mm: tuple[float, float] = (24.0, 24.0),
        graveyard_x_mm: float = 420.0,
        graveyard_slots_per_side: int = 15,
    ) -> "CoordinateTable":
        """A regular grid for dry runs and tests. **Never for hardware.**

        Real squares are not on a perfect grid — the board is never mounted exactly square to
        the gantry, which is the entire reason calibration produces a measured table. Using
        this against a real machine will drive the magnet into the gaps between squares.
        """
        ox, oy = origin_mm
        coordinates = {
            f"{FILES[f]}{RANKS[r]}": (ox + f * square_size_mm, oy + r * square_size_mm)
            for r in range(8)
            for f in range(8)
        }
        graveyard = {}
        for i in range(graveyard_slots_per_side):
            graveyard[f"white_{i + 1}"] = (graveyard_x_mm, oy + i * square_size_mm)
            graveyard[f"black_{i + 1}"] = (graveyard_x_mm + square_size_mm, oy + i * square_size_mm)
        return cls(square_size_mm, coordinates, graveyard, source="<synthetic>")

    # -- lookup ----------------------------------------------------------------------

    def square(self, name: str) -> tuple[float, float]:
        try:
            return self._coordinates[name]
        except KeyError:
            raise CoordinateError(f"unknown square {name!r}") from None

    def graveyard_slot(self, name: str) -> tuple[float, float]:
        try:
            return self._graveyard[name]
        except KeyError:
            raise CoordinateError(f"unknown graveyard slot {name!r}") from None

    def graveyard_slots(self, color: Optional[str] = None) -> tuple[str, ...]:
        """Slot names in fill order: white_1, white_2, ... then black_1, black_2, ...

        Sorted by the numeric suffix, not lexically — otherwise `white_10` fills before
        `white_2` and the graveyard fills in a visibly silly order on demo day.
        """

        def key(slot: str):
            side, index = _SLOT_RE.match(slot).groups()
            return (0 if side == "white" else 1, int(index))

        slots = sorted(self._graveyard, key=key)
        if color in ("white", "black"):
            slots = [s for s in slots if s.startswith(color + "_")]
        elif color is not None:
            raise CoordinateError(f"color must be 'white', 'black' or None, got {color!r}")
        return tuple(slots)

    def __repr__(self) -> str:
        return (
            f"CoordinateTable(square_size_mm={self.square_size_mm}, "
            f"squares=64, graveyard={len(self._graveyard)}, source={self.source!r})"
        )


class GraveyardAllocator:
    """Hands out the next free graveyard slot. One instance per game.

    The backend emits `move_to_graveyard` with no slot index — slot assignment is the motion
    layer's business (INTERFACES.md §5). Assignment is sequential and therefore deterministic:
    the same sequence of captures always produces the same slots, so a replayed game produces
    identical G-code.

    `color` is an optional hint (see `motion/README.md`). Without it, pieces still land in a
    valid free slot; with it, white pieces land in the white column and black in the black,
    which is what a spectator expects to see.
    """

    def __init__(self, table: CoordinateTable):
        self._table = table
        self._used: list[str] = []

    @property
    def used(self) -> tuple[str, ...]:
        return tuple(self._used)

    def remaining(self, color: Optional[str] = None) -> int:
        return sum(1 for s in self._table.graveyard_slots(color) if s not in self._used)

    def allocate(self, color: Optional[str] = None) -> str:
        """Reserve and return the next free slot name."""
        slot = self._peek(color)
        self._used.append(slot)
        return slot

    def _peek(self, color: Optional[str]) -> Optional[str]:
        used = set(self._used)
        for candidate_color in ([color] if color else []) + [None]:
            for slot in self._table.graveyard_slots(candidate_color):
                if slot not in used:
                    return slot
        raise CoordinateError(
            "graveyard is full — all slots occupied. A standard game needs at most 30 "
            "(15 per side, kings are never captured); check the table has enough slots."
        )

    def restore(self, used: Iterable[str]) -> None:
        """Re-seed after a restart so a resumed game keeps filling where it left off."""
        self._used = list(used)

    def __iter__(self) -> Iterator[str]:
        return iter(self._used)
