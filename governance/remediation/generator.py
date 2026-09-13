"""Violation -> remediation issue generation, with dedup/merge (issue #142).

Consumes `governance.conformance` `Finding` objects (board conformance checks
and change-set mandate checks, issue #140) and produces `RemediationIssue`
payloads: title, labels, summary, owner lane, corrective steps, policy
reference and evidence.

Repeated findings for the same (code, subject) are merged into one issue with
an accumulated occurrence count and evidence list, rather than one issue per
scan run — "the workflow suppresses duplicates and merges repeated findings
into an actionable issue" (issue #142 acceptance criterion).
"""

from __future__ import annotations

from typing import Iterable, List, Sequence

from remediation_model import (
    RemediationIssue,
    dedup_key,
    lane_for,
    sla_hours_for,
    severity_for,
)

# Conformance findings carry a free-text `remediation` string already (the
# checker's own advice). We fan it out into a corrective-steps list of one,
# unless the caller supplies extra steps (e.g. a runbook link).
_GENERIC_STEPS = (
    "Review the cited evidence against the referenced policy.",
    "Apply the corrective action below.",
    "Re-run the scanner to confirm the finding clears.",
)


def title_for(code: str, subject: str) -> str:
    return "[remediation] %s: %s" % (code, subject or "repo-wide")


def build_issue(finding, *, policy_ref: str = "governance/conformance/policy.yaml",
                 scope: str = "repo", repo: str = "") -> RemediationIssue:
    """Build one RemediationIssue from a single conformance Finding.

    ``finding`` is duck-typed against `governance.conformance.model.Finding`
    (attributes: code, message, severity, subject, remediation) so this stays
    usable against any object shaped the same way, without importing the
    conformance package at module scope (kept import-light, matching the
    sibling packages' standalone-module convention).
    """
    code = finding.code
    subject = finding.subject
    severity = severity_for(code, finding.severity)
    lane = lane_for(code, subject)
    steps = tuple(s for s in (finding.remediation,) if s) or _GENERIC_STEPS

    return RemediationIssue(
        key=dedup_key(code, subject),
        code=code,
        subject=subject,
        title=title_for(code, subject),
        severity=severity,
        owner_lane=lane,
        sla_hours=sla_hours_for(severity),
        policy_ref=policy_ref,
        corrective_steps=steps,
        evidence=[finding.message],
        occurrences=1,
        scope=scope,
        repo=repo,
    )


def merge(issues: Iterable[RemediationIssue]) -> List[RemediationIssue]:
    """Collapse issues sharing a dedup key into one, accumulating evidence and
    occurrences. The highest-severity variant's routing wins (severity only
    ever needs to go up when the same rule keeps failing, not down)."""
    merged: dict = {}
    order: List[str] = []
    for issue in issues:
        existing = merged.get(issue.key)
        if existing is None:
            merged[issue.key] = RemediationIssue(
                key=issue.key,
                code=issue.code,
                subject=issue.subject,
                title=issue.title,
                severity=issue.severity,
                owner_lane=issue.owner_lane,
                sla_hours=issue.sla_hours,
                policy_ref=issue.policy_ref,
                corrective_steps=issue.corrective_steps,
                evidence=list(issue.evidence),
                occurrences=issue.occurrences,
                scope=issue.scope,
                repo=issue.repo,
            )
            order.append(issue.key)
            continue

        existing.occurrences += issue.occurrences
        for item in issue.evidence:
            if item not in existing.evidence:
                existing.evidence.append(item)
        from remediation_model import SEVERITY_RANK  # local import: avoid a cycle at module load

        if SEVERITY_RANK.get(issue.severity, 0) > SEVERITY_RANK.get(existing.severity, 0):
            existing.severity = issue.severity
            existing.sla_hours = issue.sla_hours

    return [merged[key] for key in order]


def generate(findings: Sequence, *, policy_ref: str = "governance/conformance/policy.yaml",
             scope: str = "repo", repo: str = "") -> List[RemediationIssue]:
    """Findings -> deduped, merged remediation issues, ready to route/dispatch."""
    built = [
        build_issue(f, policy_ref=policy_ref, scope=scope, repo=repo)
        for f in findings
    ]
    return merge(built)
