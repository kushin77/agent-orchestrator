#!/usr/bin/env python3
"""Merge-governance state machine + merge policy (EPIC-00 issue #43, phase 8).

Productizes the fleet merge doctrine into an offline, deterministic
PR-lifecycle state machine for the control-plane governance surface
(``governance/merge``):

    opened -> verify-gate -> sme-review-assigned -> approved/verified
                                                 -> mergeable | blocked

A PR may reach ``mergeable`` only when ALL of the following hold —
``merge_verdict()`` is the single source of truth for the rule:

1. **Verify gate green with a commit-named attestation.** The injected verify
   check returned OK (exit 0) and the attestation names a concrete commit.
   A gate reporting NOT-OK or CANNOT-ASSESS is never green, and a green check
   that cannot name a commit cannot make a PR mergeable. This is parity with
   ``scripts/merge-gate.sh`` run semantics (issue #29), which refuses a dirty
   working tree because "an attestation names a COMMIT", and with the honesty
   tri-state (issue #28): CANNOT-ASSESS is never a pass (no-false-green,
   AO-GR-4).

2. **Independent SME review assigned and approving.** An independent reviewer
   persona (reviewer posture, distinct from the author/executor) was assigned
   and returned an approve verdict (issue #11 ``assign_reviewer`` semantics).
   The reviewer is never the author — separation of duties (AO-GR-14).

3. **No-self-merge holds.** The merger is not the PR author, OR the owner
   autonomous-merge carve-out (owner mandate 2026-09-07) is active. The
   carve-out replaces the human reviewer — never the evidence: it still
   requires the green verification evidence to be recorded (AO-GR-11,
   GOVERNANCE.md §3).

Reviewer verdicts are recorded as *evidence*, never as the final authority: a
reviewer approve does not make a PR mergeable while the verify gate is red —
the decisive check is the gate's actual output (independent verification,
AO-GR-13). A blocked PR is terminal; remediation re-enters the lifecycle
through a fresh verify-gate on a new fix commit (AO-GR-11: "a red gate blocks
the merge and the next dispatch wave").

The machine never authors a change — it only decides (AO-GR-12: the control
plane decides and directs; it never does the work it orchestrates). Runtime
signals (verify check, reviewer assignment) are injected, so this module is
pure stdlib and runs fully offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple


# --------------------------------------------------------------------------- #
# Lifecycle vocabulary
# --------------------------------------------------------------------------- #


class PrState(str, Enum):
    """PR lifecycle states (issue #43 acceptance chain).

    ``opened -> verify-gate -> sme-review-assigned -> approved/verified ->
    mergeable | blocked``. ``mergeable`` and ``blocked`` are terminal.
    """

    OPENED = "opened"
    VERIFY_GATE = "verify-gate"
    SME_REVIEW_ASSIGNED = "sme-review-assigned"
    APPROVED_VERIFIED = "approved/verified"
    MERGEABLE = "mergeable"
    BLOCKED = "blocked"


class BlockReason(str, Enum):
    """Closed vocabulary of reasons a PR is blocked (audit-friendly)."""

    VERIFY_NOT_GREEN = "verify-gate-not-green"
    VERIFY_CANNOT_ASSESS = "verify-gate-cannot-assess"
    VERIFY_NO_COMMIT = "verify-gate-no-commit-attestation"
    REVIEWER_NOT_ASSIGNED = "sme-reviewer-not-assigned"
    SELF_REVIEW = "sme-reviewer-is-author"
    REVIEW_REJECTED = "sme-review-rejected"
    SELF_MERGE_WITHOUT_CARVE_OUT = "self-merge-without-owner-carve-out"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class MergeGovernanceError(Exception):
    """Base error for the merge-governance model."""


class InvalidTransition(MergeGovernanceError):
    """Raised when an event is fired from a state that does not allow it."""


class MergeGovernanceViolation(MergeGovernanceError):
    """Raised when a policy guard is violated by a direct machine call.

    The offline engine translates these into a blocked PR (a governance
    outcome) rather than letting them surface as programming errors.
    """


# --------------------------------------------------------------------------- #
# Merge policy — the pure "when is a merge permitted" rule
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MergeSignals:
    """The signals the merge policy decides on (all recorded on the PR)."""

    verify_green: bool
    reviewer_assigned: bool
    reviewer_distinct: bool
    reviewer_approved: bool
    author: str
    merger: str
    owner_carve_out: bool


def merge_verdict(signals: MergeSignals) -> Tuple[bool, List[str]]:
    """Decide whether a merge is permitted and why (not).

    Returns ``(permitted, reasons)`` where ``reasons`` is the ordered list of
    unmet conditions (empty when permitted). Every condition is mechanical:
    green verification evidence + independent reviewer + no-self-merge (with
    the owner autonomous-merge carve-out as the only exception).
    """
    reasons: List[str] = []
    if not signals.verify_green:
        reasons.append(BlockReason.VERIFY_NOT_GREEN.value)
    if not signals.reviewer_assigned:
        reasons.append(BlockReason.REVIEWER_NOT_ASSIGNED.value)
    elif not signals.reviewer_distinct:
        reasons.append(BlockReason.SELF_REVIEW.value)
    elif not signals.reviewer_approved:
        reasons.append(BlockReason.REVIEW_REJECTED.value)
    if signals.merger == signals.author and not signals.owner_carve_out:
        reasons.append(BlockReason.SELF_MERGE_WITHOUT_CARVE_OUT.value)
    return (not reasons, reasons)


# --------------------------------------------------------------------------- #
# PR record + audit
# --------------------------------------------------------------------------- #


@dataclass
class MergePr:
    """The stateful record the machine drives through its lifecycle."""

    number: int
    title: str
    author: str
    author_tenant: str
    branch: str
    subject: str = ""
    base: str = "master"
    state: PrState = PrState.OPENED
    verify_rc: Optional[int] = None
    verify_commit: Optional[str] = None
    verify_evidence: str = ""
    reviewer_id: Optional[str] = None
    reviewer_posture: Optional[str] = None
    reviewer_tenant: str = "platform"
    reviewer_approved: bool = False
    block_reason: Optional[str] = None
    merge_evidence_commit: Optional[str] = None
    audit: List["AuditEntry"] = field(default_factory=list)

    @property
    def is_mergeable(self) -> bool:
        return self.state is PrState.MERGEABLE

    @property
    def is_blocked(self) -> bool:
        return self.state is PrState.BLOCKED


@dataclass(frozen=True)
class AuditEntry:
    """One append-only record of a merge-governance decision."""

    seq: int
    action: str
    detail: str
    state: str
    commit: Optional[str] = None
    at: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "seq": self.seq,
            "action": self.action,
            "detail": self.detail,
            "state": self.state,
            "commit": self.commit,
            "at": self.at,
        }


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def status_from_exit_code(rc: int) -> str:
    """Honesty tri-state from a gate exit code (issue #28 semantics).

    0 -> OK, 1 -> NOT-OK, anything else (e.g. 2 CANNOT-ASSESS, 124 timeout)
    -> CANNOT-ASSESS. CANNOT-ASSESS is never a pass.
    """
    if rc == 0:
        return "OK"
    if rc == 1:
        return "NOT-OK"
    return "CANNOT-ASSESS"


# --------------------------------------------------------------------------- #
# The state machine
# --------------------------------------------------------------------------- #


class MergeGovernanceMachine:
    """Event-driven PR state machine with guarded transitions.

    Allowed-event table (``_ALLOWED``) makes invalid transitions raise
    :class:`InvalidTransition`; policy guards raise
    :class:`MergeGovernanceViolation` (translated to a blocked PR by the
    engine) or record a blocked state directly (``block``).
    """

    # event -> states from which the event may fire
    _ALLOWED: Dict[str, Set[PrState]] = {
        "begin_verify": {PrState.OPENED},
        "record_verify": {PrState.VERIFY_GATE},
        "assign_reviewer": {
            PrState.OPENED,
            PrState.VERIFY_GATE,
            PrState.SME_REVIEW_ASSIGNED,
        },
        "approve_review": {PrState.SME_REVIEW_ASSIGNED},
        "reject_review": {PrState.SME_REVIEW_ASSIGNED},
        "conclude_merge": {PrState.APPROVED_VERIFIED},
        "block": {
            PrState.OPENED,
            PrState.VERIFY_GATE,
            PrState.SME_REVIEW_ASSIGNED,
            PrState.APPROVED_VERIFIED,
        },
        "retry": {PrState.BLOCKED},
    }

    def __init__(self, pr: MergePr) -> None:
        self.pr = pr
        self._seq = 0

    # -- audit ----------------------------------------------------------------

    def _log(
        self, action: str, detail: str, commit: Optional[str] = None
    ) -> None:
        self._seq += 1
        self.pr.audit.append(
            AuditEntry(
                seq=self._seq,
                action=action,
                detail=detail,
                state=self.pr.state.value,
                commit=commit,
                at=_utcnow(),
            )
        )

    def _check(self, event: str) -> None:
        if self.pr.state not in self._ALLOWED[event]:
            raise InvalidTransition(
                f"event {event!r} not allowed from state {self.pr.state.value!r}"
            )

    # -- events ---------------------------------------------------------------

    def begin_verify(self) -> None:
        """OPENED -> VERIFY_GATE: the injected verify check starts running."""
        self._check("begin_verify")
        self.pr.state = PrState.VERIFY_GATE
        self._log("verify-start", f"verify gate started for PR #{self.pr.number}")

    def record_verify(
        self,
        rc: int,
        commit: Optional[str],
        evidence: str = "",
    ) -> None:
        """Record the injected verify-gate outcome (issue #28 tri-state).

        Only an OK result that names a commit is green; NOT-OK and
        CANNOT-ASSESS results block the PR, and an OK result without a commit
        (no attestation) blocks it too — an attestation names a COMMIT.
        """
        self._check("record_verify")
        status = status_from_exit_code(rc)
        self.pr.verify_rc = rc
        self.pr.verify_commit = commit
        self.pr.verify_evidence = evidence
        green = status == "OK" and commit is not None
        if green:
            self.pr.state = PrState.SME_REVIEW_ASSIGNED
            self._log(
                "verify-pass",
                f"verify gate green (status={status}, evidence={evidence or 'none'})",
                commit=commit,
            )
            return
        if status == "CANNOT-ASSESS":
            reason = BlockReason.VERIFY_CANNOT_ASSESS
        elif status == "NOT-OK":
            reason = BlockReason.VERIFY_NOT_GREEN
        else:  # OK but no commit-named attestation
            reason = BlockReason.VERIFY_NO_COMMIT
        detail = (
            f"verify gate not green (status={status}, commit={commit or 'none'}"
            f", evidence={evidence or 'none'})"
        )
        self._block(reason, detail)

    def assign_reviewer(
        self,
        *,
        persona_id: str,
        posture: str,
        reviewer_tenant: str = "platform",
    ) -> None:
        """Assign the independent SME reviewer (issue #11 semantics).

        Only reviewer-posture personas review; the reviewer is never the
        author/executor of the same PR (separation of duties). A violation
        raises :class:`MergeGovernanceViolation` — the engine turns it into a
        blocked PR with reason ``sme-reviewer-is-author``.
        """
        self._check("assign_reviewer")
        if posture != "reviewer":
            raise MergeGovernanceViolation(
                f"persona {persona_id!r} has posture {posture!r}; only "
                "reviewer-posture personas may act as the independent reviewer"
            )
        if persona_id == self.pr.author:
            raise MergeGovernanceViolation(
                f"reviewer persona {persona_id!r} is the author of the same PR "
                "(separation of duties)"
            )
        self.pr.reviewer_id = persona_id
        self.pr.reviewer_posture = posture
        self.pr.reviewer_tenant = reviewer_tenant
        self._log(
            "reviewer-assigned",
            f"independent SME reviewer assigned: {reviewer_tenant}/{persona_id}",
        )

    def approve_review(self) -> None:
        """SME_REVIEW_ASSIGNED -> APPROVED_VERIFIED on reviewer approval."""
        self._check("approve_review")
        if self.pr.reviewer_id is None:
            raise MergeGovernanceViolation(
                "cannot approve review before an independent reviewer is assigned"
            )
        self.pr.reviewer_approved = True
        self.pr.state = PrState.APPROVED_VERIFIED
        self._log(
            "review-approve",
            f"independent SME reviewer {self.pr.reviewer_id!r} approved",
        )

    def reject_review(self, detail: str = "") -> None:
        """SME_REVIEW_ASSIGNED -> BLOCKED when the reviewer rejects."""
        self._check("reject_review")
        self._block(
            BlockReason.REVIEW_REJECTED,
            detail or f"reviewer {self.pr.reviewer_id or 'unassigned'!r} rejected",
        )

    def conclude_merge(self, *, merger: str, owner_carve_out: bool) -> None:
        """APPROVED_VERIFIED -> MERGEABLE | BLOCKED (the policy decision).

        Re-evaluates the full merge policy from the recorded signals rather
        than trusting the path taken (defense in depth): a PR only becomes
        mergeable when the verify evidence is green, the independent reviewer
        is assigned/approving, and no-self-merge holds (or the owner
        autonomous-merge carve-out applies).
        """
        self._check("conclude_merge")
        signals = MergeSignals(
            verify_green=self.pr.verify_rc == 0 and self.pr.verify_commit is not None,
            reviewer_assigned=self.pr.reviewer_id is not None,
            reviewer_distinct=self.pr.reviewer_id != self.pr.author,
            reviewer_approved=self.pr.reviewer_approved,
            author=self.pr.author,
            merger=merger,
            owner_carve_out=owner_carve_out,
        )
        permitted, reasons = merge_verdict(signals)
        if not permitted:
            # merge_verdict reports only unmet conditions; first reason wins
            first = reasons[0] if reasons else BlockReason.VERIFY_NOT_GREEN.value
            self._block(
                BlockReason(first),
                f"merge denied for merger={merger!r}: {'; '.join(reasons)}",
            )
            return
        self.pr.merge_evidence_commit = self.pr.verify_commit
        self.pr.state = PrState.MERGEABLE
        self._log(
            "merge-permitted",
            f"PR #{(self.pr.number)} mergeable — green verification "
            f"(commit={self.pr.verify_commit}) + independent review "
            f"({self.pr.reviewer_id}) for merger={merger!r}",
            commit=self.pr.verify_commit,
        )

    def block(self, reason: BlockReason, detail: str = "") -> None:
        """Explicitly block the PR from any active state."""
        self._check("block")
        self._block(reason, detail or f"blocked: {reason.value}")

    def retry(self, *, fix_commit: str) -> None:
        """BLOCKED -> VERIFY_GATE on a genuine fix commit.

        A blocked PR is terminal until a *new* commit exists (never merge
        failing work; a red gate blocks until a real fix). Retrying on the
        same commit is an invalid transition.
        """
        self._check("retry")
        if not fix_commit:
            raise InvalidTransition("retry requires a fix commit (a red gate blocks until a real fix)")
        if fix_commit == self.pr.verify_commit:
            raise InvalidTransition(
                "retry on the same commit is not a fix — a blocked PR needs a new commit"
            )
        self.pr.verify_rc = None
        self.pr.verify_commit = None
        self.pr.verify_evidence = ""
        self.pr.reviewer_id = None
        self.pr.reviewer_approved = False
        self.pr.block_reason = None
        self.pr.state = PrState.VERIFY_GATE
        self._log("retry", f"re-entered verify gate on fix commit {fix_commit}", commit=fix_commit)

    # -- internals -------------------------------------------------------------

    def _block(self, reason: BlockReason, detail: str) -> None:
        self.pr.state = PrState.BLOCKED
        self.pr.block_reason = reason.value
        self._log("block", detail, commit=self.pr.verify_commit)
