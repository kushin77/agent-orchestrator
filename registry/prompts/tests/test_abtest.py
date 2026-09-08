"""Tests for the A/B variant runner (issue #13, acceptance criterion 4).

Verifies the runner aggregates per-variant FP/FN/accuracy, selects the best
variant, matches the recorded seed run, and that the offline demo is
deterministic.
"""

from __future__ import annotations

from pathlib import Path

import abtest
from abtest import EvalCase, Variant

SEED_RUNS = Path(__file__).resolve().parents[1] / "seed" / "ab-test-runs.yaml"


def test_select_best_picks_higher_accuracy() -> None:
    events = [
        abtest.Outcome("bad@v1", "1", frozenset({"billing"}), frozenset({"support"})),
        abtest.Outcome("bad@v1", "2", frozenset({"support"}), frozenset({"support"})),
        abtest.Outcome("good@v1", "1", frozenset({"support"}), frozenset({"support"})),
        abtest.Outcome("good@v1", "2", frozenset({"billing"}), frozenset({"billing"})),
    ]
    metrics = abtest.compute_metrics(events)
    best_id, best_accuracy = abtest.select_best(metrics, events)
    assert best_id == "good@v1"
    assert best_accuracy == 1.0


def test_demo_is_deterministic_and_selects_candidate(capsys) -> None:
    exit_code = abtest.main(["demo"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "winner: candidate-classify-route-v2" in captured.out
    assert "acc=1.000" in captured.out
    assert "acc=0.714" in captured.out


def test_recorded_run_aggregates_and_matches_demo(capsys) -> None:
    runs = abtest.load_recorded_runs(SEED_RUNS)
    run = runs["ab-classify-route-v1-vs-v2"]
    metrics, events = abtest.metrics_for_recorded_run(run)
    best_id, best_accuracy = abtest.select_best(metrics, events)
    assert best_id == "candidate-classify-route-v2"
    assert best_accuracy == 1.0
    assert metrics["baseline-classify-route-v1"].total_fp() == 2


def test_ab_experiment_run_scores_all_pairs() -> None:
    variants = [
        Variant("a", "route refunds to support", "v1"),
        Variant("b", "route refunds to billing", "v2"),
    ]
    cases = [
        EvalCase("1", "please refund my invoice", frozenset({"billing"})),
        EvalCase("2", "how do I onboard", frozenset({"support"})),
    ]
    experiment = abtest.AbExperiment("run-x", "classify-route", variants, cases)

    def scorer(prompt_text: str, input_text: str):
        if "refund" in input_text.lower():
            return {"billing"} if "refunds to billing" in prompt_text else {"support"}
        return {"support"}

    metrics = experiment.run(scorer)
    events = [
        abtest.Outcome(v.variant_id, c.case_id, scorer(v.prompt, c.input), c.expected)
        for v in variants
        for c in cases
    ]
    best_id, _ = abtest.select_best(metrics, events)
    assert best_id == "b"
