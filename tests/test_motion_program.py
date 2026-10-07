"""Compiler tests: the action list -> G-code translation, and its repeatability."""
import chess
import pytest

from backend.state import GameLoop
from motion.config import MotionConfig
from motion.coordinates import CoordinateError, CoordinateTable, GraveyardAllocator
from motion.program import ActuatorCommand, GantryMove, MotionCompileError, compile_actions

TABLE = CoordinateTable.synthetic(square_size_mm=48.0, origin_mm=(24.0, 24.0))


def commands(program):
    return [s.command for s in program.steps if isinstance(s, ActuatorCommand)]


def moves(program):
    return [s for s in program.steps if isinstance(s, GantryMove)]


def test_normal_move_is_pick_then_place():
    program = compile_actions(
        [{"action": "pick_up", "square": "e2"}, {"action": "place", "square": "e4"}], TABLE
    )
    assert commands(program) == ["UP", "DOWN", "MAG_ON", "UP", "DOWN", "MAG_OFF", "UP"]
    assert [m.target for m in moves(program)] == ["e2", "e4"]


def test_gcode_matches_the_coordinate_table():
    program = compile_actions(
        [{"action": "pick_up", "square": "a1"}, {"action": "place", "square": "h8"}], TABLE
    )
    assert program.gcode == (
        "G21",
        "G90",
        "G1 X24.000 Y24.000 F6000",
        "G1 X360.000 Y360.000 F6000",
    )


def test_feed_comes_from_config():
    slow = MotionConfig(travel_feed_mm_min=1200)
    program = compile_actions([{"action": "pick_up", "square": "a1"}, {"action": "place", "square": "a2"}], TABLE, slow)
    assert moves(program)[0].gcode() == "G1 X24.000 Y24.000 F1200"


def test_every_program_starts_by_clearing_the_board():
    """An XY move with the actuator still down rakes the magnet across the pieces."""
    program = compile_actions([{"action": "pick_up", "square": "e2"}, {"action": "place", "square": "e4"}], TABLE)
    assert isinstance(program.steps[0], ActuatorCommand)
    assert program.steps[0].command == "UP"


def test_capture_drops_into_the_graveyard_before_the_move():
    actions = [
        {"action": "pick_up", "square": "e5"},
        {"action": "move_to_graveyard", "color": "black"},
        {"action": "pick_up", "square": "d4"},
        {"action": "place", "square": "e5"},
    ]
    program = compile_actions(actions, TABLE)
    assert commands(program) == [
        "UP",
        "DOWN", "MAG_ON", "UP",      # lift the captured piece
        "DOWN", "DROP", "UP",        # file it in the graveyard
        "DOWN", "MAG_ON", "UP",      # lift the capturing piece
        "DOWN", "MAG_OFF", "UP",     # place it
    ]
    assert program.graveyard_slots == ("black_1",)


def test_graveyard_colour_hint_picks_the_matching_column():
    allocator = GraveyardAllocator(TABLE)
    white = compile_actions(
        [{"action": "pick_up", "square": "e5"}, {"action": "move_to_graveyard", "color": "white"}],
        TABLE, allocator=allocator,
    )
    black = compile_actions(
        [{"action": "pick_up", "square": "d5"}, {"action": "move_to_graveyard", "color": "black"}],
        TABLE, allocator=allocator,
    )
    assert white.graveyard_slots == ("white_1",)
    assert black.graveyard_slots == ("black_1",)


def test_graveyard_slots_never_collide_across_a_game():
    allocator = GraveyardAllocator(TABLE)
    used = []
    for _ in range(12):
        program = compile_actions(
            [{"action": "pick_up", "square": "e5"}, {"action": "move_to_graveyard"}],
            TABLE, allocator=allocator,
        )
        used.extend(program.graveyard_slots)
    assert len(set(used)) == 12


def test_graveyard_slot_order_is_numeric_not_lexical():
    """white_10 must not fill before white_2."""
    assert TABLE.graveyard_slots("white")[:3] == ("white_1", "white_2", "white_3")
    assert TABLE.graveyard_slots("white")[9] == "white_10"


def test_full_graveyard_is_an_error_not_a_collision():
    small = CoordinateTable.synthetic(graveyard_slots_per_side=1)
    allocator = GraveyardAllocator(small)
    for _ in range(2):
        compile_actions(
            [{"action": "pick_up", "square": "e5"}, {"action": "move_to_graveyard"}],
            small, allocator=allocator,
        )
    with pytest.raises(CoordinateError, match="graveyard is full"):
        compile_actions(
            [{"action": "pick_up", "square": "e5"}, {"action": "move_to_graveyard"}],
            small, allocator=allocator,
        )


# -- repeatability ---------------------------------------------------------------------


