"""Deterministic workflow-level state machine.

The workflow lifecycle is a pure function of (current status, event): the
same log always projects to the same status (deterministic replay).  The
table below is the *only* legal set of workflow-level moves; step-level and
compensation events do not change workflow status (the workflow stays
RUNNING while steps execute or compensate).

    PENDING  --workflow_started----------> RUNNING
    RUNNING  --workflow_completed--------> SUCCEEDED   (terminal)
    RUNNING  --workflow_failed-----------> FAILED      (terminal)
    RUNNING  --workflow_rolled_back------> ROLLED_BACK (terminal)

Terminal statuses accept no further workflow-level events.  :func:`apply`
raises :class:`InvalidTransitionError` for any other move, both while the
engine runs and while it replays a stored log — so a corrupt or hand-edited
history is refused, never silently accepted (no-false-green discipline).
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Tuple

from .errors import InvalidTransitionError
from .model import EventKind, WorkflowStatus

# status -> event kinds that may advance it, with their target status.
_WORKFLOW_TRANSITIONS: Dict[WorkflowStatus, Dict[EventKind, WorkflowStatus]] = {
    WorkflowStatus.PENDING: {
        EventKind.WORKFLOW_STARTED: WorkflowStatus.RUNNING,
    },
    WorkflowStatus.RUNNING: {
        EventKind.WORKFLOW_COMPLETED: WorkflowStatus.SUCCEEDED,
        EventKind.WORKFLOW_FAILED: WorkflowStatus.FAILED,
        EventKind.WORKFLOW_ROLLED_BACK: WorkflowStatus.ROLLED_BACK,
    },
}

# Workflow-level events that only move a workflow forward (never backward).
_WORKFLOW_EVENT_KINDS: FrozenSet[EventKind] = frozenset(
    {
        EventKind.WORKFLOW_STARTED,
        EventKind.WORKFLOW_COMPLETED,
        EventKind.WORKFLOW_FAILED,
        EventKind.WORKFLOW_ROLLED_BACK,
    }
)


def next_status(status: WorkflowStatus, event_kind: EventKind) -> WorkflowStatus:
    """Target status for a legal (status, event) pair.

    Raises :class:`InvalidTransitionError` if the move is illegal.
    """
    targets = _WORKFLOW_TRANSITIONS.get(status)
    if targets is None:
        raise InvalidTransitionError(
            f"{status.value} is terminal and accepts no further workflow events"
        )
    target = targets.get(event_kind)
    if target is None:
        raise InvalidTransitionError(
            f"illegal transition: {status.value} + {event_kind.value}"
        )
    return target


def is_workflow_event(event_kind: EventKind) -> bool:
    """True when the event advances the workflow-level state machine."""
    return event_kind in _WORKFLOW_EVENT_KINDS


def legal_moves(status: WorkflowStatus) -> Tuple[Tuple[EventKind, WorkflowStatus], ...]:
    """Expose the legal moves for a status (used by docs and tests)."""
    targets = _WORKFLOW_TRANSITIONS.get(status, {})
    return tuple(sorted(targets.items(), key=lambda item: item[0].value))
