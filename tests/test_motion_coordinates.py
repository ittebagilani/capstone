"""Coordinate table loading. Every failure here must happen at load, not mid-game."""
import json

import pytest

from motion.coordinates import ALL_SQUARES, CoordinateError, CoordinateTable


def good_table(**overrides) -> dict:
    data = {
        "square_size_mm": 48,
        "coordinates": {sq: [i * 1.0, i * 2.0] for i, sq in enumerate(ALL_SQUARES)},
        "graveyard": {"white_1": [420, 24], "black_1": [468, 24]},
    }
    data.update(overrides)
    return data


def test_loads_a_well_formed_table():
    table = CoordinateTable.from_dict(good_table())
    assert table.square_size_mm == 48
    assert table.square("a1") == (0.0, 0.0)
    assert table.graveyard_slot("white_1") == (420.0, 24.0)


def test_missing_square_is_rejected_at_load():
    data = good_table()
    del data["coordinates"]["g7"]
    with pytest.raises(CoordinateError, match="missing 1 square"):
        CoordinateTable.from_dict(data)


def test_non_square_key_is_rejected():
    data = good_table()
    data["coordinates"]["j9"] = [1, 2]
    with pytest.raises(CoordinateError, match="non-square keys"):
        CoordinateTable.from_dict(data)


def test_non_numeric_coordinate_is_rejected():
    data = good_table()
    data["coordinates"]["a1"] = ["left", "bottom"]
    with pytest.raises(CoordinateError, match="must be numbers"):
        CoordinateTable.from_dict(data)


def test_three_axis_coordinate_is_rejected():
    """Z belongs to the actuator, not the table — a stray Z means the wrong file."""
    data = good_table()
    data["coordinates"]["a1"] = [1, 2, 3]
    with pytest.raises(CoordinateError, match=r"expected \[x, y\]"):
        CoordinateTable.from_dict(data)


def test_missing_graveyard_is_rejected():
    data = good_table()
    data["graveyard"] = {}
    with pytest.raises(CoordinateError, match="graveyard"):
        CoordinateTable.from_dict(data)


def test_bad_graveyard_slot_name_is_rejected():
    data = good_table(graveyard={"bin": [1, 2]})
    with pytest.raises(CoordinateError, match="slot names"):
        CoordinateTable.from_dict(data)


def test_square_size_must_be_present():
    data = good_table()
    del data["square_size_mm"]
    with pytest.raises(CoordinateError, match="square_size_mm"):
        CoordinateTable.from_dict(data)


def test_missing_file_says_who_owns_it(tmp_path):
    with pytest.raises(CoordinateError, match="gantry calibration"):
        CoordinateTable.load(tmp_path / "coordinates.json")


def test_malformed_json_names_the_file(tmp_path):
    path = tmp_path / "coordinates.json"
    path.write_text("{not json")
    with pytest.raises(CoordinateError, match="not valid JSON"):
        CoordinateTable.load(path)


def test_round_trips_through_a_real_file(tmp_path):
    path = tmp_path / "coordinates.json"
    path.write_text(json.dumps(good_table()))
    table = CoordinateTable.load(path)
    assert table.square("h8") == (63.0, 126.0)
    assert table.source == str(path)


def test_synthetic_grid_is_a_regular_board():
    table = CoordinateTable.synthetic(square_size_mm=40, origin_mm=(20, 20))
    assert table.square("a1") == (20.0, 20.0)
    assert table.square("b1") == (60.0, 20.0)
    assert table.square("a2") == (20.0, 60.0)
    assert table.square("h8") == (300.0, 300.0)
