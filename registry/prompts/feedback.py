#!/usr/bin/env python3
"""Feedback loop for the versioned prompt library.

Production outcomes (ground truth vs what a prompt version actually produced)
are turned into per-prompt-version metrics. Each outcome carries the set of
labels the prompt predicted and the set of ground-truth labels; a label is a
false positive (FP) when predicted but not true and a false negative (FN) when
true but not predicted. Per-version FP/FN volume is what drives prompt
improvement: a new version should lower FP/FN on the same task.

Adapted from ``kushin77/llm-triage`` ``feedback.py`` (ClassificationFeedback +
ConfusionEntry + FeedbackTracker) and generalized from issue labels to any
structured outcome labels a prompt module produces.

Usage (from the repo root):

    python3 registry/prompts/feedback.py report
    python3 registry/prompts/feedback.py report --data path/to/events.yaml
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set

try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    sys.exit(f"feedback: missing dependency ({exc}); need PyYAML")

PKG_DIR = Path(__file__).resolve().parent
DEFAULT_EVENTS = PKG_DIR / "seed" / "feedback-events.yaml"


@dataclass(frozen=True)
class Outcome:
    """One evaluated call of a prompt version."""

    prompt_id: str
    case_id: str
    predicted: frozenset
    truth: frozenset

    def true_positives(self) -> Set[str]:
        return set(self.predicted & self.truth)

    def false_positives(self) -> Set[str]:
        return set(self.predicted - self.truth)

    def false_negatives(self) -> Set[str]:
        return set(self.truth - self.predicted)

    def accuracy(self) -> float:
        """Jaccard agreement: |TP| / |TP u FP u FN| (1.0 when both empty)."""
        union = len(self.true_positives()) + len(self.false_positives()) + len(
            self.false_negatives()
        )
        if union == 0:
            return 1.0
        return len(self.true_positives()) / union


@dataclass
class LabelMetrics:
    """Per-label confusion counts and derived scores for one prompt version."""

    label: str
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

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
    """Aggregate FP/FN metrics for a single prompt version."""

    prompt_id: str
    events: int = 0
    by_label: Dict[str, LabelMetrics] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.by_label is None:
            self.by_label = {}

    def total_fp(self) -> int:
        return sum(m.fp for m in self.by_label.values())

    def total_fn(self) -> int:
        return sum(m.fn for m in self.by_label.values())

    def total_tp(self) -> int:
        return sum(m.tp for m in self.by_label.values())

    def macro_precision(self) -> float:
        values = [m.precision() for m in self.by_label.values()]
        return sum(values) / len(values) if values else 0.0

    def macro_recall(self) -> float:
        values = [m.recall() for m in self.by_label.values()]
        return sum(values) / len(values) if values else 0.0

    def overall_accuracy(self, outcomes: Iterable[Outcome]) -> float:
        total = 0.0
        count = 0
        for outcome in outcomes:
            if outcome.prompt_id == self.prompt_id:
                total += outcome.accuracy()
                count += 1
        return total / count if count else 0.0


def load_events(path: Path) -> List[Outcome]:
    """Load outcome events from a YAML file shaped like seed/feedback-events.yaml."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    events: List[Outcome] = []
    for raw in data.get("events", []):
        events.append(
            Outcome(
                prompt_id=str(raw["promptId"]),
                case_id=str(raw.get("caseId", "")),
                predicted=frozenset(raw.get("predicted", [])),
                truth=frozenset(raw.get("truth", [])),
            )
        )
    return events


def compute_metrics(events: List[Outcome]) -> Dict[str, PromptVersionMetrics]:
    """Aggregate FP/FN metrics per prompt version across all outcome events."""
    per_version: Dict[str, PromptVersionMetrics] = defaultdict(
        lambda: PromptVersionMetrics(prompt_id="")
    )
    for outcome in events:
        metrics = per_version[outcome.prompt_id]
        metrics.prompt_id = outcome.prompt_id
        metrics.events += 1
        for label in outcome.true_positives() | outcome.false_positives() | outcome.false_negatives():
            entry = metrics.by_label.setdefault(label, LabelMetrics(label=label))
            if label in outcome.true_positives():
                entry.tp += 1
            if label in outcome.false_positives():
                entry.fp += 1
            if label in outcome.false_negatives():
                entry.fn += 1
    return dict(per_version)


def format_report(
    metrics: Dict[str, PromptVersionMetrics], events: List[Outcome]
) -> str:
    """Render a human-readable per-prompt-version FP/FN report."""
    lines: List[str] = []
    lines.append("prompt feedback report")
    lines.append("=" * 40)
    for prompt_id in sorted(metrics):
        m = metrics[prompt_id]
        lines.append(f"promptId={prompt_id}  events={m.events}  "
                     f"total FP={m.total_fp()}  total FN={m.total_fn()}  "
                     f"total TP={m.total_tp()}  "
                     f"accuracy={m.overall_accuracy(events):.3f}")
        for label in sorted(m.by_label):
            lm = m.by_label[label]
            lines.append(
                f"  label={label:<12} tp={lm.tp} fp={lm.fp} fn={lm.fn} "
                f"precision={lm.precision():.3f} recall={lm.recall():.3f} "
                f"f1={lm.f1():.3f} support={lm.support()}"
            )
    return "\n".join(lines)


def _delta(baseline: PromptVersionMetrics, candidate: PromptVersionMetrics) -> str:
    dfp = candidate.total_fp() - baseline.total_fp()
    dfn = candidate.total_fn() - baseline.total_fn()
    return f"vs {baseline.prompt_id}: FP {dfp:+d}  FN {dfn:+d}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prompt-feedback",
        description="Per-prompt-version FP/FN metrics from outcome labels",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_report = sub.add_parser("report", help="print the FP/FN report")
    p_report.add_argument("--data", default=str(DEFAULT_EVENTS), help="events YAML path")
    args = parser.parse_args(argv)

    if args.command == "report":
        events_path = Path(args.data)
        if not events_path.exists():
            print(f"feedback: data file not found: {events_path}", file=sys.stderr)
            return 1
        events = load_events(events_path)
        metrics = compute_metrics(events)
        print(format_report(metrics, events))
        # Improvement comparison for multi-version prompt ids (the feedback loop).
        grouped: Dict[str, Dict[str, PromptVersionMetrics]] = defaultdict(dict)
        for prompt_id, m in metrics.items():
            task, _, version = prompt_id.partition("@")
            grouped[task][version] = m
        for task, versions in sorted(grouped.items()):
            ordered = sorted(versions)
            for later in ordered[1:]:
                print(f"{task}: {later} {_delta(versions[ordered[0]], versions[later])}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
