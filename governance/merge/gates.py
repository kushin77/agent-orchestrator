#!/usr/bin/env python3
"""Review gates + C-suite escalation on the tenant task lifecycle (issue #635).

---knowledge---
module_id: governance.merge.gates
system: governance
app: merge
solution_class: enterprise
patterns: [honesty-tri-state, declared-authority, fail-closed, injected-effects, bounded-work]
derives_from: null
owner_sme: code-review-sme
tier: L1
interfaces: [EscalationRole, EscalationTrigger, EscalationError, EscalationTerminated, GateBlocked, Escalation, root_escalation, escalation_chain, ReviewGateOutcome, review_gate, (+1 more)]
invariants: ""
gotchas: ""
related: ["#631", "#634", "#635"]
do_not_duplicate: null
---knowledge---

Workbook-4 of the Paperclip enterprise-workbook delta (parent #631).  Workbook-3
(issue #634, ``engine/core/tickets``) shipped the tenant task lifecycle
``created -> decomposed -> dispatched -> executed -> reviewed -> closed`` with a
**review step** that takes an injected verdict and refuses to close a ticket the
verdict did not approve.  Issue #43 (this package) shipped the **merge-verdict
semantics** — green verify named to a commit, independent reviewer, no-self-merge
— but *PR-scoped only*.  This module is the **join**: it consumes those exact
semantics as the *decision procedure for a task's review gate*, so a tenant task
closes only on the same evidence a PR needs to merge, and it declares the
C-suite escalation path a blocked task walks.

Nothing here redefines the rules.  The single source of truth for "may this
advance" stays :func:`model.merge_verdict`; the single source of truth for a
verify result stays :class:`gate.VerifyOutcome`; the single source of truth for
who may review stays :class:`reviewer.PersonaAssigner`.  This module only
*applies* them to a task and *translates* the outcome into the verdict the
lifecycle's review step consumes (:class:`engine.core.tickets.model.TicketReview`).

## The gate (a task, not a PR)

A task's review gate is decided by the same three conditions as a PR merge:

1. **Green verify named to a commit.**  An OK verdict that carries the commit it
   attested.  NOT-OK, CANNOT-ASSESS, or an OK verdict naming no commit closes the
   gate (``gate.is_green``).  This is what makes "a red gate can never yield a
   closed task" true by construction.
2. **Independent reviewer assigned and approving.**  A reviewer-posture persona
   distinct from the task's executor (separation of duties, AO-GR-14).
3. **No-self-merge.**  The party concluding the gate is not the executor, or the
   owner autonomous-merge carve-out is active — which still requires the green
   evidence (the carve-out replaces the human reviewer, never the evidence).

The gate produces a :class:`ReviewGateOutcome`; :meth:`ReviewGateOutcome.step_review`
is the mapping a caller injects as ``step.args['review']`` when building the
ticket workflow (``engine/core/tickets/workflow.py::ticket_workflow(review=...)``).
A closed gate maps to ``approved=False``, which the lifecycle's
``TicketReviewHandler`` records as ``gate_open=False`` and its
``TicketCloseHandler`` **fails closed** on — the task ends at REVIEWED and the
workflow never reports success.

## The escalation path (COO then CEO)

The workbook row is "COO pacing escalation -> CEO board escalation".  A task
whose gate is *blocked* (not merely red) escalates along a declared, finite
chain (:data:`ESCALATION_EDGES`):

    task --blocked--> COO (pacing) --> CEO (board escalation) --> (terminal)

The chain **terminates**: every role has at most one successor, :data:`ESCALATION_DEPTH`
is the length of the longest path, and the terminal role (CEO) accepts no further
escalation — :meth:`Escalation.escalate` raises :class:`EscalationTerminated`
rather than looping.  An escalation stops early when its concern is resolved at a
rung (the COO can pace a blocked lane back to green; only an unresolved block
reaches the CEO), so the chain is bounded by construction, never by a counter a
caller could forget to check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from gate import GateStatus, VerifyOutcome
from model import BlockReason, MergeSignals, merge_verdict

# --------------------------------------------------------------------------- #
# Escalation vocabulary
# --------------------------------------------------------------------------- #


class EscalationRole(str, Enum):
    """The C-suite rungs a blocked task escalates through (the workbook row).

    ``COO`` owns *pacing* — the throughput/finops concern the workbook assigns
    it ("COO pacing"); ``CEO`` owns the *board* escalation, the terminal rung.
    """

    COO = "COO"
    CEO = "CEO"


class EscalationTrigger(str, Enum):
    """Why a rung was entered — the workbook names exactly two triggers."""

    PACING = "pacing"
    BOARD = "board-escalation"


#: The declared escalation edges: ``role -> (successor | None, trigger)``.
#: The chain is deliberately a *line*, not a graph: one successor per rung, so
#: the number of escalations a task can ever make is bounded by the rung count.
ESCALATION_EDGES: Mapping[EscalationRole, Tuple[Optional[EscalationRole], EscalationTrigger]] = {
    EscalationRole.COO: (EscalationRole.CEO, EscalationTrigger.BOARD),
    EscalationRole.CEO: (None, EscalationTrigger.BOARD),
}

#: The rung a freshly-blocked task first reaches (pacing), and the rung that
#: ends the chain.  Declared here so callers never hard-code the names.
ESCALATION_ROOT: EscalationRole = EscalationRole.COO
ESCALATION_TERMINAL: EscalationRole = EscalationRole.CEO

#: The longest path in :data:`ESCALATION_EDGES` — the hard depth bound.  A task
#: can be escalated at most this many times before the chain is exhausted.
ESCALATION_DEPTH: int = 2


class EscalationError(Exception):
    """Base error for the task-escalation surface."""


class EscalationTerminated(EscalationError):
    """Raised when an escalation is attempted past the terminal rung.

    The chain ends at :data:`ESCALATION_TERMINAL`; escalating again is refused
    rather than silently looping.  This is the mechanical proof that "escalation
    terminates" — the terminal rung has no successor by declaration.
    """


class GateBlocked(Exception):
    """Raised when a caller demands a close the evidence does not permit."""

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = tuple(reasons)
        super().__init__(
            "the review gate is closed; the task cannot progress: "
            + "; ".join(self.reasons or ("(no reason recorded)",))
        )


# --------------------------------------------------------------------------- #
# The gate outcome
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Escalation:
    """A recorded escalation: which rung, why, and on whose authority.

    ``depth`` is 1-based (COO = 1, CEO = 2).  :meth:`escalate` returns the
    *next* escalation or raises :class:`EscalationTerminated` at the terminal
    rung, so a consumer cannot walk past the chain's end by accident.
    """

    role: EscalationRole
    trigger: EscalationTrigger
    depth: int
    reason: str = ""
    owner: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role.value,
            "trigger": self.trigger.value,
            "depth": self.depth,
            "reason": self.reason,
            "owner": self.owner,
        }

    def escalate(self, reason: str = "", owner: str = "") -> "Escalation":
        """Walk one declared edge; refuse past the terminal rung.

        The successor and its trigger come from :data:`ESCALATION_EDGES` — never
        from the caller — so the path a task can take is exactly the declared
        one.  At :data:`ESCALATION_TERMINAL` the edge target is ``None`` and an
        :class:`EscalationTerminated` is raised: the chain has a hard end.
        """
        successor, trigger = ESCALATION_EDGES[self.role]
        if successor is None:
            raise EscalationTerminated(
                f"{self.role.value} is the terminal escalation rung "
                f"(depth {self.depth}); the escalation chain has ended"
            )
        return Escalation(
            role=successor,
            trigger=trigger,
            depth=self.depth + 1,
            reason=reason,
            owner=owner,
        )


def root_escalation(reason: str = "", owner: str = "") -> Escalation:
    """The first escalation a blocked task makes (task blocked -> COO pacing)."""
    return Escalation(
        role=ESCALATION_ROOT,
        trigger=EscalationTrigger.PACING,
        depth=1,
        reason=reason,
        owner=owner,
    )


def escalation_chain(reason: str = "", owner: str = "") -> Tuple[Escalation, ...]:
    """The full declared chain from the root to the terminal rung (finite).

    Returned as data so a test (or a dashboard) can assert the *shape* of the
    path without driving it: it always ends at :data:`ESCALATION_TERMINAL`, and
    its length is :data:`ESCALATION_DEPTH` by construction.
    """
    chain: List[Escalation] = [root_escalation(reason=reason, owner=owner)]
    while True:
        try:
            chain.append(chain[-1].escalate(reason=reason, owner=owner))
        except EscalationTerminated:
            break
    return tuple(chain)


@dataclass(frozen=True)
class ReviewGateOutcome:
    """The review-gate decision for one task (the merge policy, task-scoped).

    ``open_gate`` is the single load-bearing bit: it is true only when
    :func:`model.merge_verdict` permits the advance.  ``reasons`` is the ordered
    list of unmet conditions (the ``BlockReason`` values), so a closed gate is
    always *explained* — never a bare ``False``.
    """

    task_id: str
    open_gate: bool
    reasons: Tuple[str, ...] = ()
    reviewer_id: Optional[str] = None
    reviewer_tenant: str = ""
    verify_commit: Optional[str] = None
    verify_status: str = ""
    escalated: Tuple[Escalation, ...] = ()
    audit: Tuple[Dict[str, Any], ...] = ()

    @property
    def blocked(self) -> bool:
        """A closed gate *blocks* the task (the escalation trigger)."""
        return not self.open_gate

    @property
    def escalated_to(self) -> Optional[EscalationRole]:
        """The rung the block escalated to, or ``None`` when the gate was open."""
        return self.escalated[-1].role if self.escalated else None

    def step_review(self) -> Dict[str, Any]:
        """The ``step.args['review']`` mapping the lifecycle review step reads.

        Consumes the workbook-3 contract *as it is*: a mapping with
        ``reviewed_by`` / ``approved`` / ``rationale`` is what
        ``TicketReviewHandler`` accepts, and ``approved=False`` is what makes its
        ``TicketCloseHandler`` fail closed.  The gate therefore never has to
        reach into the engine — it hands the lifecycle the verdict and the
        lifecycle enforces it.
        """
        if self.open_gate:
            rationale = (
                f"review gate OPEN for task {self.task_id}: green verify "
                f"(commit={self.verify_commit}) + independent reviewer "
                f"{self.reviewer_id or '(none recorded)'}"
            )
        else:
            rationale = (
                f"review gate CLOSED for task {self.task_id}: "
                + "; ".join(self.reasons or ("(no reason recorded)",))
            )
        return {
            "reviewed_by": self.reviewer_id or "",
            "approved": self.open_gate,
            "rationale": rationale,
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task_id,
            "gate": "open" if self.open_gate else "closed",
            "reasons": list(self.reasons),
            "reviewer": self.reviewer_id,
            "reviewer_tenant": self.reviewer_tenant,
            "verify_commit": self.verify_commit,
            "verify_status": self.verify_status,
            "escalated_to": (self.escalated_to.value if self.escalated_to else None),
            "escalations": [item.as_dict() for item in self.escalated],
            "audit": [dict(entry) for entry in self.audit],
        }


# --------------------------------------------------------------------------- #
# The gate itself
# --------------------------------------------------------------------------- #


def _audit(
    task_id: str,
    action: str,
    detail: str,
    *,
    commit: Optional[str] = None,
) -> Dict[str, Any]:
    return {"action": action, "task": task_id, "detail": detail, "commit": commit}


def review_gate(
    *,
    task_id: str,
    verify: VerifyOutcome,
    reviewer_id: Optional[str] = None,
    reviewer_tenant: str = "platform",
    reviewer_posture: str = "reviewer",
    reviewer_approved: bool = False,
    executor: str,
    concluder: Optional[str] = None,
    owner_carve_out: bool = True,
    escalate_on_block: bool = True,
    escalation_reason: str = "",
    escalation_owner: str = "",
) -> ReviewGateOutcome:
    """Decide a task's review gate by consuming the merge-verdict semantics.

    This is the whole of the contract, in one pure function:

    * **independent reviewer** — a reviewer must be *named*, must be
      reviewer-posture, and must be **distinct from the executor**; anything
      else is a separation-of-duties failure (``SELF_REVIEW`` /
      ``REVIEWER_NOT_ASSIGNED``), not a silent pass.
    * **green verify named to a commit** — delegated to
      :attr:`gate.VerifyOutcome.is_green`, so a NOT-OK / CANNOT-ASSESS verdict or
      an unattested OK verdict closes the gate.
    * **no-self-merge** — the concluder (default: the executor) may not conclude
      its own task without the owner carve-out.

    The three signals are assembled into the *same* :class:`model.MergeSignals`
    the PR lifecycle uses and handed to the *same* :func:`model.merge_verdict`
    rule — this module adds no fourth condition and re-orders nothing.  A closed
    gate escalates along the declared C-suite chain when
    ``escalate_on_block`` is set; an open gate escalates nowhere.
    """
    if not task_id:
        raise ValueError("task_id must be non-empty")
    if not executor:
        raise ValueError("executor must be non-empty")

    # -- independent reviewer (separation of duties, AO-GR-14) ---------------
    named = bool(reviewer_id)
    distinct = bool(reviewer_id) and reviewer_id != executor
    posture_ok = reviewer_posture == "reviewer"
    assigned = named and posture_ok and distinct

    signals = MergeSignals(
        verify_green=verify.is_green,
        reviewer_assigned=named,
        reviewer_distinct=distinct,
        reviewer_approved=bool(reviewer_approved) and assigned,
        author=executor,
        merger=concluder or executor,
        owner_carve_out=owner_carve_out,
    )
    permitted, reasons = merge_verdict(signals)
    reasons = list(reasons)
    if named and not posture_ok:
        # ``merge_verdict`` only knows "assigned"; the posture class is a
        # reviewer-assignment rule (the ``reviewer`` class is rigid), so it is
        # reported here rather than silently folded into "rejected".
        reasons.append(BlockReason.SELF_REVIEW.value)
    if BlockReason.VERIFY_NOT_GREEN.value in reasons:
        # ``merge_verdict`` reports the *coarse* "not green"; the honesty
        # tri-state distinguishes why (issue #28): an unattested OK verdict, a
        # CANNOT-ASSESS verdict, and a genuine NOT-OK are three different
        # findings a reviewer must be able to tell apart.  The detail comes
        # from ``gate.VerifyOutcome`` itself, never re-derived here.
        if verify.status is not GateStatus.OK:
            detail = (
                BlockReason.VERIFY_CANNOT_ASSESS.value
                if verify.status is GateStatus.CANNOT_ASSESS
                else BlockReason.VERIFY_NOT_GREEN.value
            )
        else:
            detail = BlockReason.VERIFY_NO_COMMIT.value
        index = reasons.index(BlockReason.VERIFY_NOT_GREEN.value)
        reasons[index] = detail
    # A closed gate is always explained; de-duplicate while preserving order so
    # the *first* reason (the decisive one) stays first.
    reasons = list(dict.fromkeys(reasons))

    audit: List[Dict[str, Any]] = []
    commit = verify.commit
    audit.append(
        _audit(
            task_id,
            "verify-recorded",
            f"verify status={verify.status.value} commit={commit or 'none'}",
            commit=commit,
        )
    )
    if assigned:
        audit.append(
            _audit(
                task_id,
                "reviewer-assigned",
                f"independent reviewer {reviewer_tenant}/{reviewer_id} "
                f"(posture={reviewer_posture})",
            )
        )
    else:
        audit.append(
            _audit(
                task_id,
                "reviewer-refused",
                f"reviewer={reviewer_id or 'none'} posture={reviewer_posture} "
                f"executor={executor} (independent reviewer not established)",
            )
        )

    escalations: Tuple[Escalation, ...] = ()
    if permitted:
        audit.append(
            _audit(
                task_id,
                "gate-open",
                f"task may advance: green verify (commit={commit}) + "
                f"independent review ({reviewer_id})",
                commit=commit,
            )
        )
    else:
        audit.append(
            _audit(
                task_id,
                "gate-closed",
                "task may not advance: " + "; ".join(reasons),
                commit=commit,
            )
        )
        if escalate_on_block:
            escalations = escalation_chain(
                reason=escalation_reason or "; ".join(reasons),
                owner=escalation_owner,
            )
            for step in escalations:
                audit.append(
                    _audit(
                        task_id,
                        "escalated",
                        f"escalated to {step.role.value} "
                        f"(trigger={step.trigger.value}, depth={step.depth})",
                    )
                )

    return ReviewGateOutcome(
        task_id=task_id,
        open_gate=permitted,
        reasons=tuple(reasons),
        reviewer_id=reviewer_id if assigned else None,
        reviewer_tenant=reviewer_tenant if assigned else "",
        verify_commit=commit,
        verify_status=verify.status.value,
        escalated=escalations,
        audit=tuple(audit),
    )


def require_open(outcome: ReviewGateOutcome) -> None:
    """Fail closed when a caller treats a closed gate as progress.

    This is the mechanical guard a *consumer* uses: asking to close a task whose
    gate is closed raises :class:`GateBlocked` rather than returning a falsy
    value a careless caller could ignore (the gate is never a formality).
    """
    if not outcome.open_gate:
        raise GateBlocked(outcome.reasons)


__all__ = [
    "ESCALATION_DEPTH",
    "ESCALATION_EDGES",
    "ESCALATION_ROOT",
    "ESCALATION_TERMINAL",
    "Escalation",
    "EscalationError",
    "EscalationRole",
    "EscalationTerminated",
    "EscalationTrigger",
    "GateBlocked",
    "ReviewGateOutcome",
    "escalation_chain",
    "require_open",
    "review_gate",
    "root_escalation",
]
