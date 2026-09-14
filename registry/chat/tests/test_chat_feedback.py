"""The feedback loop: the prompts view's FP/FN vocabulary, one vocabulary only.

Issue #509 acceptance: feedback is captured against ``(module version, model,
tier)`` and projected into the **existing** per-version metrics vocabulary. The
parity test below reads ``registry/prompts/feedback.py`` (a read-only reference)
and asserts every field and metric method it renders exists here under the same
name, so the two cannot drift into two vocabularies.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from registry.chat import feedback, labels
from registry.chat.eval import standins
from registry.chat.feedback import (
    DEFAULT_EVENTS,
    FeedbackError,
    FeedbackEvent,
    LabelMetrics,
    PromptVersionMetrics,
    compute_metrics,
    correction,
    format_report,
    load_events,
    outcome_labels,
    project,
    thumbs_up,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_prompts_feedback() -> Any:
    """Load the prompt library's feedback module by path (read-only reference)."""
    path = REPO_ROOT / "registry" / "prompts" / "feedback.py"
    spec = importlib.util.spec_from_file_location("prompts_feedback_parity", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_feedback_is_keyed_by_module_version_model_and_tier() -> None:
    event = thumbs_up("msg-1", "chat-answer@v1", "deepseek-v4-flash", "flash", "ANSWER", True)
    assert event.group() == ("chat-answer@v1", "deepseek-v4-flash", "flash")
    metrics = compute_metrics(
        [
            event,
            thumbs_up("msg-2", "chat-answer@v1", "deepseek-v4-pro", "pro", "ANSWER", True),
        ]
    )
    assert set(metrics) == {
        ("chat-answer@v1", "deepseek-v4-flash", "flash"),
        ("chat-answer@v1", "deepseek-v4-pro", "pro"),
    }
    assert metrics[("chat-answer@v1", "deepseek-v4-pro", "pro")].events == 1


def test_a_thumbs_down_correction_produces_the_fp_and_fn() -> None:
    event = correction(
        "msg-3",
        "chat-answer@v1",
        "deepseek-v4-flash",
        "flash",
        "ANSWER",
        "NO_DATA",
        True,
        False,
    )
    assert event.false_positives() == frozenset({"ANSWER", "CITED"})
    assert event.false_negatives() == frozenset({"NO_DATA", "UNCITED"})
    assert event.true_positives() == frozenset()
    assert event.accuracy() == 0.0
    metrics = compute_metrics([event])[event.group()]
    assert metrics.total_fp() == 2
    assert metrics.total_fn() == 2
    assert metrics.total_tp() == 0
    assert metrics.by_label["ANSWER"].fp == 1
    assert metrics.by_label["NO_DATA"].fn == 1
    assert metrics.by_label["ANSWER"].precision() == 0.0
    assert metrics.by_label["ANSWER"].recall() == 0.0
    assert metrics.by_label["ANSWER"].f1() == 0.0
    assert metrics.by_label["ANSWER"].support() == 0


def test_a_thumbs_up_scores_nothing_against_the_version() -> None:
    metrics = compute_metrics(
        [thumbs_up("msg-4", "chat-answer@v1", "deepseek-v4-flash", "flash", "ANSWER", True)]
    )[("chat-answer@v1", "deepseek-v4-flash", "flash")]
    assert metrics.total_fp() == 0
    assert metrics.total_fn() == 0
    assert metrics.total_tp() == 2
    assert metrics.unscored == 0


def test_a_thumbs_down_without_a_correction_is_unscored_never_a_clean_pass() -> None:
    event = correction(
        "msg-5", "chat-answer@v1", "deepseek-v4-flash", "flash", "ANSWER", None, True
    )
    assert event.scorable is False
    metrics = compute_metrics([event])[event.group()]
    assert metrics.unscored == 1
    assert metrics.events == 1
    assert metrics.total_fp() == 0
    assert metrics.by_label == {}
    assert "unscored=1" in format_report(compute_metrics([event]), [event])


def test_an_unknown_tier_or_outcome_is_refused() -> None:
    with pytest.raises(FeedbackError):
        thumbs_up("msg-6", "chat-answer@v1", "some-model", "turbo", "ANSWER", True)
    with pytest.raises(FeedbackError):
        outcome_labels("MAYBE", True)


def _vocabulary(cls: Any) -> set:
    """Every public name a metrics view can render from this class."""
    fields = {field.name for field in dataclasses.fields(cls)}
    return fields | {name for name in dir(cls) if not name.startswith("_")}


def test_the_metrics_vocabulary_is_the_prompt_librarys() -> None:
    theirs = _load_prompts_feedback()
    their_label_fields = {field.name for field in dataclasses.fields(theirs.LabelMetrics)}
    our_label_fields = {field.name for field in dataclasses.fields(LabelMetrics)}
    assert their_label_fields <= our_label_fields, sorted(
        their_label_fields - our_label_fields
    )
    missing = _vocabulary(theirs.PromptVersionMetrics) - _vocabulary(PromptVersionMetrics)
    assert not missing, sorted(missing)
    their_event_methods = {
        name for name in dir(theirs.Outcome) if not name.startswith("_")
    }
    assert {"true_positives", "false_positives", "false_negatives", "accuracy"} <= (
        their_event_methods
    )
    for name in their_event_methods:
        assert hasattr(FeedbackEvent, name), name


def test_the_projection_carries_the_prompts_view_keys() -> None:
    events = load_events(DEFAULT_EVENTS)
    payload = project(compute_metrics(events))
    assert payload["schema"] == "chat-feedback/v1"
    assert payload["rows"], "the seed produced no rows"
    for row in payload["rows"]:
        assert {
            "prompt_id",
            "model",
            "tier",
            "events",
            "total_tp",
            "total_fp",
            "total_fn",
            "macro_precision",
            "macro_recall",
            "by_label",
        } <= set(row)
        for label_row in row["by_label"].values():
            assert {"tp", "fp", "fn", "precision", "recall", "f1", "support"} <= set(
                label_row
            )


def test_the_seed_report_is_deterministic_and_names_the_triples() -> None:
    events = load_events(DEFAULT_EVENTS)
    first = format_report(compute_metrics(events), events)
    second = format_report(compute_metrics(events), events)
    assert first == second
    assert "promptId=chat-answer@v1  model=deepseek-v4-flash  tier=flash" in first
    assert "promptId=chat-refuse@v1" in first
    metrics = compute_metrics(events)
    assert metrics[("chat-answer@v1", "deepseek-v4-flash", "flash")].total_fp() == 4
    assert metrics[("chat-answer@v1", "deepseek-v4-pro", "pro")].unscored == 1


def test_the_feedback_cli_reports_and_projects(capsys: pytest.CaptureFixture) -> None:
    assert feedback.main(["report"]) == 0
    report = capsys.readouterr().out
    assert "total FP=" in report
    assert "promptId=chat-answer@v1" in report
    assert feedback.main(["project"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "chat-feedback/v1"
    assert payload["rows"], "the projection of the seed is empty"


def test_a_feedback_file_that_is_not_a_capture_document_is_refused(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.yaml"
    path.write_text("schema: something-else/v1\nevents: []\n", encoding="utf-8")
    with pytest.raises(FeedbackError):
        load_events(path)


def test_the_label_vocabulary_is_the_one_the_fixtures_declare() -> None:
    assert labels.OUTCOMES == ("ANSWER", "NO_DATA", "REFUSAL", "BLOCK", "FLAG")
    assert set(labels.DECISION_LEVEL) == set(labels.OUTCOMES)
    assert set(labels.DECISION_LEVEL.values()) <= {"block", "warn", "log"}
    assert "CITED" in labels.GROUNDING and "UNCITED" in labels.GROUNDING
    assert set(standins.DOCUMENT_OUTCOME) == set(labels.OUTCOMES) - {"ANSWER"}
    assert set(standins.REASON_CODES) == {
        "NO_DATA_NO_SOURCE",
        "REFUSAL_CROSS_TENANT",
        "BLOCK_INBOUND_CREDENTIAL",
        "FLAG_POISONED_SOURCE",
    }
    payload: Dict[str, Any] = json.loads(json.dumps(project(compute_metrics([]))))
    assert payload["rows"] == []
