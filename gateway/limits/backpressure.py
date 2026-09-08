"""Backpressure when a tenant budget is exhausted.

Doctrine (carried from the cannibalized fleet backpressure controller and the
token-budget "never fail open" lesson): when an enforce-mode budget is
exhausted the call is NEVER served silently.  The controller returns an
explicit decision — the request is either queued for later (strategy=queue,
bounded) or degraded (strategy=degrade, no live provider call) — with a
non-null reason and a ``succeeded=False`` flag a caller can test.  There is no
code path that fabricates a success out of a block.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass

QUEUE = "queue"
DEGRADE = "degrade"

STRATEGIES = frozenset({QUEUE, DEGRADE})


@dataclass(frozen=True)
class QueueJob:
    """One backpressured request parked in the bounded queue."""

    job_id: str
    tenant: str
    agent: str
    model_tier: str
    task_type: str | None
    request_id: str
    enqueued_at: float


class BackpressureQueue:
    """Bounded FIFO of backpressured jobs (in-process seam)."""

    def __init__(self, capacity: int = 1000) -> None:
        self.capacity = int(capacity)
        if self.capacity <= 0:
            raise ValueError("queue capacity must be positive")
        self._jobs: deque[QueueJob] = deque()

    def enqueue(self, job: QueueJob) -> bool:
        if len(self._jobs) >= self.capacity:
            return False
        self._jobs.append(job)
        return True

    def __len__(self) -> int:
        return len(self._jobs)


@dataclass(frozen=True)
class BackpressureDecision:
    """Explicit outcome of handling an exhausted budget.

    ``action`` is one of queued|degraded; ``reason`` is always non-null;
    ``succeeded`` is always False so no caller can mistake the block for a
    served call.
    """

    action: str  # queued | degraded
    reason: str
    queued: bool = False
    degraded: bool = False
    job_id: str | None = None
    message: str = ""

    @property
    def succeeded(self) -> bool:
        """Backpressure decisions NEVER succeed; this is always False."""
        return False


class BackpressureController:
    """Turns a budget-exhausted condition into an explicit queue/degrade choice.

    strategy=queue: enqueue the request (bounded); when the queue is full it
    degrades instead of dropping silently or fabricating success.
    strategy=degrade: refuse the live call and return the explicit degrade
    decision (the caller may serve a degraded/cached fallback, but never a
    pretend provider success).
    """

    def __init__(
        self,
        strategy: str = DEGRADE,
        queue: BackpressureQueue | None = None,
        *,
        clock=time.time,
    ) -> None:
        strategy = str(strategy).lower()
        if strategy not in STRATEGIES:
            raise ValueError(f"strategy must be one of {sorted(STRATEGIES)}")
        self.strategy = strategy
        self.queue = queue if queue is not None else BackpressureQueue()
        self.clock = clock

    def handle(
        self,
        *,
        tenant: str,
        agent: str,
        model_tier: str,
        task_type: str | None = None,
        request_id: str = "",
        reason: str = "budget_exceeded",
    ) -> BackpressureDecision:
        """Produce the explicit backpressure decision for an exhausted budget."""
        if self.strategy == QUEUE:
            job = QueueJob(
                job_id=uuid.uuid4().hex,
                tenant=tenant,
                agent=agent,
                model_tier=model_tier,
                task_type=task_type,
                request_id=request_id,
                enqueued_at=self.clock(),
            )
            if self.queue.enqueue(job):
                return BackpressureDecision(
                    action=QUEUE,
                    reason=reason,
                    queued=True,
                    job_id=job.job_id,
                    message=f"queued {job.job_id} (budget exhausted)",
                )
            return BackpressureDecision(
                action=DEGRADE,
                reason=reason,
                degraded=True,
                message="backpressure queue full; degrading",
            )
        return BackpressureDecision(
            action=DEGRADE,
            reason=reason,
            degraded=True,
            message="degraded (budget exhausted)",
        )
