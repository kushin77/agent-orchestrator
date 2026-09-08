#!/usr/bin/env python3
"""A/B variant runner for prompt improvements.

Head-to-head evaluation of candidate prompt variants before one is published as
the next version of a taskType. Each variant is scored on the same labeled eval
set; per-variant FP/FN/accuracy is aggregated (reusing feedback.py) and the
best variant is selected - adapted from the ``kushin77/intelligence``
``prompt_tuner.py`` A/B pattern (PromptVariant + record_performance +
select_best_variant), stripped of the network-bound tuning that its partial
stub could not provide.

The runner is model-agnostic: ``run`` accepts a scorer callable
``(prompt_text, input_text) -> set[str]``. The offline demo supplies a
deterministic keyword stand-in so the mechanics are reproducible without a live
model; production callers swap in a real model call.

Usage (from the repo root):

    python3 registry/prompts/abtest.py demo
    python3 registry/prompts/abtest.py report --data seed/ab-test-runs.yaml
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    sys.exit(f"abtest: missing dependency ({exc}); need PyYAML")

from feedback import Outcome, PromptVersionMetrics, compute_metrics  # type: ignore

PKG_DIR = Path(__file__).resolve().parent
DEFAULT_RUNS = PKG_DIR / "seed" / "ab-test-runs.yaml"

Scorer = Callable[[str, str], Set[str]]


@dataclass
class Variant:
    """One candidate prompt (baseline or experiment) in an A/B run."""

    variant_id: str
    prompt: str
    label: str = ""


@dataclass
class EvalCase:
    """One labeled eval input shared by every variant in the run."""

    case_id: str
    input: str
    expected: frozenset


@dataclass
class AbExperiment:
    """Evaluates N prompt variants against M labeled eval cases."""

    run_id: str
    task_type: str
    variants: List[Variant]
    cases: List[EvalCase]

    def run(self, scorer: Scorer) -> Dict[str, PromptVersionMetrics]:
        """Score every (variant, case) pair and aggregate per-variant metrics."""
        events: List[Outcome] = []
        for variant in self.variants:
            for case in self.cases:
                predicted = scorer(variant.prompt, case.input)
                events.append(
                    Outcome(
                        prompt_id=variant.variant_id,
                        case_id=case.case_id,
                        predicted=frozenset(predicted),
                        truth=case.expected,
                    )
                )
        return compute_metrics(events)


def select_best(
    metrics: Dict[str, PromptVersionMetrics], events: List[Outcome]
) -> Tuple[str, float]:
    """Return the (variant_id, accuracy) with the highest overall accuracy."""
    best_id: Optional[str] = None
    best_accuracy = -1.0
    for variant_id, m in metrics.items():
        accuracy = m.overall_accuracy(events)
        if accuracy > best_accuracy:
            best_accuracy = accuracy
            best_id = variant_id
    if best_id is None:
        raise ValueError("no variants to select from")
    return best_id, best_accuracy


def load_recorded_runs(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load recorded A/B runs from YAML (see seed/ab-test-runs.yaml)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return {run["runId"]: run for run in data.get("runs", [])}


def metrics_for_recorded_run(run: Dict[str, Any]) -> Tuple[
    Dict[str, PromptVersionMetrics], List[Outcome]
]:
    """Aggregate per-variant metrics from a recorded run's case outcomes."""
    events = [
        Outcome(
            prompt_id=str(case["variantId"]),
            case_id=str(case.get("caseId", "")),
            predicted=frozenset(case.get("predicted", [])),
            truth=frozenset(case.get("expected", [])),
        )
        for case in run.get("cases", [])
    ]
    return compute_metrics(events), events


def format_experiment(
    run_id: str,
    metrics: Dict[str, PromptVersionMetrics],
    events: List[Outcome],
    baseline: str,
) -> str:
    """Render a per-variant accuracy table plus the selected winner."""
    lines: List[str] = []
    lines.append(f"A/B run: {run_id}")
    lines.append("=" * 44)
    rows: List[Tuple[str, float, int, int]] = []
    for variant_id in sorted(metrics):
        m = metrics[variant_id]
        accuracy = m.overall_accuracy(events)
        rows.append((variant_id, accuracy, m.total_fp(), m.total_fn()))
        winner = "  <-- baseline" if variant_id == baseline else ""
        lines.append(
            f"  {variant_id:<28} acc={accuracy:.3f} fp={m.total_fp():<2} "
            f"fn={m.total_fn()}{winner}"
        )
    best_id, best_accuracy = select_best(metrics, events)
    lines.append(f"winner: {best_id} (accuracy {best_accuracy:.3f})")
    return "\n".join(lines)


