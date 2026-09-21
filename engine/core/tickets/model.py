"""Tenant-facing agentic task manager — domain model (issue #634, workbook-3).

The gap this closes (measured, see the issue): ticket tooling already exists
at *repo-governance* level (``governance/board``, ``governance/ticket``,
``governance/dispatch``) and decomposition primitives already exist
(``engine/multiagent/planner.py``, ``fleet/brain.py``) — but nothing joins
them into a **tenant-facing** task lifecycle that the durable engine core can
host.  This package is that join, and nothing more: it owns the lifecycle
vocabulary and reads the decomposition/execution result *out of the durable
transcript*, so it never reimplements the planner or the event store.

A ticket is a tenant-scoped unit of work.  Its lifecycle is closed and
strictly ordered (no skipping, no going back)::

    CREATED -> DECOMPOSED -> DISPATCHED -> EXECUTED -> REVIEWED -> CLOSED

Those six states are *derived from the event log* — never stored beside it —
exactly like the workflow projection in :mod:`core.workflow`.  Two events
carry the product semantics:

* ``ENGINEER_TASK_CREATED`` / ``ENGINEER_TASK_CLOSED`` — the existing
  :class:`core.model.EventKind` members the durable engine already ships
  (they were declared for exactly this surface and were unused until now).
* ``plan_decomposed`` / ``ticket_dispatched`` / ``ticket_executed`` /
  ``ticket_reviewed`` — the lifecycle events this module appends, carrying
  the *replayable* inputs (the decomposition plan and the injected review
  verdict) so a projection rebuilt from the log is byte-for-byte equivalent
  to the live object.

Every payload carries the tenant scope (``tenant`` + ``namespace_id``) and ``
WorkflowExecution.from_events`` refuses a log that changes the tenant
mid-flight, so a ticket can never leak across tenants.

---knowledge---
module_id: engine.core.tickets.model
system: engine
app: core
solution_class: enterprise
patterns: [domain-model, lifecycle-state-machine, tenant-scoped]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [TicketState, lifecycle_order, legal_moves, TicketLifecycleError, next_ticket_state, DecompositionOutcome, TicketReview, TicketProjection]
invariants: "the ticket lifecycle order is declared once; an out-of-order transition is refused"
gotchas: ""
related: ["#634"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple

# -- event kinds -----------------------------------------------------------
#
# The two ENGINEER_TASK_* kinds are `core.model.EventKind` members.  They are
# imported lazily-free (EventKind is already an enum member there) so this
# module stays a plain data module.

ENGINEER_TASK_CREATED = "engineer_task_created"
ENGINEER_TASK_CLOSED = "engineer_task_closed"
PLAN_DECOMPOSED = "plan_decomposed"
TICKET_DISPATCHED = "ticket_dispatched"
TICKET_EXECUTED = "ticket_executed"
TICKET_REVIEWED = "ticket_reviewed"


class TicketState(str, Enum):
    """Closed tenant-ticket lifecycle vocabulary.

    The order of the members *is* the lifecycle order; :meth:`legal_moves`
    exposes exactly one next state per state (plus CLOSED as the terminal
    state with no successors).  There is no path that skips a state and no
    path that revisits one (no-false-green: a ticket is never "reviewed"
    before it was "executed").
    """

    CREATED = "created"
    DECOMPOSED = "decomposed"
    DISPATCHED = "dispatched"
    EXECUTED = "executed"
    REVIEWED = "reviewed"
    CLOSED = "closed"

    @property
    def terminal(self) -> bool:
        return self is TicketState.CLOSED

    @property
    def index(self) -> int:
        return _LIFECYCLE.index(self)


#: The lifecycle order, as a tuple (single source of truth for ordering).
_LIFECYCLE: Tuple[TicketState, ...] = (
    TicketState.CREATED,
    TicketState.DECOMPOSED,
    TicketState.DISPATCHED,
    TicketState.EXECUTED,
    TicketState.REVIEWED,
    TicketState.CLOSED,
)

#: event kind -> (state it requires, state it produces)
TICKET_EVENT_TRANSITIONS: Mapping[str, Tuple[TicketState, TicketState]] = {
    ENGINEER_TASK_CREATED: (TicketState.CREATED, TicketState.CREATED),
    PLAN_DECOMPOSED: (TicketState.CREATED, TicketState.DECOMPOSED),
    TICKET_DISPATCHED: (TicketState.DECOMPOSED, TicketState.DISPATCHED),
    TICKET_EXECUTED: (TicketState.DISPATCHED, TicketState.EXECUTED),
    TICKET_REVIEWED: (TicketState.EXECUTED, TicketState.REVIEWED),
    ENGINEER_TASK_CLOSED: (TicketState.REVIEWED, TicketState.CLOSED),
}

#: The (event kind, from-state, to-state) triples the two workflow-level
#: anchor events also move the *engine* state machine through — used to
#: project a ticket onto its workflow status without re-deciding the mapping.
ANCHOR_WORKFLOW_EFFECT: Mapping[str, Tuple[Optional[str], Optional[str]]] = {
    ENGINEER_TASK_CREATED: (None, "running"),
    ENGINEER_TASK_CLOSED: ("running", "succeeded"),
}


def lifecycle_order() -> Tuple[TicketState, ...]:
    """The lifecycle order (CREATED first, CLOSED last)."""
    return _LIFECYCLE


def legal_moves(state: TicketState) -> Tuple[TicketState, ...]:
    """The state(s) reachable from ``state`` (empty at CLOSED)."""
    if state.terminal:
        return ()
    return (_LIFECYCLE[state.index + 1],)


class TicketLifecycleError(ValueError):
    """An event would move a ticket through an illegal lifecycle transition.

    Raised while running *and* while replaying a stored log, so a corrupt or
    hand-edited history is refused rather than silently accepted.
    """


def next_ticket_state(
    state: TicketState, event_kind: str, *, ticket_id: str = ""
) -> TicketState:
    """Project one lifecycle event onto a ticket state (deterministic).

    ``ticket_id`` only decorates the error message; the transition itself is
    a pure function of ``(state, event_kind)`` so replay is deterministic.
    """
    if event_kind not in TICKET_EVENT_TRANSITIONS:
        raise TicketLifecycleError(
            f"unknown ticket event {event_kind!r} for {ticket_id or 'ticket'}"
        )
    required, produced = TICKET_EVENT_TRANSITIONS[event_kind]
    if state is required:
        return produced
    if produced is state:
        # Idempotent re-emission of an event that produces the current state.
        # ``engineer_task_closed`` legitimately arrives twice in a ticket log —
        # once from the ``close`` step (before the engine's terminal event) and
        # once mapped from the engine's own ``workflow_completed`` — and the
        # second is a confirmation, not a transition.
        return state
    if event_kind == ENGINEER_TASK_CREATED:
        # A non-ticket workflow (no ``tickets.*`` steps) still emits
        # ``workflow_started``; that maps to the CREATED anchor and is a
        # confirmation of the initial state, never a transition.
        return state
    if state.terminal:
        raise TicketLifecycleError(
            f"ticket {ticket_id or 'ticket'} is closed and accepts no further "
            f"lifecycle events (refused {event_kind!r})"
        )
    raise TicketLifecycleError(
        f"illegal ticket transition for {ticket_id or 'ticket'}: "
        f"{state.value} + {event_kind!r} (expected state "
        f"{required.value!r} for that event)"
    )


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecompositionOutcome:
    """The bounded decomposition result, projected from the event log.

    This is a *projection* of the planner lane's :class:`HierarchyReport`
    (``engine.multiagent``) — it holds no behaviour of its own, so this
    package never reimplements decomposition.
    """

    mission_id: str
    planner_lane_id: str
    rounds: int  # dispatch rounds actually run (1 + escalations used)
    max_escalation_rounds: int  # the bound the planner ran under
    escalation_count: int
    subtask_count: int
    findings: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)
    failures: Tuple[str, ...] = field(default_factory=tuple)
    escalated: Tuple[str, ...] = field(default_factory=tuple)
    summary: str = ""

    @property
    def exhausted_escalation_budget(self) -> bool:
        """True when the planner used every permitted re-plan and still had a
        required subtask unresolved — the bound stopped the mission."""
        return self.escalation_count >= self.max_escalation_rounds

    @property
    def resolved(self) -> bool:
        """True when every required subtask produced an acceptable finding."""
        return not self.escalated and not self.failures

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "planner_lane_id": self.planner_lane_id,
            "rounds": self.rounds,
            "max_escalation_rounds": self.max_escalation_rounds,
            "escalation_count": self.escalation_count,
            "subtask_count": self.subtask_count,
            "findings": list(self.findings),
            "failures": list(self.failures),
            "escalated": list(self.escalated),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecompositionOutcome":
        return cls(
            mission_id=str(data["mission_id"]),
            planner_lane_id=str(data["planner_lane_id"]),
            rounds=int(data.get("rounds", 0)),
            max_escalation_rounds=int(data.get("max_escalation_rounds", 0)),
            escalation_count=int(data.get("escalation_count", 0)),
            subtask_count=int(data.get("subtask_count", 0)),
            findings=tuple(data.get("findings", ())),
            failures=tuple(data.get("failures", ())),
            escalated=tuple(data.get("escalated", ())),
            summary=str(data.get("summary", "")),
        )


@dataclass(frozen=True)
class TicketReview:
    """An injected review verdict (replayable — it is persisted verbatim).

    ``approved=False`` is the honest negative path: the review still *ran*
    (the ticket reaches REVIEWED) but it does not close the ticket.
    """

    reviewed_by: str
    approved: bool
    rationale: str = ""
    reviewed_at: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "reviewed_by": self.reviewed_by,
            "approved": self.approved,
            "rationale": self.rationale,
            "reviewed_at": self.reviewed_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TicketReview":
        return cls(
            reviewed_by=str(data.get("reviewed_by", "")),
            approved=bool(data.get("approved", False)),
            rationale=str(data.get("rationale", "")),
            reviewed_at=str(data.get("reviewed_at", "")),
        )


@dataclass(frozen=True)
class TicketProjection:
    """The tenant-facing view of one ticket, derived purely from its log."""

    tenant: str
    ticket_id: str
    state: TicketState
    title: str = ""
    correlation_id: str = ""
    decomposition: Optional[DecompositionOutcome] = None
    dispatch: Optional[Dict[str, Any]] = None
    execution: Optional[Dict[str, Any]] = None
    review: Optional[TicketReview] = None
    outcomes: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def closed(self) -> bool:
        return self.state is TicketState.CLOSED

    @property
    def approved(self) -> bool:
        return self.review is not None and self.review.approved

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tenant": self.tenant,
            "ticket_id": self.ticket_id,
            "state": self.state.value,
            "title": self.title,
            "correlation_id": self.correlation_id,
            "decomposition": (
                self.decomposition.as_dict() if self.decomposition else None
            ),
            "dispatch": dict(self.dispatch) if self.dispatch else None,
            "execution": dict(self.execution) if self.execution else None,
            "review": self.review.as_dict() if self.review else None,
            "outcomes": [dict(item) for item in self.outcomes],
        }

    def lifecycle(self) -> Tuple[str, ...]:
        """The lifecycle states the ticket has *reached*, in order."""
        return tuple(
            state.value for state in _LIFECYCLE if state.index <= self.state.index
        )


__all__ = [
    "DecompositionOutcome",
    "TicketLifecycleError",
    "TicketProjection",
    "TicketReview",
    "TicketState",
    "ANCHOR_WORKFLOW_EFFECT",
    "ENGINEER_TASK_CLOSED",
    "ENGINEER_TASK_CREATED",
    "PLAN_DECOMPOSED",
    "TICKET_DISPATCHED",
    "TICKET_EVENT_TRANSITIONS",
    "TICKET_EXECUTED",
    "TICKET_REVIEWED",
    "legal_moves",
    "lifecycle_order",
    "next_ticket_state",
]
