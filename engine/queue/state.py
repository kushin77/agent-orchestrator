"""engine/queue.state — the task lifecycle state machine.

Issue #22 lifecycle::

    PENDING ──claim──▶ CLAIMED(lease) ──start──▶ RUNNING ──ack──▶ SUCCEEDED
        ▲                  │  ▲                       │
        │   reap/retry     │  │  renew(heartbeat)     │  fail / reap
        └──────────────────┘  │                       ▼
                              │                 FAILED | DEAD   (dead-letter)
                              └── lease expiry ──▶ reap ──▶ PENDING (attempts+1)
                                                          or DEAD (attempts spent)

Terminal states are ``SUCCEEDED`` (acked), ``FAILED`` (worker-reported failure
exhausted its attempt budget) and ``DEAD`` (a claim whose lease expired past
its attempt budget — an orphaned/reaped task). ``FAILED`` and ``DEAD`` are the
dead-letter buckets; ``replay()`` returns them to ``PENDING`` with the attempt
budget reset. Every arrow above is a legal transition enforced centrally.
"""

from __future__ import annotations

from enum import Enum
from typing import AbstractSet, FrozenSet

# Guards against typos in the transition table below.


class TaskState(str, Enum):
    """Lifecycle states of a queued task (issue #22)."""

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DEAD = "DEAD"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


#: States in which a worker holds an unexpired lease.
LEASE_STATES: FrozenSet[TaskState] = frozenset(
    {TaskState.CLAIMED, TaskState.RUNNING}
)

#: Open (not terminal) states.
OPEN_STATES: FrozenSet[TaskState] = frozenset(
    {TaskState.PENDING, TaskState.CLAIMED, TaskState.RUNNING}
)

#: Terminal states — nothing may claim or transition out of them except replay.
TERMINAL_STATES: FrozenSet[TaskState] = frozenset(
    {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.DEAD}
)

#: Dead-letter buckets (replayable back to PENDING).
DEAD_LETTER_STATES: FrozenSet[TaskState] = frozenset(
    {TaskState.FAILED, TaskState.DEAD}
)

#: Legal state transitions. The queue authority rejects anything else.
VALID_TRANSITIONS: dict[TaskState, AbstractSet[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.CLAIMED}),
    TaskState.CLAIMED: frozenset(
        {
            TaskState.RUNNING,  # start
            TaskState.SUCCEEDED,  # ack before start (allowed, degenerate)
            TaskState.FAILED,  # worker-reported fail (attempt spent)
            TaskState.PENDING,  # reap of an expired lease (attempt spent)
            TaskState.DEAD,  # reap past the attempt budget
        }
    ),
    TaskState.RUNNING: frozenset(
        {
            TaskState.SUCCEEDED,  # ack
            TaskState.FAILED,  # worker-reported fail
            TaskState.PENDING,  # reap of an expired lease
            TaskState.DEAD,  # reap past the attempt budget
        }
    ),
    TaskState.SUCCEEDED: frozenset(),
    TaskState.FAILED: frozenset({TaskState.PENDING}),  # replay
    TaskState.DEAD: frozenset({TaskState.PENDING}),  # replay
}


def transition_allowed(source: TaskState, target: TaskState) -> bool:
    """Return whether ``source -> target`` is a legal lifecycle transition."""
    return target in VALID_TRANSITIONS.get(source, frozenset())


def assert_transition(source: TaskState, target: TaskState) -> None:
    """Raise :class:`ValueError` when ``source -> target`` is not legal."""
    if not transition_allowed(source, target):
        raise ValueError(
            f"illegal task transition: {source.value} -> {target.value}"
        )
