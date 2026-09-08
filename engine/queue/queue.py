"""engine/queue.queue — the queue authority (single-writer, no split-brain).

Implements the issue-#22 lifecycle on top of a :class:`QueueSnapshot`:

- ``enqueue``   PENDING            (idempotent by ``task_id`` / ``idempotency_key``,
                                    backpressure-bounded per tenant)
- ``claim``     PENDING -> CLAIMED (lease held by one agent; priority + age order)
- ``start``     CLAIMED -> RUNNING
- ``renew``     heartbeat: extend the lease (keeps the claim from being reaped)
- ``ack``       -> SUCCEEDED       (only the lease holder, lease still valid)
- ``fail``      -> PENDING (retry) or FAILED (dead-letter) when attempts spent
- ``reap``      lease-expired CLAIMED/RUNNING -> PENDING (attempts+1) or DEAD
                — the orphan reaper for workers that died mid-task
- ``replay``    FAILED/DEAD -> PENDING (attempts reset) — dead-letter replay

Every mutation runs under the store's exclusive lock and appends an audit
ledger row, so the queue is a genuine single writer: two agents can never
claim the same task (only ``PENDING`` is claimable), and a task is never
recorded SUCCEEDED except by an explicit ``ack`` from its lease holder — a
task whose worker died (``set -e``-style) is reaped to PENDING/DEAD, never
silently marked done (the leaderboard fanout-bug doctrine).
"""

from __future__ import annotations

import copy
import uuid
from typing import Callable, Dict, List, Mapping, Optional, Union

from engine.queue.backpressure import (
    BackpressureError,
    open_count,
    would_exceed_budget,
)
from engine.queue.config import QueueConfig, default_config
from engine.queue.dedup import find_by_idempotency_key
from engine.queue.model import (
    AuditEntry,
    Task,
    TaskSpec,
    coerce_priority,
)
from engine.queue.state import (
    DEAD_LETTER_STATES,
    LEASE_STATES,
    TaskState,
    assert_transition,
)
from engine.queue.store import InMemoryStore, QueueSnapshot, Store


class QueueError(Exception):
    """Base class for queue-authority violations."""


class DuplicateTaskError(QueueError):
    """A conflicting task (same id, different idempotency key) already exists."""


class NoSuchTaskError(QueueError):
    """No task with the given id exists."""


class NotClaimedError(QueueError):
    """Operation requires holding the task's lease (wrong agent / not claimed)."""


class LeaseError(QueueError):
    """The lease has expired; the task must be reaped and reclaimed."""


class NotReplayableError(QueueError):
    """The task is not in a dead-letter state and cannot be replayed."""


Clock = Callable[[], float]


def _time_now() -> float:
    """Wall clock used when the caller does not inject a fake clock."""
    import time

    return time.time()


def _new_task_id() -> str:
    return "t-%s" % uuid.uuid4().hex[:12]


