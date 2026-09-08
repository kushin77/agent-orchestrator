"""Difficulty scorer tests (issue #17 AC 1: difficulty drives escalation).

The scorer is the deterministic 0-100 input to escalation; these tests pin its
component math (word count, file count, modification scope, historical failure
rate) so escalation thresholds in the chooser are grounded in a stable score.
"""

from __future__ import annotations

import pytest

from complexity import DifficultyScorer


def test_empty_task_scores_zero() -> None:
    score = DifficultyScorer().score(prompt="")
    assert score.score == 0.0


def test_word_count_raises_score() -> None:
    short = DifficultyScorer().score(prompt="short task")
    long_ = DifficultyScorer().score(prompt=" ".join(["word"] * 200))
    assert short.score < long_.score


def test_file_count_raises_score() -> None:
    single = DifficultyScorer().score(prompt="x", files=["a.py"])
    many = DifficultyScorer().score(prompt="x", files=["a.py", "b.py", "c.py", "d.py", "e.py"])
    assert many.file_count_component == 25.0
    assert many.score > single.score


def test_modification_scope_and_repo_size() -> None:
    tiny = DifficultyScorer().score(prompt="x", symbols=["sym"], repo_loc=1_000)
    huge = DifficultyScorer().score(
        prompt="x", symbols=["s1", "s2", "s3"], repo_loc=200_000
    )
    assert huge.modification_scope_component > tiny.modification_scope_component


def test_historical_fail_rate_via_provider() -> None:
    scorer = DifficultyScorer(fail_rate_provider=lambda _: 1.0)
    score = scorer.score(prompt="x", task_class="security-review")
    assert score.historical_fail_component == 25.0
    assert score.score > 0.0


def test_fail_rate_provider_ignored_when_absent() -> None:
    score = DifficultyScorer().score(prompt="x", task_class="security-review")
    assert score.historical_fail_component == 0.0


def test_score_always_within_bounds() -> None:
    scorer = DifficultyScorer(fail_rate_provider=lambda _: 1.0)
    score = scorer.score(
        prompt=" ".join(["word"] * 300),
        files=["f%d.py" % i for i in range(10)],
        symbols=["s%d" % i for i in range(30)],
        repo_loc=500_000,
        task_class="code-author",
    )
    assert 0.0 <= score.score <= 100.0
    assert score.to_dict()["score"] == score.score


def test_non_string_prompt_rejected() -> None:
    with pytest.raises(ValueError):
        DifficultyScorer().score(prompt=123)  # type: ignore[arg-type]
