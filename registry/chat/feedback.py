"""Feedback loop — real chat turns become per-version FP/FN evidence (#509).

---knowledge---
module_id: registry.chat.feedback
system: registry
app: chat
solution_class: pattern
patterns: [derived-view, fp-fn-metrics, one-vocabulary-no-second]
derives_from: registry/prompts/feedback.py
owner_sme: qa-sme
tier: L1
interfaces: [FeedbackEvent, LabelMetrics, PromptVersionMetrics, load_events, compute_metrics, project, format_report, main]
invariants: "the FP/FN vocabulary is the prompts view's existing one; this module declares no second"
gotchas: ""
related: ["#509"]
do_not_duplicate: null
---knowledge---

A user's thumbs-up or thumbs-down (with an optional correction) is an *outcome
label* on one specific ``(prompt module version, model, tier)`` — the triple the
prompts view already aggregates on. The vocabulary is the existing one: the
per-label ``tp`` / ``fp`` / ``fn`` confusion counts and the ``precision`` /
``recall`` / ``f1`` / ``support`` they derive, plus per-version ``total_fp`` /
``total_fn`` / ``total_tp``, ``macro_precision`` / ``macro_recall`` and
``overall_accuracy`` — field-for-field the vocabulary
``registry/prompts/feedback.py`` renders. There is no second metrics vocabulary
here, and ``test_chat_feedback.py`` asserts the prompts library's field and
method names are all present rather than trusting the prose.

The labels scored are the turn's declared labels
(:mod:`registry.chat.labels`): the outcome it resolved to (``ANSWER`` /
``NO_DATA`` / ``REFUSAL`` / ``BLOCK`` / ``FLAG``) and whether it carried
citations (``CITED`` / ``UNCITED``). A thumbs-up agrees with the turn, so
predicted and truth coincide and nothing is scored against the version. A
thumbs-down **with** a correction declares the truth, and the difference is the
FP/FN that drives the next prompt-module version.

A thumbs-down **without** a correction is not scored — it says the turn was
wrong without saying what was right — so the event is counted as ``unscored``
and reported, never averaged in as if it were a clean pass (AO-GR-19).

``scripts/check-chat-eval.sh`` cross-checks the tier vocabulary against its home
(``governance/finops/policy.json``) so a rename there fails a gate rather than
splitting the vocabulary in two.

Usage (from the repo root):

    python3 -m registry.chat.feedback report
    python3 -m registry.chat.feedback project --data registry/chat/seed/feedback-events.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

import yaml

from . import labels

PKG_DIR = Path(__file__).resolve().parent
DEFAULT_EVENTS = PKG_DIR / "seed" / "feedback-events.yaml"

#: ``(prompt module version, model, tier)`` — the aggregate key.
GroupKey = Tuple[str, str, str]

#: The verdicts a captured feedback event may carry.
VERDICTS = ("thumbs-up", "thumbs-down")

#: The events file's schema marker.
EVENTS_SCHEMA = "chat-feedback-events/v1"


class FeedbackError(ValueError):
    """Raised when a feedback event cannot be captured or scored honestly."""


def outcome_labels(outcome: str, cited: bool) -> FrozenSet[str]:
    """The label set one turn resolves to: its outcome plus its grounding label."""
    if outcome not in labels.OUTCOMES:
        raise FeedbackError(
            f"outcome {outcome!r} is not in the declared vocabulary {list(labels.OUTCOMES)}"
        )
    return frozenset({outcome, "CITED" if cited else "UNCITED"})


def _require_tier(tier: str) -> str:
    if tier not in labels.TIERS:
        raise FeedbackError(
            f"tier {tier!r} is not in the FinOps tier vocabulary {list(labels.TIERS)}"
        )
    return tier


@dataclass(frozen=True)
class FeedbackEvent:
    """One captured feedback event, keyed to the triple that produced the turn."""

    message_id: str
    prompt_id: str
    model: str
    tier: str
    verdict: str
    predicted: FrozenSet[str]
    truth: FrozenSet[str]

    def group(self) -> GroupKey:
        return (self.prompt_id, self.model, self.tier)

    @property
    def scorable(self) -> bool:
        """True when the feedback declares a truth to score the turn against."""
        return bool(self.truth)

    def true_positives(self) -> FrozenSet[str]:
        return self.predicted & self.truth

    def false_positives(self) -> FrozenSet[str]:
        return self.predicted - self.truth

    def false_negatives(self) -> FrozenSet[str]:
        return self.truth - self.predicted

    def accuracy(self) -> float:
        """Jaccard agreement: ``|TP| / |TP u FP u FN|`` (1.0 when all empty)."""
        union = (
            len(self.true_positives())
            + len(self.false_positives())
            + len(self.false_negatives())
        )
        if union == 0:
            return 1.0
        return len(self.true_positives()) / union


def thumbs_up(
    message_id: str, prompt_id: str, model: str, tier: str, outcome: str, cited: bool
) -> FeedbackEvent:
    """A thumbs-up: the turn was right, so predicted and truth coincide."""
    reference = outcome_labels(outcome, cited)
    return FeedbackEvent(
        message_id=message_id,
        prompt_id=prompt_id,
        model=model,
        tier=_require_tier(tier),
        verdict="thumbs-up",
        predicted=reference,
        truth=reference,
    )


def correction(
    message_id: str,
    prompt_id: str,
    model: str,
    tier: str,
    outcome: str,
    corrected_outcome: Optional[str],
    cited: bool,
    corrected_cited: bool = False,
) -> FeedbackEvent:
    """A thumbs-down with an optional correction.

    Without ``corrected_outcome`` the event is captured but not scorable: the
    user said the turn was wrong and did not say what was right, and guessing a
    truth would fabricate the very metric this loop exists to report.
    """
    truth = (
        frozenset()
        if corrected_outcome is None
        else outcome_labels(corrected_outcome, corrected_cited)
    )
    return FeedbackEvent(
        message_id=message_id,
        prompt_id=prompt_id,
        model=model,
        tier=_require_tier(tier),
        verdict="thumbs-down",
        predicted=outcome_labels(outcome, cited),
        truth=truth,
    )


@dataclass
class LabelMetrics:
    """Per-label confusion counts and derived scores for one prompt version."""

    label: str
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def precision(self) -> float:
        denominator = self.tp + self.fp
        return self.tp / denominator if denominator else 0.0

    def recall(self) -> float:
        denominator = self.tp + self.fn
        return self.tp / denominator if denominator else 0.0

    def f1(self) -> float:
        precision = self.precision()
        recall = self.recall()
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    def support(self) -> int:
        return self.tp + self.fn


@dataclass
class PromptVersionMetrics:
    """The prompts view's per-version metrics, aggregated per model+tier."""

    prompt_id: str
    model: str = ""
    tier: str = ""
    events: int = 0
    unscored: int = 0
    by_label: Dict[str, LabelMetrics] = field(default_factory=dict)

    @property
    def group(self) -> GroupKey:
        return (self.prompt_id, self.model, self.tier)

    def total_fp(self) -> int:
        return sum(entry.fp for entry in self.by_label.values())

    def total_fn(self) -> int:
        return sum(entry.fn for entry in self.by_label.values())

    def total_tp(self) -> int:
        return sum(entry.tp for entry in self.by_label.values())

    def macro_precision(self) -> float:
        values = [entry.precision() for entry in self.by_label.values()]
        return sum(values) / len(values) if values else 0.0

    def macro_recall(self) -> float:
        values = [entry.recall() for entry in self.by_label.values()]
        return sum(values) / len(values) if values else 0.0

    def overall_accuracy(self, events: List[FeedbackEvent]) -> float:
        scored = [
            event
            for event in events
            if event.scorable and event.group() == self.group
        ]
        if not scored:
            return 0.0
        return sum(event.accuracy() for event in scored) / len(scored)


