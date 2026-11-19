"""Measure the square classifier against a labelled capture session (CLAUDE.md §7.3).

Reports what §7.3 asks for: board-level accuracy (the headline), per-square accuracy, and the
confusion matrix — empty<->black is a shadow problem, white<->black a lighting problem. Both
after the 3-frame vote (what scan() returns) and per single frame (what the vote is rescuing).
Every misread square is listed with its confidence, so the 0.90 retry threshold (§3.5) can be
checked against real data. Runs anywhere — it only reads the saved warped frames.

    PYTHONPATH=. python vision/evaluate.py vision/samples/<session>
"""
import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import chess
import cv2

from vision.classifier import BLACK, EMPTY, WHITE, classify, vote

LABELS = (EMPTY, WHITE, BLACK)


def expected_squares(white: list[str], black: list[str]) -> list[str]:
    out = [EMPTY] * 64
    for name in white:
        out[chess.parse_square(name)] = WHITE
    for name in black:
        out[chess.parse_square(name)] = BLACK
    return out


@dataclass
class Tally:
    boards: int = 0
    boards_ok: int = 0
    confusion: dict = field(default_factory=lambda: {(t, p): 0 for t in LABELS for p in LABELS})

    def add(self, truth: list[str], pred: list[str]) -> None:
        self.boards += 1
        self.boards_ok += truth == pred
        for t, p in zip(truth, pred):
            self.confusion[(t, p)] += 1

    @property
    def square_acc(self) -> float:
        total = sum(self.confusion.values())
        return sum(self.confusion[(l, l)] for l in LABELS) / total if total else 0.0

    @property
    def board_acc(self) -> float:
        return self.boards_ok / self.boards if self.boards else 0.0

    def report(self, title: str) -> str:
        lines = [f"{title}: board-level {self.boards_ok}/{self.boards} = {self.board_acc:.1%}, "
                 f"per-square {self.square_acc:.2%}",
                 "    truth \\ pred " + "".join(f"{p:>8}" for p in LABELS)]
        for t in LABELS:
            lines.append(f"    {t:>13} " + "".join(f"{self.confusion[(t, p)]:>8}" for p in LABELS))
        return "\n".join(lines)


def evaluate(session: Path) -> tuple[Tally, Tally, list[str]]:
    """(voted tally, single-frame tally, misread lines) for one session directory."""
    reference = cv2.imread(str(session / "reference.png"))
    if reference is None:
        raise FileNotFoundError(session / "reference.png")
    voted, single, misreads = Tally(), Tally(), []
    for line in (session / "labels.jsonl").read_text().splitlines():
        shot = json.loads(line)
        truth = expected_squares(shot["white"], shot["black"])
        frames = [classify(cv2.imread(str(session / f)), reference) for f in shot["frames"]]
        for squares, _ in frames:
            single.add(truth, squares)
        squares, confidence = vote(frames)
        voted.add(truth, squares)
        for i, (t, p) in enumerate(zip(truth, squares)):
            if t != p:
                misreads.append(f"  {shot['name']:<12} {chess.square_name(i)}  "
                                f"truth {t:<5}  read {p:<5}  conf {confidence[i]:.2f}")
    return voted, single, misreads


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", type=Path)
    args = ap.parse_args()
    voted, single, misreads = evaluate(args.session)
    print(voted.report("3-frame vote (scan())"))
    print()
    print(single.report("single frame"))
    print("\nmisread squares after vote:" if misreads else "\nno misread squares after vote")
    print("\n".join(misreads))


if __name__ == "__main__":
    main()