def test_same_move_compiles_to_the_same_program():
    actions = [{"action": "pick_up", "square": "g1"}, {"action": "place", "square": "f3"}]
    first = compile_actions(actions, TABLE)
    second = compile_actions(actions, TABLE)
    assert first.move_id != second.move_id  # fresh id per move
    assert first.gcode == second.gcode
    assert first.checksum == second.checksum  # and the id must not leak into the hash


def test_different_moves_have_different_checksums():
    a = compile_actions([{"action": "pick_up", "square": "e2"}, {"action": "place", "square": "e4"}], TABLE)
    b = compile_actions([{"action": "pick_up", "square": "d2"}, {"action": "place", "square": "d4"}], TABLE)
    assert a.checksum != b.checksum


def test_listing_interleaves_gantry_and_actuator_in_execution_order():
    program = compile_actions(
        [{"action": "pick_up", "square": "e2"}, {"action": "place", "square": "e4"}], TABLE
    )
    body = [line for line in program.listing().split("\n") if not line.startswith(";")]
    assert body == [
        "G21", "G90",
        ">UP  ; safety: clear the board before any XY move",
        "G1 X216.000 Y72.000 F6000  ; -> e2",
        ">DOWN  ; descend over e2",
        ">MAG_ON  ; grip e2",
        ">UP  ; lift from e2",
        "G1 X216.000 Y168.000 F6000  ; -> e4",
        ">DOWN  ; descend over e4",
        ">MAG_OFF  ; release on e4",
        ">UP  ; retract from e4",
    ]


# -- rejection of impossible action lists ----------------------------------------------


def test_place_without_pick_up_is_rejected():
    with pytest.raises(MotionCompileError, match="nothing held"):
        compile_actions([{"action": "place", "square": "e4"}], TABLE)


def test_two_pick_ups_in_a_row_are_rejected():
    with pytest.raises(MotionCompileError, match="already holding"):
        compile_actions(
            [{"action": "pick_up", "square": "e2"}, {"action": "pick_up", "square": "d2"}], TABLE
        )


def test_program_ending_mid_carry_is_rejected():
    with pytest.raises(MotionCompileError, match="still holding"):
        compile_actions([{"action": "pick_up", "square": "e2"}], TABLE)


def test_promotion_never_reaches_the_gantry():
    with pytest.raises(MotionCompileError, match="pause_for_promotion"):
        compile_actions([{"action": "pause_for_promotion"}], TABLE)


def test_unknown_square_is_caught_before_anything_moves():
    with pytest.raises(CoordinateError, match="unknown square"):
        compile_actions([{"action": "pick_up", "square": "j9"}, {"action": "place", "square": "e4"}], TABLE)


def test_unknown_action_is_rejected():
    with pytest.raises(MotionCompileError, match="unknown action"):
        compile_actions([{"action": "teleport", "square": "e4"}], TABLE)


# -- the backend's own decompositions must all compile ---------------------------------


def _loop(board: chess.Board) -> GameLoop:
    return GameLoop(board, scan_fn=lambda: None, engine=None)


@pytest.mark.parametrize(
    "fen, uci",
    [
        ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "e2e4"),          # quiet
        ("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2", "e4d5"),     # capture
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1g1"),                              # O-O
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1c1"),                              # O-O-O
        ("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1", "e8g8"),                              # black O-O
        ("8/8/8/3pP3/8/8/8/4K2k w - d6 0 2", "e5d6"),                                  # en passant
    ],
)
def test_backend_decompositions_compile(fen, uci):
    board = chess.Board(fen)
    loop = _loop(board)
    actions = loop._actions_for(chess.Move.from_uci(uci))
    program = compile_actions(actions, TABLE)
    assert program.steps  # compiled without raising, which is the assertion that matters
    assert not any(isinstance(s, GantryMove) and s.target.startswith("graveyard") for s in program.steps) or (
        program.graveyard_slots
    )


def test_en_passant_removes_the_pawn_from_its_actual_square():
    board = chess.Board("8/8/8/3pP3/8/8/8/4K2k w - d6 0 2")
    actions = _loop(board)._actions_for(chess.Move.from_uci("e5d6"))
    # The captured pawn is on d5, not on the destination d6.
    assert actions[0] == {"action": "pick_up", "square": "d5"}
    assert actions[1]["action"] == "move_to_graveyard"
    assert actions[1]["color"] == "black"
    program = compile_actions(actions, TABLE)
    assert moves(program)[0].target == "d5"


def test_castling_is_one_program_with_both_pieces():
    board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    actions = _loop(board)._actions_for(chess.Move.from_uci("e1g1"))
    program = compile_actions(actions, TABLE)
    assert [m.target for m in moves(program)] == ["e1", "g1", "h1", "f1"]
