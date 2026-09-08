"""Tests for the feedback loop (issue #13, acceptance criterion 3).

Verifies per-prompt-version FP/FN metric computation on hand-built events and
on the committed seed data, and that the CLI report surfaces the metrics.
"""

from __future__ import annotations

from pathlib import Path

import feedback
from feedback import Outcome, compute_metrics

SEED_EVENTS = Path(__file__).resolve().parents[1] / "seed" / "feedback-events.yaml"


def _events() -> list:
    return [
        Outcome("p@v1", "a", frozenset({"billing"}), frozenset({"billing"})),
        Outcome("p@v1", "b", frozenset({"billing"}), frozenset({"support"})),  # FP billing, FN support
        Outcome("p@v1", "c", frozenset({"support"}), frozenset({"support"})),
        Outcome("p@v1", "d", frozenset({"support"}), frozenset({"billing"})),  # FP support, FN billing
    ]


def test_per_label_fp_fn_math() -> None:
    events = _events()
    metrics = compute_metrics(events)
    m = metrics["p@v1"]
    assert m.events == 4
    assert m.by_label["billing"].tp == 1
    assert m.by_label["billing"].fp == 1
    assert m.by_label["billing"].fn == 1
    assert m.by_label["support"].tp == 1
    assert m.by_label["support"].fp == 1
    assert m.by_label["support"].fn == 1
    assert m.total_fp() == 2
    assert m.total_fn() == 2
    assert m.by_label["billing"].precision() == 0.5
    assert m.by_label["billing"].recall() == 0.5
    assert abs(m.overall_accuracy(events) - 0.5) < 1e-9  # 2/4 events fully correct


def test_perfect_prompt_version_has_zero_fp_fn() -> None:
    events = [
        Outcome("p@v1", str(i), frozenset({"x"}), frozenset({"x"})) for i in range(3)
    ]
    m = compute_metrics(events)["p@v1"]
    assert m.total_fp() == 0
    assert m.total_fn() == 0
    assert m.total_tp() == 3


def test_seed_data_shows_v2_improvement() -> None:
    events = feedback.load_events(SEED_EVENTS)
    metrics = compute_metrics(events)
    v1 = metrics["classify-route@v1"]
    v2 = metrics["classify-route@v2"]
    review = metrics["code-review-verdict@v1"]
    assert (v1.total_fp(), v1.total_fn()) == (3, 3)
    assert (v2.total_fp(), v2.total_fn()) == (1, 1)  # disambiguation cut FP/FN
    assert (review.total_fp(), review.total_fn()) == (2, 2)
    assert v2.total_fp() < v1.total_fp()
    assert v2.total_fn() < v1.total_fn()


def test_cli_report_prints_and_exits_zero(capsys) -> None:
    exit_code = feedback.main(["report", "--data", str(SEED_EVENTS)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "prompt feedback report" in captured.out
    assert "classify-route@v1" in captured.out
    assert "classify-route@v2" in captured.out
    assert "total FP=" in captured.out
