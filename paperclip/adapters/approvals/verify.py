"""Verify a projection against the authority it claims (issue #416).

`project` derives; `verify` *re-reads the authority* and refuses any projection
that cannot be reproduced from it. This is the rule the EPIC demands: an
approval that changes only the projection — a grant with no authoritative record
behind it — is a bug, and it must fail here rather than pass as a grant.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

from .mapping import decision_index, project, render
from .model import (
    STATE_DENIED,
    STATE_GRANTED,
    STATE_PENDING,
    Approval,
    Finding,
    Projection,
    authority_for,
)
from .schema import load_schema, validate


def verify_approval(approval: Approval, index: dict) -> List[Finding]:
    """Re-check one approval against the decision index read from the authority."""
    findings: List[Finding] = []

    authority = authority_for(approval.kind)
    if approval.authority != authority.surface or approval.authority_store != authority.store:
        findings.append(
            Finding(
                "authority-mismatch",
                f"{approval.id}: names authority {approval.authority!r} but a {approval.kind} is "
                f"authoritative on {authority.surface!r}",
                kind=approval.kind,
                subject=approval.subject,
            )
        )

    if approval.state == STATE_PENDING:
        if approval.decision_ref:
            findings.append(
                Finding(
                    "projection-without-record",
                    f"{approval.id}: pending approval cites decision {approval.decision_ref!r}; "
                    "pending means there is no decision record",
                    ref=approval.decision_ref,
                    kind=approval.kind,
                    subject=approval.subject,
                )
            )
        return findings

    if approval.state not in (STATE_GRANTED, STATE_DENIED):
        findings.append(
            Finding("unknown-state", f"{approval.id}: state {approval.state!r} is not one of pending/granted/denied", kind=approval.kind, subject=approval.subject)
        )
        return findings

    if not approval.decision_ref:
        findings.append(
            Finding(
                "projection-without-record",
                f"{approval.id}: {approval.state} with no authoritative record — a projection "
                "change with no record behind it is a bug",
                kind=approval.kind,
                subject=approval.subject,
            )
        )
        return findings

    record = index.get(approval.decision_ref)
    if record is None:
        findings.append(
            Finding(
                "record-absent",
                f"{approval.id}: cites {approval.decision_ref!r}, which the {approval.authority} "
                "authority does not hold",
                ref=approval.decision_ref,
                kind=approval.kind,
                subject=approval.subject,
            )
        )
        return findings

    expected_state = STATE_GRANTED if record.granted else STATE_DENIED
    actual = (approval.kind, approval.subject, approval.state, approval.actor)
    expected = (record.kind, record.subject, expected_state, record.actor)
    if actual != expected:
        findings.append(
            Finding(
                "record-contradicts",
                f"{approval.id}: projection says {actual!r} but {approval.decision_ref} records {expected!r}",
                ref=approval.decision_ref,
                kind=approval.kind,
                subject=approval.subject,
            )
        )
    return findings


def verify(root: Path | str, projection: Optional[Projection] = None) -> List[Finding]:
    """Every finding: projection refusals plus every authority inconsistency."""
    root = Path(root)
    if projection is None:
        projection = project(root)
    findings: List[Finding] = list(projection.findings)

    index = decision_index(root)
    seen: Set[Tuple[str, str]] = set()
    for approval in projection.approvals:
        seen.add((approval.kind, approval.subject))
        findings.extend(verify_approval(approval, index))

    for problem in schema_findings(projection):
        findings.append(problem)

    for ref, decision in sorted(index.items()):
        if (decision.kind, decision.subject) not in seen:
            findings.append(
                Finding(
                    "orphan-decision",
                    f"{ref} records a {decision.kind} decision for {decision.subject} but no "
                    "approval projects it",
                    ref=ref,
                    kind=decision.kind,
                    subject=decision.subject,
                )
            )
    return findings


def schema_findings(projection: Projection) -> List[Finding]:
    """Every projected approval must conform to ``schema/approval.schema.json``."""
    schema = load_schema()
    findings: List[Finding] = []
    for approval in projection.approvals:
        for problem in validate(approval.to_dict(), schema, where=approval.id):
            findings.append(
                Finding("schema-violation", problem, kind=approval.kind, subject=approval.subject)
            )
    return findings


def deterministic(root: Path | str) -> bool:
    """Two independent projections over one revision must be byte-identical."""
    return render(project(root)) == render(project(root))


def findings_summary(findings: Sequence[Finding]) -> str:
    """One line per finding, ordered for a stable gate transcript."""
    return "\n".join(finding.line() for finding in sorted(findings, key=lambda f: (f.code, f.ref, f.detail)))
