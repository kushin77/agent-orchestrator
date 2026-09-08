"""engine/queue.model — data shapes for the job queue.

``Task`` is the durable unit of work; ``TaskSpec`` describes a task at enqueue
time; ``AuditEntry`` is one append-only ledger row recording a lifecycle
transition (every transition, issue #22 acceptance). ``Priority`` orders
claim selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Union

from engine.queue.state import TaskState


class Priority(str, Enum):
    """Delivery priority — claim selection prefers HIGH, then age."""

    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value

    @property
    def rank(self) -> int:
        """Selection weight used by the queue authority."""
        return {"HIGH": 2, "NORMAL": 1, "LOW": 0}[self.value]


def coerce_priority(value: Union[str, Priority]) -> Priority:
    """Normalize a ``Priority`` or its name to a :class:`Priority`."""
    if isinstance(value, Priority):
        return value
    try:
        return Priority[str(value).upper()]
    except KeyError as exc:
        raise ValueError(f"unknown priority: {value!r}") from exc


@dataclass(frozen=True)
class TaskSpec:
    """Everything needed to enqueue one task (issue #22, ``enqueue``)."""

    task_id: Optional[str] = None  # generated when omitted
    tenant: str = "default"
    priority: Union[str, Priority] = Priority.NORMAL
    payload: Any = None  # JSON-serializable
    idempotency_key: Optional[str] = None  # at-least-once dedup (no dup effects)


@dataclass
class Task:
    """A durable unit of work flowing through the lifecycle state machine."""

    task_id: str
    tenant: str = "default"
    priority: Priority = Priority.NORMAL
    payload: Any = None
    status: TaskState = TaskState.PENDING
    attempts: int = 0
    idempotency_key: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    lease_agent: Optional[str] = None
    lease_until: Optional[float] = None
    last_error: Optional[str] = None


@dataclass(frozen=True)
class AuditEntry:
    """One append-only ledger row. ``seq`` is strictly monotonic."""

    seq: int
    task_id: str
    from_state: Optional[TaskState]  # None on enqueue
    to_state: TaskState
    agent: Optional[str]
    ts: float
    reason: Optional[str] = None