def load_events(path: Path) -> List[FeedbackEvent]:
    """Read captured feedback events from a YAML file."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise FeedbackError(f"cannot read feedback events {path}: {exc}") from exc
    if not isinstance(data, Mapping) or data.get("schema") != EVENTS_SCHEMA:
        raise FeedbackError(f"{path} is not a {EVENTS_SCHEMA} document")
    events: List[FeedbackEvent] = []
    for index, raw in enumerate(data.get("events") or []):
        if not isinstance(raw, Mapping):
            raise FeedbackError(f"event {index} is not a mapping")
        verdict = str(raw.get("verdict", ""))
        if verdict not in VERDICTS:
            raise FeedbackError(
                f"event {index} carries verdict {verdict!r}, not one of {list(VERDICTS)}"
            )
        common = dict(
            message_id=str(raw.get("messageId", "")),
            prompt_id=str(raw.get("promptId", "")),
            model=str(raw.get("model", "")),
            tier=str(raw.get("tier", "")),
            outcome=str(raw.get("outcome", "")),
            cited=bool(raw.get("cited", False)),
        )
        if not common["message_id"]:
            raise FeedbackError(f"event {index} has no messageId")
        if verdict == "thumbs-up":
            events.append(
                thumbs_up(
                    common["message_id"],
                    common["prompt_id"],
                    common["model"],
                    common["tier"],
                    common["outcome"],
                    common["cited"],
                )
            )
        else:
            corrected = raw.get("correctedOutcome")
            events.append(
                correction(
                    common["message_id"],
                    common["prompt_id"],
                    common["model"],
                    common["tier"],
                    common["outcome"],
                    None if corrected is None else str(corrected),
                    common["cited"],
                    bool(raw.get("correctedCited", False)),
                )
            )
    return events


def compute_metrics(events: List[FeedbackEvent]) -> Dict[GroupKey, PromptVersionMetrics]:
    """Aggregate FP/FN metrics per ``(prompt version, model, tier)``."""
    per_group: Dict[GroupKey, PromptVersionMetrics] = {}
    for event in events:
        metrics = per_group.get(event.group())
        if metrics is None:
            metrics = PromptVersionMetrics(
                prompt_id=event.prompt_id, model=event.model, tier=event.tier
            )
            per_group[event.group()] = metrics
        metrics.events += 1
        if not event.scorable:
            metrics.unscored += 1
            continue
        seen = (
            event.true_positives() | event.false_positives() | event.false_negatives()
        )
        for label in seen:
            entry = metrics.by_label.setdefault(label, LabelMetrics(label=label))
            if label in event.true_positives():
                entry.tp += 1
            if label in event.false_positives():
                entry.fp += 1
            if label in event.false_negatives():
                entry.fn += 1
    return per_group


def project(metrics: Mapping[GroupKey, PromptVersionMetrics]) -> Dict[str, Any]:
    """The JSON projection the prompts view renders — the same keys, per triple."""
    rows: List[Dict[str, Any]] = []
    for group in sorted(metrics):
        entry = metrics[group]
        rows.append(
            {
                "prompt_id": entry.prompt_id,
                "model": entry.model,
                "tier": entry.tier,
                "events": entry.events,
                "unscored": entry.unscored,
                "total_tp": entry.total_tp(),
                "total_fp": entry.total_fp(),
                "total_fn": entry.total_fn(),
                "macro_precision": entry.macro_precision(),
                "macro_recall": entry.macro_recall(),
                "by_label": {
                    label: {
                        "tp": entry.by_label[label].tp,
                        "fp": entry.by_label[label].fp,
                        "fn": entry.by_label[label].fn,
                        "precision": entry.by_label[label].precision(),
                        "recall": entry.by_label[label].recall(),
                        "f1": entry.by_label[label].f1(),
                        "support": entry.by_label[label].support(),
                    }
                    for label in sorted(entry.by_label)
                },
            }
        )
    return {"schema": "chat-feedback/v1", "rows": rows}


def format_report(
    metrics: Mapping[GroupKey, PromptVersionMetrics], events: List[FeedbackEvent]
) -> str:
    """Render the per-triple report in the vocabulary the prompts view speaks."""
    lines = ["chat feedback report", "=" * 64]
    for group in sorted(metrics):
        entry = metrics[group]
        lines.append(
            f"promptId={entry.prompt_id}  model={entry.model}  tier={entry.tier}  "
            f"events={entry.events}  unscored={entry.unscored}  "
            f"total FP={entry.total_fp()}  total FN={entry.total_fn()}  "
            f"total TP={entry.total_tp()}  "
            f"accuracy={entry.overall_accuracy(events):.3f}"
        )
        for label in sorted(entry.by_label):
            local = entry.by_label[label]
            lines.append(
                f"  label={label:<12} tp={local.tp} fp={local.fp} fn={local.fn} "
                f"precision={local.precision():.3f} recall={local.recall():.3f} "
                f"f1={local.f1():.3f} support={local.support()}"
            )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chat-feedback",
        description="Per-(prompt version, model, tier) FP/FN metrics from chat feedback",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("report", "print the FP/FN report"),
        ("project", "print the JSON projection the prompts view renders"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--data", default=str(DEFAULT_EVENTS), help="events YAML path")
    args = parser.parse_args(argv)
    try:
        events = load_events(Path(args.data))
    except FeedbackError as exc:
        print(f"chat-feedback: {exc}", file=sys.stderr)
        return 1
    metrics = compute_metrics(events)
    if args.command == "report":
        print(format_report(metrics, events))
    else:
        print(json.dumps(project(metrics), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
