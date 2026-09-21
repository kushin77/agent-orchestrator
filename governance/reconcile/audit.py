"""Worktree/branch audit — every artifact on disk must be explained (#628).

---knowledge---
module_id: governance.reconcile.audit
system: governance
app: reconcile
solution_class: enterprise
patterns: [fail-closed, offline-hermetic, injected-effects, lane-isolation, explain-every-artifact]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [AuditUnavailable, WorktreeEntry, Artifact, Explanation, AuditReport, AuditOps, parse_worktrees, read_beats, audit, describe]
invariants: ""
gotchas: ""
related: ["#628", "#1291"]
do_not_duplicate: null
---knowledge---

``heartbeat.judge`` answers "is this session dead?" and ``sweep`` answers "what do
I do with a dead one?". Both start from the sessions that *beat*, so both are
blind to a lane that left no heartbeat at all: its worktree, its branch and the
work they hold are simply not in their input. Measured on this workstation
(2026-09-14): 118 ``git worktree list`` entries, 41 of them for **closed** issues,
``.fleet/sessions/`` holding no beats and the claim ledger holding no live claim —
and ``status`` still printed ``0 session(s), 0 orphan(s)`` and exited OK. Absence
of evidence was being read as absence of orphans: a fail-open audit.

This module closes that by enumerating the **disk** rather than the evidence.
Every ``git worktree list`` entry (except the primary checkout, which cannot be
orphaned because it *is* the repository) and every local ``issue-*`` branch is an
*artifact*, and every artifact must be explained by at least one of:

* a **session beat** — ``.fleet/sessions/<id>.json`` names its worktree, its
  branch or its issue (``heartbeat.Session``);
* a **claim record** — a live claim on the artifact's issue, from
  ``.board/claims/`` plus the frozen ``.board/claims.jsonl``
  (``governance.dispatch.claims``);
* the **landing history** — ``.fleet/lifecycle/<issue>.json``, the journal the
  lifecycle close-out writes once an item's work has landed
  (``governance.lifecycle.cli.JOURNAL_DIR``);
* the **landing proof** — the *work* is on the default branch, proven from the
  default branch's own commits by ``landing.py`` (issue #1291). This is a
  separate source on purpose: the journal above is **gitignored runtime state**,
  so a lane that landed on a box that never wrote one was reported as an orphan
  — measured at ``99f6b37``: 25 of 35 findings were false for exactly this
  reason, and each one red a composite gate that serializes the whole fleet.

An artifact no record explains is **reported by name**.

**This audit has no removal path.** It has no ``apply``, it calls no destructive
operation, and ``AuditOps`` has no such method for it to call. Reclaiming stays
``sweep``'s job and ``sweep``'s three-way rule is untouched: the worker never
trades unmerged work for an unlocked issue. This only makes visible the lanes
``sweep`` structurally cannot see, so a human — or a later sweep, once a real
beat exists — can act on them.

The exit contract matters most here, because the defect was a *wrong OK*: ``0``
only when the state was read and every artifact was explained, ``1`` when an
artifact is unmatched, and ``2`` CANNOT-ASSESS when the state could not be read —
an unreadable ``git``, a worktree list with no primary entry (a repository always
has one, so an empty answer is a failed read rather than a clean one), a corrupt
session beat, a corrupt claim record or a corrupt journal. An audit that cannot
tell "no orphans" from "could not look" reports the second, never the first.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from governance.reconcile.heartbeat import Session, now_epoch, sessions_dir

WORKTREE = "worktree"
BRANCH = "branch"

MATCHED = "matched"
UNMATCHED = "unmatched"
EXEMPT = "exempt"

#: A local branch that names the issue it serves. The same shape
#: ``governance/lifecycle`` reads an issue out of a branch name with.
ISSUE_BRANCH = re.compile(r"^issue-(\d+)")

#: Where the lifecycle close-out journals a landed item.
LANDING_DIR = ".fleet/lifecycle"

#: The ledger the dispatch claim records live under.
CLAIMS_DIR = ".board/claims"


class AuditUnavailable(Exception):
    """A source the audit needs could not be read: the verdict is CANNOT-ASSESS.

    Raised instead of returning a plausible-looking empty answer, because "no
    record exists" and "a record I could not read" are exactly the two cases this
    audit was written to keep apart.
    """


@dataclass(frozen=True)
class WorktreeEntry:
    """One line of ``git worktree list --porcelain``."""

    path: str
    branch: str = ""
    head: str = ""
    primary: bool = False
    detached: bool = False
    locked: bool = False
    prunable: bool = False

    def to_json(self) -> dict:
        return {
            "path": self.path,
            "branch": self.branch,
            "head": self.head,
            "primary": self.primary,
            "detached": self.detached,
            "locked": self.locked,
            "prunable": self.prunable,
        }


@dataclass(frozen=True)
class Artifact:
    """One thing on disk that a governance record has to account for."""

    kind: str
    name: str
    branch: str = ""
    issue: int | None = None

    def to_json(self) -> dict:
        return {"kind": self.kind, "name": self.name, "branch": self.branch, "issue": self.issue}

    def __str__(self) -> str:
        return f"{self.kind} {self.name}"


@dataclass(frozen=True)
class Explanation:
    """What explains one artifact — or the finding that nothing does."""

    artifact: Artifact
    disposition: str
    evidence: tuple[str, ...] = ()
    reason: str = ""

    @property
    def matched(self) -> bool:
        return self.disposition == MATCHED

    @property
    def unmatched(self) -> bool:
        return self.disposition == UNMATCHED

    @property
    def exempt(self) -> bool:
        return self.disposition == EXEMPT

    def to_json(self) -> dict:
        return {
            **self.artifact.to_json(),
            "disposition": self.disposition,
            "evidence": list(self.evidence),
            "reason": self.reason,
        }


@dataclass
class AuditReport:
    """Every artifact examined, and what explains it."""

    root: str = ""
    at: float = 0.0
    explanations: list[Explanation] = field(default_factory=list)
    reason: str = ""
    unreadable: tuple[str, ...] = ()

    @property
    def assessable(self) -> bool:
        """False when the state could not be read — the verdict is CANNOT-ASSESS."""
        return not self.reason

    @property
    def matched(self) -> list[Explanation]:
        return [item for item in self.explanations if item.matched]

    @property
    def unmatched(self) -> list[Explanation]:
        return [item for item in self.explanations if item.unmatched]

    @property
    def exempt(self) -> list[Explanation]:
        return [item for item in self.explanations if item.exempt]

    @property
    def exit_code(self) -> int:
        if not self.assessable:
            return 2
        return 1 if self.unmatched else 0

    def to_json(self) -> dict:
        return {
            "root": self.root,
            "at": self.at,
            "assessable": self.assessable,
            "reason": self.reason,
            "counts": {
                "examined": len(self.explanations),
                MATCHED: len(self.matched),
                UNMATCHED: len(self.unmatched),
                EXEMPT: len(self.exempt),
            },
            "artifacts": [item.to_json() for item in self.explanations],
        }


class AuditOps(Protocol):
    """The reads the audit needs. Injected, so its rules are testable offline.

    Deliberately read-only: there is no removal method here, which is how "this
    audit reports but never removes" is enforced by construction rather than by
    comment.
    """

    def list_worktrees(self) -> list[WorktreeEntry]: ...

    def list_local_branches(self) -> list[str]: ...

    def active_claims(self) -> dict[int, str]: ...

    def landed_issues(self) -> set[int]: ...

    def landing_proof(self, kind: str, name: str, branch: str) -> str:
        """Evidence that this artifact's *work* is on the default branch (#1291).

        ``""`` when it is not proven (the artifact stays a finding); else a
        string naming how it was proven — ``landed:ancestor:<sha>``,
        ``landed:tree-contained:<sha>`` or ``landed:patch-identity:<sha>``.

        Optional on a double: an ops implementation written before this existed
        simply explains fewer artifacts (its audit reports *more* findings,
        never fewer), which is the fail-closed direction for an excuse.
        """


def _issue_of(branch: str) -> int | None:
    match = ISSUE_BRANCH.match(branch or "")
    return int(match.group(1)) if match else None


def parse_worktrees(porcelain: str) -> list[WorktreeEntry]:
    """Parse ``git worktree list --porcelain`` output.

    The main worktree is always listed first (git documents the order), which is
    how the primary checkout is identified — it is the one entry that must never
    be called an orphan. Pure text in, entries out, so the parse is testable
    without a repository.
    """
    entries: list[WorktreeEntry] = []
    for block in porcelain.strip().split("\n\n"):
        path = ""
        branch = ""
        head = ""
        detached = locked = prunable = False
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            value = value.strip()
            if key == "worktree":
                path = value
            elif key == "HEAD":
                head = value
            elif key == "branch":
                branch = value.removeprefix("refs/heads/")
            elif key == "detached":
                detached = True
            elif key == "locked":
                locked = True
            elif key == "prunable":
                prunable = True
        if path:
            entries.append(
                WorktreeEntry(
                    path=path,
                    branch=branch,
                    head=head,
                    primary=not entries,
                    detached=detached,
                    locked=locked,
                    prunable=prunable,
                )
            )
    return entries


def _read(label: str, reader, unreadable: list[str]):
    """Call one injected reader, recording an unreadable source instead of crashing.

    Any failure — the audited exception, or a bug in an ops implementation —
    makes the source unreadable, which makes the whole audit CANNOT-ASSESS. That
    is the fail-closed direction on purpose.
    """
    try:
        return reader()
    except Exception as exc:  # noqa: BLE001 - an unreadable source is data, not a crash
        unreadable.append(f"{label}: {type(exc).__name__}: {exc}"[:200])
        return None


def read_beats(root: Path | str) -> list[Session]:
    """Every session beat, strictly: a beat that cannot be parsed is unreadable.

    Stricter than ``heartbeat.list_sessions``, which skips a malformed record so a
    sweep is never blocked by one bad file. Here a skipped record would silently
    turn into an unmatched artifact — evidence read as absence, which is the
    defect this module exists to fix — so a corrupt beat makes the audit
    CANNOT-ASSESS instead.
    """
    directory = sessions_dir(root)
    if not directory.exists():
        return []
    beats: list[Session] = []
    for path in sorted(directory.glob("*.json")):
        try:
            beats.append(Session.from_json(json.loads(path.read_text(encoding="utf-8"))))
        except Exception as exc:  # noqa: BLE001 - an unreadable beat is not "no beat"
            raise AuditUnavailable(f"{path.name} is unreadable ({type(exc).__name__}: {exc})") from exc
    return beats


def _explain(
    artifact: Artifact,
    *,
    by_worktree: dict[str, set[str]],
    by_branch: dict[str, set[str]],
    by_issue: dict[int, set[str]],
    claims: dict[int, str],
    landed: set[int],
) -> Explanation:
    """Which record, if any, accounts for this artifact."""
    evidence: list[str] = []

    def add(tag: str) -> None:
        if tag not in evidence:
            evidence.append(tag)

    if artifact.kind == WORKTREE:
        for session_id in sorted(by_worktree.get(artifact.name, ())):
            add(f"beat:{session_id}")
    if artifact.branch:
        for session_id in sorted(by_branch.get(artifact.branch, ())):
            add(f"beat:{session_id}")
    if artifact.issue:
        for session_id in sorted(by_issue.get(artifact.issue, ())):
            add(f"beat:{session_id}")
        if artifact.issue in claims:
            add(f"claim:#{artifact.issue}:{claims[artifact.issue]}")
        if artifact.issue in landed:
            add(f"landed:#{artifact.issue}")

    if evidence:
        return Explanation(artifact, MATCHED, tuple(evidence), "explained by " + ", ".join(evidence))
    return Explanation(
        artifact,
        UNMATCHED,
        (),
        "no session beat, claim record or landing record names it",
    )


def audit(
    root: Path | str,
    *,
    ops: AuditOps | None = None,
    prefix: str = "issue-",
    at: float | None = None,
) -> AuditReport:
    """Report every worktree and every local ``issue-*`` branch nothing explains.

    Reads the disk (through ``ops``) and the repository's own records, and
    reports. It removes nothing and unlocks nothing: an unmatched artifact is a
    finding, not a reclaim, and it stays on disk and in the claim ledger for a
    human — or a later sweep — to resolve.
    """
    if ops is None:
        raise ValueError("audit requires an operations port (see RepoOps)")
    root = Path(root)
    report = AuditReport(root=str(root), at=now_epoch() if at is None else at)

    unreadable: list[str] = []
    worktrees = _read("worktrees", ops.list_worktrees, unreadable)
    branches = _read("branches", ops.list_local_branches, unreadable)
    claims = _read("claims", ops.active_claims, unreadable)
    landed = _read("landing", ops.landed_issues, unreadable)
    beats = _read("beats", lambda: read_beats(root), unreadable)

    if unreadable:
        report.unreadable = tuple(unreadable)
        report.reason = "; ".join(unreadable)
        return report

    # A repository always has at least its own worktree, so an empty answer is a
    # failed read, not a clean repository. Refusing here is the whole point: an
    # audit that cannot enumerate must not report OK.
    if not worktrees:
        report.reason = "git reported no worktrees at all; a repository has at least its own"
        report.unreadable = (report.reason,)
        return report
    if not any(entry.primary for entry in worktrees):
        report.reason = "git reported no primary worktree; the listing could not be identified"
        report.unreadable = (report.reason,)
        return report

    by_worktree: dict[str, set[str]] = {}
    by_branch: dict[str, set[str]] = {}
    by_issue: dict[int, set[str]] = {}
    for session in beats:
        if session.worktree:
            by_worktree.setdefault(session.worktree, set()).add(session.session_id)
        if session.branch:
            by_branch.setdefault(session.branch, set()).add(session.session_id)
        if session.issue:
            by_issue.setdefault(int(session.issue), set()).add(session.session_id)

    for entry in worktrees:
        artifact = Artifact(WORKTREE, entry.path, entry.branch, _issue_of(entry.branch))
        if entry.primary:
            # Not a finding: the primary checkout is the repository, and every
            # repository has exactly one. Recorded as examined-and-exempt rather
            # than dropped, so "not reported" is never confused with "not looked
            # at", and every other entry is still held to the same rule.
            report.explanations.append(
                Explanation(
                    artifact,
                    EXEMPT,
                    ("primary",),
                    "the primary checkout is the repository itself; it cannot be orphaned",
                )
            )
        else:
            report.explanations.append(
                _explain(artifact, by_worktree=by_worktree, by_branch=by_branch,
                         by_issue=by_issue, claims=claims, landed=landed)
            )

    for branch in branches:
        if not branch.startswith(prefix):
            continue
        artifact = Artifact(BRANCH, branch, branch, _issue_of(branch))
        report.explanations.append(
            _explain(artifact, by_worktree=by_worktree, by_branch=by_branch,
                     by_issue=by_issue, claims=claims, landed=landed)
        )

    # The landing proof (#1291) runs last and only for what nothing else
    # explains: it is the most expensive source (a handful of git reads per
    # artifact) and the only one that is *never* needed when a beat, a claim or
    # the journal already accounts for the artifact.
    prover = getattr(ops, "landing_proof", None)
    if prover is not None:
        failures: list[str] = []
        for index, explanation in enumerate(report.explanations):
            if explanation.matched or explanation.exempt:
                continue
            artifact = explanation.artifact
            try:
                proof = prover(artifact.kind, artifact.name, artifact.branch)
            except Exception as exc:  # noqa: BLE001 - an unreadable source is data
                failures.append(f"landing proof: {type(exc).__name__}: {exc}"[:200])
                continue
            if proof:
                report.explanations[index] = Explanation(
                    artifact, MATCHED, (proof,), f"explained by {proof}"
                )
        if failures:
            # "I could not look" is not "nothing is there" — the same rule every
            # other source here follows. A prover that cannot run must not silently
            # leave artifacts looking unexplained *or* explained.
            report.reason = "; ".join(failures)
            report.unreadable = tuple(failures)
    return report


def describe(report: AuditReport) -> str:
    """A one-block human summary, with every unmatched artifact named."""
    if not report.assessable:
        return f"reconcile-audit (CANNOT-ASSESS): {report.reason}"
    lines = [
        f"reconcile-audit ({report.root}): {len(report.explanations)} artifact(s) — "
        f"matched={len(report.matched)}, unmatched={len(report.unmatched)}, "
        f"exempt={len(report.exempt)}"
    ]
    for item in report.explanations:
        detail = ""
        if item.matched:
            detail = "  [" + ", ".join(item.evidence) + "]"
        lines.append(f"  {item.disposition:<9} {item.artifact}{detail}")
        if not item.matched:
            lines.append(f"      {item.reason}")
    return "\n".join(lines)
