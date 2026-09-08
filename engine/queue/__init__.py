"""engine/queue — task lifecycle state machine + job queue (issue #22).

The queue authority for the state-machine execution pillar: enqueue, claim
(with leases), start/renew (heartbeat), ack/fail, an orphan reaper for
lease-expired claims, and dead-letter replay. Single-writer (no split-brain),
at-least-once with idempotency keys, backpressure-bounded per tenant, and an
append-only audit ledger on every transition.

Lifecycle::

    PENDING -> CLAIMED(lease) -> RUNNING -> SUCCEEDED | FAILED | DEAD
                    └── lease expiry --reap--> PENDING (retry) | DEAD

Public surface
--------------

- ``JobQueue`` (engine.queue.queue) — the single-writer authority:
  ``enqueue``/``claim``/``start``/``renew``/``ack``/``fail``/``reap``/
  ``replay`` plus reads (``get``, ``list_tasks``, ``audit_log``, ``depth``,
  ``stats``).
- ``Worker`` (engine.queue.worker) — claim -> run -> ack/fail loop that never
  records a false PASS (handler death ⇒ FAILED, never SUCCEEDED).
- ``TaskState`` / ``VALID_TRANSITIONS`` (engine.queue.state) — the lifecycle
  state machine.
- ``Task`` / ``TaskSpec`` / ``AuditEntry`` / ``Priority`` (engine.queue.model).
- ``InMemoryStore`` / ``FileStore`` (engine.queue.store) — single-writer
  persistence seams (in-process RLock, or flock + atomic JSON replace).
- ``QueueConfig`` / ``load_config`` (engine.queue.config), ``queue.yaml``.
- ``BackpressureError`` (engine.queue.backpressure) — bounded per-tenant queues.
- ``find_by_idempotency_key`` (engine.queue.dedup) — at-least-once dedup.

The package is importable as ``engine.queue`` when the repo root is on
``sys.path`` (tests arrange this in ``tests/conftest.py``).
"""

from engine.queue.backpressure import BackpressureError
from engine.queue.config import (
    DEFAULT_CONFIG_PATH,
    QueueConfig,
    default_config,
    load_config,
)
from engine.queue.dedup import fingerprint_payload, find_by_idempotency_key
from engine.queue.model import (
    AuditEntry,
    Priority,
    Task,
    TaskSpec,
    coerce_priority,
)
from engine.queue.queue import (
    DuplicateTaskError,
    JobQueue,
    LeaseError,
    NoSuchTaskError,
    NotClaimedError,
    NotReplayableError,
    QueueError,
)
from engine.queue.state import (
    DEAD_LETTER_STATES,
    LEASE_STATES,
    OPEN_STATES,
    TERMINAL_STATES,
    TaskState,
    transition_allowed,
)
from engine.queue.store import (
    FileStore,
    InMemoryStore,
    QueueSnapshot,
    Store,
)
from engine.queue.worker import Outcome, Worker

__all__ = [
    # state machine
    "TaskState",
    "LEASE_STATES",
    "OPEN_STATES",
    "TERMINAL_STATES",
    "DEAD_LETTER_STATES",
    "transition_allowed",
    # model
    "Priority",
    "Task",
    "TaskSpec",
    "AuditEntry",
    "coerce_priority",
    # queue authority
    "JobQueue",
    "QueueError",
    "DuplicateTaskError",
    "NoSuchTaskError",
    "NotClaimedError",
    "LeaseError",
    "NotReplayableError",
    # stores
    "Store",
    "InMemoryStore",
    "FileStore",
    "QueueSnapshot",
    # config
    "QueueConfig",
    "default_config",
    "load_config",
    "DEFAULT_CONFIG_PATH",
    # backpressure
    "BackpressureError",
    # dedup
    "find_by_idempotency_key",
    "fingerprint_payload",
    # worker
    "Worker",
    "Outcome",
]