class KeywordScorer:
    """Deterministic stand-in model: first routing keyword found wins.

    The routing table is what a real prompt instructs the model to do; here it
    is supplied directly so the demo is reproducible offline. Swap for a live
    model call in production.
    """

    def __init__(self, routing: List[Tuple[str, str]], fallback: str = "noise") -> None:
        self.routing = routing
        self.fallback = fallback

    def __call__(self, prompt_text: str, input_text: str) -> Set[str]:
        lowered = input_text.lower()
        for keyword, label in self.routing:
            if keyword in lowered:
                return {label}
        return {self.fallback}


def _demo() -> int:
    """Run a reproducible offline A/B demo on the classify-route task."""
    baseline_prompt = (
        "Route tenant requests: refunds and chargebacks are support; invoices "
        "are billing; how-to and invites are support; keys and tokens are "
        "security; quotes and seats are sales; anything else is noise."
    )
    candidate_prompt = (
        "Route tenant requests. Disambiguation: money movement (refund, "
        "chargeback, invoice correction) is always billing; how-to, invites, "
        "and docs are always support; keys, tokens, and data access are always "
        "security; quotes and seats are sales; anything else is noise."
    )
    variants = [
        Variant("baseline-classify-route-v1", baseline_prompt, "v1 rules"),
        Variant("candidate-classify-route-v2", candidate_prompt, "v2 disambiguation"),
    ]
    cases = [
        EvalCase("c1", "Please refund the overcharge on our last invoice", frozenset({"billing"})),
        EvalCase("c2", "How do I invite a teammate to the org?", frozenset({"support"})),
        EvalCase("c3", "We lost access to our API keys after the rotation", frozenset({"security"})),
        EvalCase("c4", "Quote us for 500 seats on the enterprise plan", frozenset({"sales"})),
        EvalCase("c5", "Please unsubscribe this newsletter spam", frozenset({"noise"})),
        EvalCase("c6", "Invoice #2299 shows a charge we did not make", frozenset({"billing"})),
        EvalCase("c7", "Can you reverse the chargeback from last month?", frozenset({"billing"})),
    ]
    experiment = AbExperiment(
        run_id="ab-classify-route-v1-vs-v2",
        task_type="classify-route",
        variants=variants,
        cases=cases,
    )
    baseline_rules = [
        ("refund", "support"),
        ("invoice", "billing"),
        ("chargeback", "support"),
        ("invite", "support"),
        ("api", "security"),
        ("keys", "security"),
        ("quote", "sales"),
        ("seats", "sales"),
        ("unsubscribe", "noise"),
    ]
    candidate_rules = [
        ("refund", "billing"),
        ("chargeback", "billing"),
        ("invoice", "billing"),
        ("invite", "support"),
        ("api", "security"),
        ("keys", "security"),
        ("quote", "sales"),
        ("seats", "sales"),
        ("unsubscribe", "noise"),
    ]
    scorers = {
        "baseline-classify-route-v1": KeywordScorer(baseline_rules),
        "candidate-classify-route-v2": KeywordScorer(candidate_rules),
    }
    events: List[Outcome] = []
    for variant in variants:
        scorer = scorers[variant.variant_id]
        for case in cases:
            events.append(
                Outcome(
                    prompt_id=variant.variant_id,
                    case_id=case.case_id,
                    predicted=frozenset(scorer(variant.prompt, case.input)),
                    truth=case.expected,
                )
            )
    metrics = compute_metrics(events)
    print(format_experiment(experiment.run_id, metrics, events, variants[0].variant_id))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prompt-abtest", description="A/B variant runner for prompt improvements"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="run the reproducible offline A/B demo")
    p_report = sub.add_parser("report", help="aggregate a recorded A/B run file")
    p_report.add_argument("--data", default=str(DEFAULT_RUNS), help="runs YAML path")
    args = parser.parse_args(argv)

    if args.command == "demo":
        return _demo()
    if args.command == "report":
        runs_path = Path(args.data)
        if not runs_path.exists():
            print(f"abtest: runs file not found: {runs_path}", file=sys.stderr)
            return 1
        runs = load_recorded_runs(runs_path)
        for run_id in sorted(runs):
            run = runs[run_id]
            metrics, events = metrics_for_recorded_run(run)
            print(
                format_experiment(
                    run_id,
                    metrics,
                    events,
                    str(run.get("baseline", "")),
                )
            )
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
