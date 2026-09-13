"""Hygiene audit — every work item that did not close cleanly, named (issue #269).

The audit is **offline**: it reads a lifecycle record (produced by
``cli.py collect`` at runtime, or by a fixture in the gate) and never touches the
network. That is deliberate. A gate that depends on live GitHub state is green
whenever the record is stale — the failure mode measured in
``.board/snapshot.json``, where master reads conforming only because its snapshot
is old (tracked by #170). So the live collection is a runtime concern and the
*mechanism* is what the gate asserts, against fixtures it can mutate.

Two properties make the result trustworthy:

* **Scope is declared, never implied.** The record states what it covers, and the
  report echoes it, so a narrow audit cannot be mistaken for a clean board.
* **Legacy drift is quarantined by name, and the quarantine can only shrink.** A
  quarantine entry is honoured only while the issue tracking it is still open; if
  that issue closes, the entry becomes a finding itself
  (``QUARANTINE_STALE``) instead of quietly continuing to excuse the item.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from governance.lifecycle.model import (
    DECLARING_LABEL_PREFIXES,
    INVARIANTS_BY_CODE,
    ITEM_INVARIANTS,
    Invariant,
    invariant,
    invariants_for,
    owes_closure,
)


@dataclass(frozen=True)
class Finding:
    """One broken invariant on one item, with the remediation that clears it."""

    code: str
    subject: str
    detail: str

    @property
    def remediation(self) -> str:
        return invariant(self.code).remediation

    def __str__(self) -> str:
        return f"{self.code}  {self.subject}  {self.detail}"


@dataclass(frozen=True)
class Quarantine:
    """An item excused by name, while the issue tracking it is still open."""

    code: str
    subject: str
    tracked_by: str
    reason: str = ""


def load_quarantine(payload: dict | None) -> list[Quarantine]:
    """Read the declared legacy quarantine from a baseline document."""
    entries = []
    for raw in (payload or {}).get("quarantine", []) or []:
        entries.append(
            Quarantine(
                code=str(raw["code"]),
                subject=str(raw["subject"]),
                tracked_by=str(raw["tracked_by"]),
                reason=str(raw.get("reason", "")),
            )
        )
    return entries


def _filing_findings(item: dict) -> list[Finding]:
    """An open, milestoned item must declare the labels it is held to."""
    labels = [str(label) for label in item.get("labels") or []]
    if not item.get("milestone"):
        return []  # Unmilestoned work is out of the conformance gate's scope.
    if any(label.startswith(prefix) for prefix in DECLARING_LABEL_PREFIXES for label in labels):
        return []
    return [
        Finding(
            code="FILING_LABELS_MISSING",
            subject=f"#{item.get('issue')}",
            detail=f"open and milestoned ({item.get('milestone')}) but declares no declaring label",
        )
    ]


def _closure_findings(item: dict) -> list[Finding]:
    """A closed item owes every closure invariant, derived from its artifacts."""
    subject = f"#{item.get('issue')}"
    problems: list[Finding] = []

    pr = item.get("pr") or {}
    verify = item.get("verify") or {}
    # Evidence must name the *verified head* commit. It cannot name the merge
    # commit: a squash merge creates a new commit, so demanding equality there
    # would fail every correctly-merged item. What matters is that the tree which
    # was verified is the tree that landed.
    verified_commit = str(pr.get("head_commit") or "")

    if pr.get("state") != "merged":
        problems.append(
            Finding("PR_NOT_MERGED", subject, f"pull request state is {pr.get('state') or 'unknown'}, not merged")
        )
    elif not verify.get("ok"):
        problems.append(Finding("VERIFY_EVIDENCE_MISSING", subject, "no green verification attestation is recorded"))
    elif not verified_commit:
        problems.append(
            Finding("VERIFY_EVIDENCE_MISSING", subject, "the item records no verified head commit to hold the evidence against")
        )
    elif str(verify.get("commit") or "") != verified_commit:
        recorded = str(verify.get("commit") or "none")
        problems.append(
            Finding(
                "VERIFY_EVIDENCE_MISSING",
                subject,
                f"the attestation names {recorded[:12]}, not the verified head commit {verified_commit[:12]}",
            )
        )

    if not item.get("branch_deleted", False):
        problems.append(
            Finding("BRANCH_NOT_DELETED", subject, f"source branch {pr.get('branch') or '(unknown)'} still exists")
        )

    if (item.get("claim") or {}).get("live"):
        problems.append(
            Finding("CLAIM_STILL_HELD", subject, f"still claimed by {(item.get('claim') or {}).get('agent') or 'unknown'}")
        )

    directive = item.get("directive") or {}
    if directive and directive.get("state") != "done":
        problems.append(
            Finding(
                "DIRECTIVE_NOT_CONSUMED",
                subject,
                f"directive {directive.get('id') or '(unknown)'} is {directive.get('state') or 'unknown'}, not consumed",
            )
        )

    lane = item.get("lane") or {}
    if lane.get("present"):
        problems.append(
            Finding("LANE_NOT_RECLAIMED", subject, f"lane {lane.get('session_id') or '(unknown)'} is still provisioned")
        )

    if not item.get("closing_evidence", False):
        problems.append(Finding("CLOSING_EVIDENCE_MISSING", subject, "the issue was closed without recorded evidence"))

    if item.get("state") != "closed":
        problems.append(
            Finding(
                "ISSUE_NOT_CLOSED",
                subject,
                "the change landed but the issue is still open, so the item is still on the board",
            )
        )

    return problems


def audit_item(item: dict) -> list[Finding]:
    """Every invariant this item breaks (empty list = hygienic).

    A landed change owes the closure invariants even while its issue is still
    open, because closing the issue is one of those invariants.
    """
    if owes_closure(item):
        return _closure_findings(item)
    return _filing_findings(item)


def _tracking_state(record: dict, tracked_by: str) -> str:
    return str((record.get("tracking") or {}).get(tracked_by, "unknown"))


def audit(
    record: dict,
    quarantine: Iterable[Quarantine] | None = None,
) -> list[Finding]:
    """Audit a whole lifecycle record; findings are named, ordered and deduped.

    A quarantined ``(code, subject)`` pair is excused only while its tracking
    issue is open. A quarantine whose tracking issue has closed is reported as
    ``QUARANTINE_STALE`` — the quarantine is a lease on legacy debt, not a
    permanent exemption.
    """
    entries = list(quarantine or [])
    excused = {(entry.code, entry.subject) for entry in entries}
    problems: list[Finding] = []

    for item in record.get("items") or []:
        for finding in audit_item(item):
            if (finding.code, finding.subject) in excused:
                continue
            problems.append(finding)

    for entry in entries:
        if entry.code not in {inv.code for inv in ITEM_INVARIANTS}:
            problems.append(
                Finding("QUARANTINE_STALE", entry.subject, f"quarantine names unknown invariant {entry.code!r}")
            )
            continue
        if _tracking_state(record, entry.tracked_by) != "open":
            problems.append(
                Finding(
                    "QUARANTINE_STALE",
                    entry.subject,
                    f"excused for {entry.code} but its tracking issue {entry.tracked_by} is "
                    f"{_tracking_state(record, entry.tracked_by)}; retire the quarantine entry",
                )
            )
    return problems


def applicable_invariants(item: dict) -> list[Invariant]:
    """The invariants an item owes, given the artifacts it actually has."""
    return list(invariants_for(item))


def hygiene(record: dict, quarantine: Iterable[Quarantine] | None = None) -> dict:
    """The audit as a machine-readable report, with its scope stated."""
    problems = audit(record, quarantine)
    items = record.get("items") or []
    return {
        "scope": record.get("scope") or "unspecified",
        "items": len(items),
        "closed": sum(1 for item in items if item.get("state") == "closed"),
        "findings": [
            {
                "code": finding.code,
                "subject": finding.subject,
                "detail": finding.detail,
                "remediation": finding.remediation,
            }
            for finding in problems
        ],
        "hygienic": not problems,
    }
