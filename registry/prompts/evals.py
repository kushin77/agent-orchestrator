#!/usr/bin/env python3
"""Regression-eval harness for the prompt library (issue #639).

---knowledge---
module_id: registry.prompts.evals
system: registry
app: prompts
solution_class: enterprise
patterns: [publish-precondition, regression-gate, fail-closed]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [EvalCase, EvalReport, load_cases, evaluate, require_ok, format_report, main]
invariants: "a module whose evals fail cannot be frozen by registry.py, because measurement is a precondition of publication"
gotchas: ""
related: ["#639"]
do_not_duplicate: null
---knowledge---

A published prompt module is only as good as the last time its behavior was
measured, so this harness makes the measurement a **precondition of
publication**: a module whose evals fail cannot be frozen by ``registry.py``.

Design (see README.md, section "The regression-eval gate"):

- An eval *case* binds a ``promptId`` to the set of outcome labels its output
  **must** carry (``expected``) and the set the run **actually** carried
  (``observed``). The two are handed to
  :func:`feedback.compute_metrics` as ground truth and prediction, so the
  library's own FP/FN math decides the verdict -- there is no second scoring
  implementation to drift from ``feedback.py``.
- A case **passes** iff it has no false positive and no false negative. A case
  with zero observed labels therefore fails its expectations rather than
  vacuously passing.
- :func:`evaluate` maps each ``promptId`` to a :class:`EvalReport`. A promptId
  with no cases is **unevaluated**, never silently green: publication of an
  unevaluated module is refused.

Cases live in ``evals/eval-cases.yaml``. The ``candidates`` section holds
promptIds that have no module definition (they exist only to exercise the
gate); the ``cases`` section holds the published modules.

Usage (from the repo root):

    python3 registry/prompts/evals.py report      # pass/fail per promptId
    python3 registry/prompts/evals.py report --fail-on-eval-failure
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    sys.exit(f"evals: missing dependency ({exc}); need PyYAML")

import feedback

PKG_DIR = Path(__file__).resolve().parent
DEFAULT_CASES = PKG_DIR / "evals" / "eval-cases.yaml"


class EvalGateError(Exception):
    """Base error for the regression-eval gate."""


class UnevaluatedPromptError(EvalGateError):
    """Raised when a promptId has no regression-eval cases at all."""


@dataclass(frozen=True)
class EvalCase:
    """One regression-eval case for a prompt version."""

    prompt_id: str
    case_id: str
    expected: frozenset
    observed: frozenset

    def to_outcome(self) -> feedback.Outcome:
        """Project onto the feedback library's outcome shape.

        ``expected`` is the ground truth and ``observed`` the prediction, which
        is what makes a missing label a false negative and an invented label a
        false positive in the shared FP/FN math.
        """
        return feedback.Outcome(
            prompt_id=self.prompt_id,
            case_id=self.case_id,
            predicted=self.observed,
            truth=self.expected,
        )

    def passes(self) -> bool:
        outcome = self.to_outcome()
        return not outcome.false_positives() and not outcome.false_negatives()

    def failures(self) -> Tuple[List[str], List[str]]:
        outcome = self.to_outcome()
        return sorted(outcome.false_positives()), sorted(outcome.false_negatives())


@dataclass
class EvalReport:
    """Aggregate eval verdict for one prompt version."""

    prompt_id: str
    cases: List[EvalCase] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passes())

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def failing_cases(self) -> List[EvalCase]:
        return [c for c in self.cases if not c.passes()]

    @property
    def ok(self) -> bool:
        """A report is green only when it has cases and all of them pass."""
        return self.total > 0 and self.failed == 0

    def metrics(self) -> feedback.PromptVersionMetrics:
        """FP/FN metrics through the shared feedback library."""
        return feedback.compute_metrics([c.to_outcome() for c in self.cases])[
            self.prompt_id
        ]


def load_cases(path: Optional[Path] = None) -> List[EvalCase]:
    """Load regression-eval cases from a YAML file shaped like eval-cases.yaml."""
    cases_path = Path(path) if path is not None else DEFAULT_CASES
    data = yaml.safe_load(cases_path.read_text(encoding="utf-8")) or {}
    cases: List[EvalCase] = []
    for section in ("cases", "candidates"):
        for raw in data.get(section, []) or []:
            cases.append(
                EvalCase(
                    prompt_id=str(raw["promptId"]),
                    case_id=str(raw.get("caseId", raw.get("promptId", ""))),
                    expected=frozenset(raw.get("expected", [])),
                    observed=frozenset(raw.get("observed", [])),
                )
            )
    return cases


def evaluate(cases: Iterable[EvalCase]) -> Dict[str, EvalReport]:
    """Group eval cases into a report per promptId."""
    reports: Dict[str, EvalReport] = {}
    for case in cases:
        report = reports.setdefault(case.prompt_id, EvalReport(case.prompt_id))
        report.cases.append(case)
    return reports


def require_ok(
    prompt_id: str,
    cases: Optional[Iterable[EvalCase]] = None,
    path: Optional[Path] = None,
) -> EvalReport:
    """Return the report for ``prompt_id`` or raise, for the publish gate.

    Raises :class:`UnevaluatedPromptError` when the promptId carries no cases
    (an unevaluated module is never publishable) and
    :class:`EvalGateError` when any of its cases fail.
    """
    all_cases = list(cases) if cases is not None else load_cases(path)
    report = evaluate(all_cases).get(prompt_id)
    if report is None or report.total == 0:
        raise UnevaluatedPromptError(
            f"{prompt_id} has no regression-eval cases in "
            f"{Path(path) if path is not None else DEFAULT_CASES}; an unevaluated "
            "module must not be published"
        )
    if not report.ok:
        lines: List[str] = []
        for case in report.failing_cases:
            fp, fn = case.failures()
            detail = []
            if fp:
                detail.append(f"FP={fp}")
            if fn:
                detail.append(f"FN={fn}")
            lines.append(f"  - {case.case_id} ({case.prompt_id}): {' '.join(detail)}")
        raise EvalGateError(
            f"{prompt_id} failed regression evals "
            f"({report.failed} of {report.total} case(s)):\n" + "\n".join(lines)
        )
    return report


def format_report(reports: Dict[str, EvalReport]) -> str:
    """Render a human-readable per-promptId eval report."""
    lines: List[str] = []
    lines.append("prompt regression-eval report")
    lines.append("=" * 40)
    for prompt_id in sorted(reports):
        report = reports[prompt_id]
        verdict = "PASS" if report.ok else "FAIL"
        lines.append(
            f"promptId={prompt_id}  cases={report.total}  "
            f"passed={report.passed}  failed={report.failed}  {verdict}"
        )
        for case in report.cases:
            fp, fn = case.failures()
            if case.passes():
                lines.append(f"  ok   {case.case_id}")
            else:
                detail = []
                if fp:
                    detail.append(f"FP={fp}")
                if fn:
                    detail.append(f"FN={fn}")
                lines.append(f"  FAIL {case.case_id}  {' '.join(detail)}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prompt-evals",
        description="Regression-eval harness for the versioned prompt library",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_report = sub.add_parser("report", help="print the eval report")
    p_report.add_argument("--data", default=str(DEFAULT_CASES), help="cases YAML path")
    p_report.add_argument(
        "--fail-on-eval-failure",
        action="store_true",
        help="exit nonzero when any promptId has a failing case",
    )
    args = parser.parse_args(argv)

    if args.command == "report":
        cases_path = Path(args.data)
        if not cases_path.exists():
            print(f"evals: cases file not found: {cases_path}", file=sys.stderr)
            return 1
        reports = evaluate(load_cases(cases_path))
        print(format_report(reports))
        if args.fail_on_eval_failure and any(
            not report.ok for report in reports.values()
        ):
            print("evals: FAIL (at least one promptId has a failing case)")
            return 1
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
