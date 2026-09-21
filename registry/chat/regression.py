"""The promotion gate for a changed chat prompt module version (issue #509).

---knowledge---
module_id: registry.chat.regression
system: registry
app: chat
solution_class: enterprise
patterns: [promotion-gate, baseline-diff, fail-closed]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [PromotionResult, parse_version_ref, evaluate_promotion, main]
invariants: "a case that passed on the baseline and fails on the candidate refuses the promotion, naming the case"
gotchas: ""
related: ["#509"]
do_not_duplicate: null
---knowledge---

A prompt-module version is a behaviour change, so it is evaluated against the
fixture set **before** promotion. The candidate version is run over the same
fixtures as the baseline, and any case that passed on the baseline and fails on
the candidate is a **regression**: it is named, and the promotion is refused.

    python3 -m registry.chat.regression \\
        --candidate chat-answer@v2 --baseline chat-answer@v1

A case that fails on *both* versions is not attributed to the candidate — it is
reported as pre-existing drift, by name, so a real failure is never silently
folded into "no regression". A candidate that cannot be resolved cannot be
assessed, and says so.

Exit-code contract: ``0`` promotable / ``1`` regressed (cases named) /
``2`` cannot assess.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .eval import harness

OK = "OK"
REGRESSED = "REGRESSED"
CANNOT_ASSESS = "CANNOT-ASSESS"


class PromotionError(Exception):
    """Raised when a promotion cannot even be described (bad version reference)."""


def parse_version_ref(reference: str) -> Tuple[str, str]:
    """Split ``taskType@version`` into its parts, refusing a malformed reference."""
    task_type, separator, version = str(reference).partition("@")
    if not separator or not task_type or not version:
        raise PromotionError(
            f"{reference!r} is not a taskType@version reference (expected e.g. chat-answer@v2)"
        )
    return task_type, version


@dataclass(frozen=True)
class PromotionResult:
    """The verdict for one candidate version against one baseline version."""

    candidate: str
    baseline: str
    status: str
    regressions: Tuple[str, ...] = ()
    improvements: Tuple[str, ...] = ()
    pre_existing: Tuple[str, ...] = ()
    unassessable: Tuple[str, ...] = ()
    candidate_results: Tuple[harness.CaseResult, ...] = ()
    baseline_results: Tuple[harness.CaseResult, ...] = ()
    notes: Tuple[str, ...] = ()

    @property
    def exit_code(self) -> int:
        if self.status == REGRESSED:
            return 1
        if self.status == CANNOT_ASSESS:
            return 2
        return 0

    def _reasons(self, results: Tuple[harness.CaseResult, ...], case_id: str) -> str:
        for result in results:
            if result.case_id == case_id:
                detail = [
                    f"{check.name}({check.detail})"
                    for check in result.checks
                    if not check.ok
                ]
                return ", ".join(detail) if detail else result.detail
        return "no result recorded"

    def render(self) -> str:
        lines = [f"promotion: {self.candidate} vs {self.baseline}"]
        baseline_pass = sum(1 for r in self.baseline_results if r.status == harness.PASS)
        candidate_pass = sum(1 for r in self.candidate_results if r.status == harness.PASS)
        total = len(self.candidate_results)
        lines.append(f"  baseline   {self.baseline}: {baseline_pass}/{total} case(s) pass")
        lines.append(f"  candidate  {self.candidate}: {candidate_pass}/{total} case(s) pass")
        for case_id in self.regressions:
            lines.append(
                f"  REGRESSED {case_id} — candidate FAIL: "
                f"{self._reasons(self.candidate_results, case_id)}"
            )
        for case_id in self.pre_existing:
            lines.append(
                f"  PRE-EXISTING-FAILURE {case_id} — fails on both versions, not "
                "attributed to the candidate"
            )
        for case_id in self.improvements:
            lines.append(f"  IMPROVED {case_id} — failed on the baseline, passes now")
        for case_id in self.unassessable:
            lines.append(f"  CANNOT-ASSESS {case_id} — the candidate could not be run")
        for note in self.notes:
            lines.append(f"  NOTE {note}")
        if self.status == OK:
            lines.append(
                f"promotion: OK — {self.candidate} regresses no fixture case "
                f"({candidate_pass}/{total} pass)"
            )
        elif self.status == REGRESSED:
            lines.append(
                f"promotion: NOT-OK — {len(self.regressions)} regression(s): "
                f"{', '.join(self.regressions)}"
            )
        else:
            lines.append(
                "promotion: CANNOT-ASSESS — the candidate could not be evaluated "
                "against the fixtures"
            )
        return "\n".join(lines)


def evaluate_promotion(
    candidate: str,
    baseline: str,
    cases_path: Optional[Path] = None,
    root: Optional[Path] = None,
) -> PromotionResult:
    """Run the fixtures under both versions and name what the candidate changed."""
    candidate_task, candidate_version = parse_version_ref(candidate)
    baseline_task, baseline_version = parse_version_ref(baseline)
    if candidate_task != baseline_task:
        raise PromotionError(
            f"a promotion compares two versions of one taskType "
            f"({candidate_task!r} vs {baseline_task!r})"
        )
    candidate_report = harness.evaluate(
        cases_path, root, versions={candidate_task: candidate_version}
    )
    baseline_report = harness.evaluate(
        cases_path, root, versions={baseline_task: baseline_version}
    )
    baseline_status = {
        result.case_id: result.status for result in baseline_report.results
    }
    regressions = []
    improvements = []
    pre_existing = []
    for result in candidate_report.results:
        prior = baseline_status.get(result.case_id)
        if result.status == harness.FAIL:
            if prior == harness.PASS:
                regressions.append(result.case_id)
            else:
                pre_existing.append(result.case_id)
        elif result.status == harness.PASS and prior == harness.FAIL:
            improvements.append(result.case_id)

    unassessable = candidate_report.unassessable
    notes = []
    if unassessable and regressions:
        notes.append(
            "some cases could not be run at all; the named regressions are independent "
            "of them"
        )
    if baseline_report.unassessable:
        notes.append(
            "the baseline could not be run over "
            f"{', '.join(baseline_report.unassessable)}: nothing there can be attributed "
            "to this candidate"
        )
    if pre_existing:
        notes.append(
            "cases failing on both versions are pre-existing drift in the fixture or "
            "the baseline, not this candidate's regression"
        )
    if regressions:
        status = REGRESSED
    elif unassessable or baseline_report.unassessable or not candidate_report.results:
        status = CANNOT_ASSESS
    else:
        status = OK
    return PromotionResult(
        candidate=candidate,
        baseline=baseline,
        status=status,
        regressions=tuple(regressions),
        improvements=tuple(improvements),
        pre_existing=tuple(pre_existing),
        unassessable=tuple(unassessable),
        candidate_results=tuple(candidate_report.results),
        baseline_results=tuple(baseline_report.results),
        notes=tuple(notes),
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chat-regression",
        description="Evaluate a candidate chat prompt module version before promotion",
    )
    parser.add_argument("--candidate", required=True, help="taskType@version being promoted")
    parser.add_argument("--baseline", required=True, help="taskType@version it replaces")
    parser.add_argument("--cases", default=str(harness.DEFAULT_CASES))
    parser.add_argument("--root", default=None, help="chat package root override")
    args = parser.parse_args(argv)
    try:
        result = evaluate_promotion(
            args.candidate, args.baseline, Path(args.cases), args.root
        )
    except (PromotionError, harness.EvalFixtureError) as exc:
        print(f"promotion: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return 2
    print(result.render())
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
