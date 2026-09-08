"""engine/queue.backpressure — bounded per-tenant queues.

Issue #22 acceptance: *backpressure + bounded queues per tenant*. Each tenant
may hold at most ``per_tenant_max_pending`` open tasks (PENDING + CLAIMED +
RUNNING). Beyond that bound the authority refuses new work with
:class:`BackpressureError` rather than unboundedly growing memory/disk — the
queue is bounded, so a runaway producer cannot starve the workers or hide a
sick tenant behind an ever-growing backlog.
"""

from __future__ import annotations

from typing import Mapping

from engine.queue.model import Task
from engine.queue.state import OPEN_STATES


class BackpressureError(Exception):
    """Raised when a tenant's open-task budget would be exceeded."""


def open_count(tasks: Mapping[str, Task], tenant: str | None = None) -> int:
    """Count open (non-terminal) tasks, optionally filtered by tenant."""
    if tenant is None:
        return sum(1 for t in tasks.values() if t.status in OPEN_STATES)
    return sum(
        1
        for t in tasks.values()
        if t.tenant == tenant and t.status in OPEN_STATES
    )


def would_exceed_budget(
    tasks: Mapping[str, Task], tenant: str, budget: int
) -> bool:
    """Whether admitting one more open task for ``tenant`` exceeds ``budget``."""
    return open_count(tasks, tenant) >= budget