class JobQueue:
    """The single-writer task-queue authority.

    Parameters
    ----------
    store:
        Persistence seam (``InMemoryStore`` or ``FileStore``). Defaults to an
        in-memory store.
    config:
        Tuning knobs (:class:`QueueConfig`). Defaults to ``default_config()``.
    clock:
        Time source (float seconds, monotonic-ish). Inject a fake for tests.
    """

    def __init__(
        self,
        store: Optional[Store] = None,
        config: Optional[QueueConfig] = None,
        clock: Clock = _time_now,
    ) -> None:
        self._store = store if store is not None else InMemoryStore()
        self._cfg = config if config is not None else default_config()
        self._clock = clock

    # -- internal helpers --------------------------------------------------

    def _now(self) -> float:
        return self._clock()

    def _mutate(self, fn: Callable[[QueueSnapshot], object]) -> object:
        """Run ``fn`` on a fresh snapshot under the store's exclusive lock.

        The snapshot is committed only when ``fn`` returns; an exception
        discards the mutation (nothing is written), preserving atomicity.
        """
        with self._store.lock():
            snapshot = self._store.load()
            result = fn(snapshot)
            self._store.save(snapshot)
            return result

    @staticmethod
    def _require_task(snapshot: QueueSnapshot, task_id: str) -> Task:
        task = snapshot.tasks.get(task_id)
        if task is None:
            raise NoSuchTaskError(f"no such task: {task_id}")
        return task

    @staticmethod
    def _expired(task: Task, now: float) -> bool:
        return task.lease_until is not None and task.lease_until < now

    def _record(
        self,
        snapshot: QueueSnapshot,
        task: Task,
        to_state: TaskState,
        agent: Optional[str],
        reason: Optional[str] = None,
    ) -> None:
        """Append an audit row and apply a validated state transition."""
        from_state = task.status
        assert_transition(from_state, to_state)
        snapshot.seq += 1
        snapshot.audit.append(
            AuditEntry(
                seq=snapshot.seq,
                task_id=task.task_id,
                from_state=from_state,
                to_state=to_state,
                agent=agent,
                ts=self._now(),
                reason=reason,
            )
        )
        task.status = to_state
        task.updated_at = self._now()

    @staticmethod
    def _detach(task: Task) -> Task:
        return copy.deepcopy(task)

    # -- lifecycle operations -----------------------------------------------

    def enqueue(self, spec: Union[TaskSpec, Mapping]) -> Task:
        """Enqueue work as PENDING (at-least-once, backpressure-bounded).

        Returns the freshly enqueued task. If ``task_id`` (strong handle) or
        ``idempotency_key`` already exists the existing task is returned
        instead of enqueueing a duplicate — no duplicate side effects.
        """
        if not isinstance(spec, TaskSpec):
            spec = TaskSpec(**dict(spec))
        tenant = spec.tenant or "default"
        priority = coerce_priority(spec.priority)

        def _fn(snapshot: QueueSnapshot) -> Task:
            if spec.task_id and spec.task_id in snapshot.tasks:
                existing = snapshot.tasks[spec.task_id]
                if (
                    spec.idempotency_key
                    and existing.idempotency_key != spec.idempotency_key
                ):
                    raise DuplicateTaskError(
                        f"task id {spec.task_id!r} exists with a different "
                        f"idempotency key"
                    )
                return self._detach(existing)

            if spec.idempotency_key:
                dup = find_by_idempotency_key(
                    snapshot.tasks, spec.idempotency_key
                )
                if dup is not None:
                    return self._detach(dup)

            if would_exceed_budget(
                snapshot.tasks, tenant, self._cfg.per_tenant_max_pending
            ):
                open_now = open_count(snapshot.tasks, tenant)
                raise BackpressureError(
                    f"tenant {tenant!r} at backpressure bound "
                    f"({open_now} >= {self._cfg.per_tenant_max_pending} open); "
                    f"rejecting new task"
                )

            now = self._now()
            task = Task(
                task_id=spec.task_id or _new_task_id(),
                tenant=tenant,
                priority=priority,
                payload=copy.deepcopy(spec.payload),
                status=TaskState.PENDING,
                attempts=0,
                idempotency_key=spec.idempotency_key,
                created_at=now,
                updated_at=now,
            )
            snapshot.tasks[task.task_id] = task
            snapshot.seq += 1
            snapshot.audit.append(
                AuditEntry(
                    seq=snapshot.seq,
                    task_id=task.task_id,
                    from_state=None,
                    to_state=TaskState.PENDING,
                    agent=None,
                    ts=now,
                    reason="enqueue",
                )
            )
            return self._detach(task)

        return self._mutate(_fn)  # type: ignore[return-value]

    def _candidate(
        self, snapshot: QueueSnapshot, tenant: Optional[str]
    ) -> Optional[Task]:
        """Best claimable task: max (priority rank + age bump), then oldest."""
        now = self._now()
        best: Optional[Task] = None
        best_key: Optional[tuple] = None
        for task in snapshot.tasks.values():
            if task.status is not TaskState.PENDING:
                continue
            if task.attempts >= self._cfg.max_attempts:
                continue
            if tenant is not None and task.tenant != tenant:
                continue
            rank = task.priority.rank
            if now - task.created_at > self._cfg.age_bump_seconds:
                rank += 1
            key = (-rank, task.created_at, task.task_id)
            if best_key is None or key < best_key:
                best, best_key = task, key
        return best

    def claim(
        self,
        agent: str,
        tenant: Optional[str] = None,
        lease_seconds: Optional[float] = None,
    ) -> Optional[Task]:
        """Claim the best PENDING task for ``agent`` (priority + age order).

        The claimed task enters CLAIMED with a lease (``lease_agent`` +
        ``lease_until``). Returns ``None`` when nothing is claimable. Only a
        PENDING task can be claimed, so no two agents ever claim the same task.
        """
        if not agent:
            raise ValueError("claim requires an agent id")
        lease = lease_seconds if lease_seconds is not None else self._cfg.lease_seconds

        def _fn(snapshot: QueueSnapshot) -> Optional[Task]:
            candidate = self._candidate(snapshot, tenant)
            if candidate is None:
                return None
            now = self._now()
            self._record(
                snapshot, candidate, TaskState.CLAIMED, agent=agent, reason="claim"
            )
            candidate.lease_agent = agent
            candidate.lease_until = now + lease
            return self._detach(candidate)

        return self._mutate(_fn)  # type: ignore[return-value]

    def start(self, task_id: str, agent: str) -> Task:
        """Mark a held task RUNNING (CLAIMED -> RUNNING). Idempotent."""

        def _fn(snapshot: QueueSnapshot) -> Task:
            task = self._require_task(snapshot, task_id)
            if task.status is TaskState.RUNNING and task.lease_agent == agent:
                return self._detach(task)
            if task.status is not TaskState.CLAIMED or task.lease_agent != agent:
                raise NotClaimedError(
                    f"task {task_id} is {task.status.value} held by "
                    f"{task.lease_agent!r}; agent {agent!r} cannot start it"
                )
            now = self._now()
            if self._expired(task, now):
                raise LeaseError(
                    f"task {task_id} lease expired at {task.lease_until}; "
                    f"reap and reclaim"
                )
            self._record(snapshot, task, TaskState.RUNNING, agent=agent, reason="start")
            return self._detach(task)

        return self._mutate(_fn)  # type: ignore[return-value]

    def renew(
        self,
        task_id: str,
        agent: str,
        lease_seconds: Optional[float] = None,
    ) -> Task:
        """Heartbeat: extend the lease of a task this agent currently holds."""

        def _fn(snapshot: QueueSnapshot) -> Task:
            task = self._require_task(snapshot, task_id)
            if task.status not in LEASE_STATES or task.lease_agent != agent:
                raise NotClaimedError(
                    f"task {task_id} is {task.status.value} held by "
                    f"{task.lease_agent!r}; agent {agent!r} cannot renew it"
                )
            now = self._now()
            if self._expired(task, now):
                raise LeaseError(f"task {task_id} lease already expired")
            lease = (
                lease_seconds
                if lease_seconds is not None
                else self._cfg.lease_seconds
            )
            task.lease_until = now + lease
            task.updated_at = now
            return self._detach(task)

        return self._mutate(_fn)  # type: ignore[return-value]

    def ack(self, task_id: str, agent: str) -> Task:
        """Acknowledge success: -> SUCCEEDED. Only the lease holder may ack.

        Idempotent: acking an already-SUCCEEDED task returns it unchanged. An
        expired lease or a different holder raises (no split-brain ack).
        """

        def _fn(snapshot: QueueSnapshot) -> Task:
            task = self._require_task(snapshot, task_id)
            if task.status is TaskState.SUCCEEDED:
                return self._detach(task)
            if task.status not in LEASE_STATES or task.lease_agent != agent:
                raise NotClaimedError(
                    f"task {task_id} is {task.status.value} held by "
                    f"{task.lease_agent!r}; agent {agent!r} cannot ack it"
                )
            now = self._now()
            if self._expired(task, now):
                raise LeaseError(
                    f"task {task_id} lease expired at {task.lease_until}; "
                    f"the task was reaped — do not ack a reclaimed task"
                )
            self._record(snapshot, task, TaskState.SUCCEEDED, agent=agent, reason="ack")
            task.lease_agent = None
            task.lease_until = None
            return self._detach(task)

        return self._mutate(_fn)  # type: ignore[return-value]

    def fail(
        self,
        task_id: str,
        agent: str,
        reason: Optional[str] = None,
    ) -> Task:
        """Record failure: retry (-> PENDING) or dead-letter (-> FAILED).

        Idempotent on dead-lettered tasks. Only the lease holder may fail; an
        expired lease raises (the reaper owns it by then).
        """

        def _fn(snapshot: QueueSnapshot) -> Task:
            task = self._require_task(snapshot, task_id)
            if task.status in DEAD_LETTER_STATES:
                return self._detach(task)
            if task.status is TaskState.SUCCEEDED:
                raise NotClaimedError(
                    f"task {task_id} already SUCCEEDED; cannot fail it"
                )
            if task.status not in LEASE_STATES or task.lease_agent != agent:
                raise NotClaimedError(
                    f"task {task_id} is {task.status.value} held by "
                    f"{task.lease_agent!r}; agent {agent!r} cannot fail it"
                )
            now = self._now()
            if self._expired(task, now):
                raise LeaseError(f"task {task_id} lease already expired")
            task.attempts += 1
            if task.attempts >= self._cfg.max_attempts:
                self._record(
                    snapshot,
                    task,
                    TaskState.FAILED,
                    agent=agent,
                    reason=f"attempt {task.attempts} of "
                    f"{self._cfg.max_attempts} exhausted: {reason or 'failure'}",
                )
            else:
                self._record(
                    snapshot,
                    task,
                    TaskState.PENDING,
                    agent=agent,
                    reason=f"attempt {task.attempts} of "
                    f"{self._cfg.max_attempts} failed, retrying: {reason or ''}",
                )
            task.lease_agent = None
            task.lease_until = None
            task.last_error = reason
            return self._detach(task)

        return self._mutate(_fn)  # type: ignore[return-value]

    def reap(self, at: Optional[float] = None) -> List[str]:
        """Orphan reaper: reclaim tasks whose lease expired (no heartbeat).

        Each expired CLAIMED/RUNNING task is requeued to PENDING with one more
        attempt, or dead-lettered to DEAD when its attempt budget is spent.
        Never SUCCEEDED — a task whose worker died is never recorded as done.
        Returns the ids of reaped tasks (idempotent: empty on a second run).
        """

        def _fn(snapshot: QueueSnapshot) -> List[str]:
            now = at if at is not None else self._now()
            reaped: List[str] = []
            for task in list(snapshot.tasks.values()):
                if task.status not in LEASE_STATES:
                    continue
                if task.lease_until is None or task.lease_until >= now:
                    continue
                task.attempts += 1
                if task.attempts >= self._cfg.max_attempts:
                    self._record(
                        snapshot,
                        task,
                        TaskState.DEAD,
                        agent=None,
                        reason=f"reap: {task.attempts} attempts exhausted",
                    )
                else:
                    self._record(
                        snapshot,
                        task,
                        TaskState.PENDING,
                        agent=None,
                        reason=f"reap: lease expired -> requeue "
                        f"(attempt {task.attempts})",
                    )
                task.lease_agent = None
                task.lease_until = None
                reaped.append(task.task_id)
            return reaped

        return self._mutate(_fn)  # type: ignore[return-value]

    def replay(self, task_id: Optional[str] = None) -> List[str]:
        """Dead-letter replay: FAILED/DEAD -> PENDING with attempts reset.

        With no ``task_id`` every dead-lettered task is replayed. With a
        ``task_id`` only that task is replayed (raises ``NotReplayableError``
        if it is not in a dead-letter state).
        """

        def _fn(snapshot: QueueSnapshot) -> List[str]:
            if task_id is not None:
                if task_id not in snapshot.tasks:
                    raise NoSuchTaskError(f"no such task: {task_id}")
                task = snapshot.tasks[task_id]
                if task.status not in DEAD_LETTER_STATES:
                    raise NotReplayableError(
                        f"task {task_id} is {task.status.value}; only "
                        f"FAILED/DEAD tasks can be replayed"
                    )
                targets = [task_id]
            else:
                targets = [
                    tid
                    for tid, t in snapshot.tasks.items()
                    if t.status in DEAD_LETTER_STATES
                ]
            replayed: List[str] = []
            for tid in targets:
                task = snapshot.tasks[tid]
                task.attempts = 0
                task.lease_agent = None
                task.lease_until = None
                self._record(
                    snapshot, task, TaskState.PENDING, agent=None, reason="replay"
                )
                replayed.append(tid)
            return replayed

        return self._mutate(_fn)  # type: ignore[return-value]

    # -- reads ---------------------------------------------------------------

    def get(self, task_id: str) -> Optional[Task]:
        """Return a detached copy of one task, or ``None``."""
        with self._store.lock():
            task = self._store.load().tasks.get(task_id)
            return self._detach(task) if task is not None else None

    def list_tasks(
        self,
        status: Optional[TaskState] = None,
        tenant: Optional[str] = None,
    ) -> List[Task]:
        """List tasks, optionally filtered by status/tenant (detached copies)."""
        with self._store.lock():
            tasks = self._store.load().tasks.values()
            out = []
            for task in tasks:
                if status is not None and task.status is not status:
                    continue
                if tenant is not None and task.tenant != tenant:
                    continue
                out.append(self._detach(task))
            return sorted(out, key=lambda t: t.created_at)

    def audit_log(self) -> List[AuditEntry]:
        """Append-only audit ledger, ordered by seq (detached copies)."""
        with self._store.lock():
            return copy.deepcopy(self._store.load().audit)

    def depth(self, tenant: Optional[str] = None) -> int:
        """Number of open (non-terminal) tasks, optionally per tenant."""
        with self._store.lock():
            return open_count(self._store.load().tasks, tenant)

    def stats(self) -> Dict[str, object]:
        """Queue statistics: per-state counts, per-tenant depth, ledger size."""
        with self._store.lock():
            snapshot = self._store.load()
        by_status: Dict[str, int] = {}
        tenants: Dict[str, int] = {}
        for task in snapshot.tasks.values():
            by_status[task.status.value] = by_status.get(task.status.value, 0) + 1
            if task.status.value in (
                TaskState.PENDING.value,
                TaskState.CLAIMED.value,
                TaskState.RUNNING.value,
            ):
                tenants[task.tenant] = tenants.get(task.tenant, 0) + 1
        return {
            "by_status": by_status,
            "open_by_tenant": tenants,
            "dead_letter": sum(
                1
                for t in snapshot.tasks.values()
                if t.status in DEAD_LETTER_STATES
            ),
            "audit_entries": len(snapshot.audit),
            "tasks": len(snapshot.tasks),
        }
