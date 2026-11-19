"""vision/evaluate.py on a synthetic capture session written to disk."""
import json

import cv2
import numpy as np
import pytest

from tests.test_classifier import WHITE_PIECE, _render
from vision.evaluate import evaluate


def _write_session(tmp_path, shots):
    cv2.imwrite(str(tmp_path / "reference.png"), _render({}).astype(np.uint8))
    with open(tmp_path / "labels.jsonl", "w") as f:
        for name, pieces, white in shots:
            frames = []
            for k in range(3):
                cv2.imwrite(str(tmp_path / f"{name}_f{k}.png"), _render(pieces).astype(np.uint8))
                frames.append(f"{name}_f{k}.png")
            f.write(json.dumps({"name": name, "frames": frames, "white": white, "black": []}) + "\n")


def test_correct_labels_score_perfect(tmp_path):
    _write_session(tmp_path, [("00_empty", {}, []),
                              ("01_two", {"a1": WHITE_PIECE, "h8": WHITE_PIECE}, ["a1", "h8"])])
    voted, single, misreads = evaluate(tmp_path)
    assert voted.board_acc == 1.0 and voted.square_acc == 1.0
    assert single.boards == 6
    assert voted.confusion[("white", "white")] == 2
    assert misreads == []


def test_wrong_label_counts_against_board_and_lands_in_confusion(tmp_path):
    # Piece physically on e4, labelled as d4: one missed white, one phantom white.
    _write_session(tmp_path, [("02_bad", {"e4": WHITE_PIECE}, ["d4"])])
    voted, _, misreads = evaluate(tmp_path)
    assert voted.board_acc == 0.0
    assert voted.square_acc == 62 / 64
    assert voted.confusion[("white", "empty")] == 1
    assert voted.confusion[("empty", "white")] == 1
    assert len(misreads) == 2


def test_rank_digits_expand():
    pytest.importorskip("picamera2")
    from vision.capture_samples import parse_squares
    assert parse_squares("2 e4") == sorted([f"{f}2" for f in "abcdefgh"] + ["e4"])
    assert parse_squares("e9") is None
