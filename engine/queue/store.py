"""engine/queue.store — persistence seam + the single-writer authority.

The queue authority is single-writer (issue #22 acceptance: *queue authority
single-writer*, no split-brain). Every mutation runs under exactly one
exclusive lock. Two stores back the seam, sharing one snapshot contract:

- ``InMemoryStore`` — a ``threading.RLock`` over an in-process snapshot
  (tests, dev, single process).
- ``FileStore`` — an ``fcntl.flock``-guarded JSON file, written atomically
  (tempfile + ``os.replace`` + fsync) so two OS processes on the same file
  still serialize. This is the offline analogue of CMR ``fleet/queue.sh``'s
  mkdir lock and the leaderboard ``single-writer-lock.sh`` doctrine: one
  writer at a time, no split-brain claim.

The store never reasons about lifecycle — it only persists/restores a
:class:`QueueSnapshot`. All policy lives in ``engine.queue.queue.JobQueue``.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Protocol

from engine.queue.model import AuditEntry, Priority, Task
from engine.queue.state import TaskState


@dataclass
class QueueSnapshot:
    """One consistent view of the queue state (tasks + audit + seq)."""

    tasks: Dict[str, Task] = field(default_factory=dict)
    audit: List[AuditEntry] = field(default_factory=list)
    seq: int = 0  # last audit sequence number issued


class Store(Protocol):
    """Persistence seam every store must satisfy."""

    def lock(self) -> Iterator[None]: ...  # pragma: no cover - protocol

    def load(self) -> QueueSnapshot: ...  # pragma: no cover - protocol

    def save(self, snapshot: QueueSnapshot) -> None: ...  # pragma: no cover


# --- (de)serialization for the JSON seam ---------------------------------


def task_to_dict(task: Task) -> dict:
    return {
        "task_id": task.task_id,
        "tenant": task.tenant,
        "priority": task.priority.value,
        "payload": task.payload,
        "status": task.status.value,
        "attempts": task.attempts,
        "idempotency_key": task.idempotency_key,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "lease_agent": task.lease_agent,
        "lease_until": task.lease_until,
        "last_error": task.last_error,
    }


def task_from_dict(data: dict) -> Task:
    return Task(
        task_id=data["task_id"],
        tenant=data.get("tenant", "default"),
        priority=Priority(data.get("priority", Priority.NORMAL.value)),
        payload=data.get("payload"),
        status=TaskState(data.get("status", TaskState.PENDING.value)),
        attempts=int(data.get("attempts", 0)),
        idempotency_key=data.get("idempotency_key"),
        created_at=float(data.get("created_at", 0.0)),
        updated_at=float(data.get("updated_at", 0.0)),
        lease_agent=data.get("lease_agent"),
        lease_until=data.get("lease_until"),
        last_error=data.get("last_error"),
    )


def audit_to_dict(entry: AuditEntry) -> dict:
    return {
        "seq": entry.seq,
        "task_id": entry.task_id,
        "from_state": entry.from_state.value if entry.from_state else None,
        "to_state": entry.to_state.value,
        "agent": entry.agent,
        "ts": entry.ts,
        "reason": entry.reason,
    }


def audit_from_dict(data: dict) -> AuditEntry:
    frm = data.get("from_state")
    return AuditEntry(
        seq=int(data["seq"]),
        task_id=data["task_id"],
        from_state=TaskState(frm) if frm else None,
        to_state=TaskState(data["to_state"]),
        agent=data.get("agent"),
        ts=float(data.get("ts", 0.0)),
        reason=data.get("reason"),
    )


def _snapshot_to_dict(snap: QueueSnapshot) -> dict:
    return {
        "seq": snap.seq,
        "tasks": {tid: task_to_dict(t) for tid, t in snap.tasks.items()},
        "audit": [audit_to_dict(a) for a in snap.audit],
    }


def _snapshot_from_dict(data: dict) -> QueueSnapshot:
    raw_tasks = data.get("tasks") or {}
    raw_audit = data.get("audit") or []
    return QueueSnapshot(
        tasks={tid: task_from_dict(t) for tid, t in raw_tasks.items()},
        audit=[audit_from_dict(a) for a in raw_audit],
        seq=int(data.get("seq", 0)),
    )


# --- stores ----------------------------------------------------------------


class InMemoryStore:
    """Single-process store: a re-entrant lock over an in-memory snapshot."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot = QueueSnapshot()

    @contextmanager
    def lock(self) -> Iterator[None]:
        with self._lock:
            yield

    def load(self) -> QueueSnapshot:
        return copy.deepcopy(self._snapshot)

    def save(self, snapshot: QueueSnapshot) -> None:
        self._snapshot = snapshot


class FileStore:
    """flock-guarded, atomically-replaced JSON store (multi-process safe).

    ``path`` points at the state file; a sibling ``<path>.lock`` holds the
    ``flock``. A missing file is treated as an empty queue; the first save
    creates it.
    """

    def __init__(self, path: str) -> None:
        self._path = os.path.abspath(path)
        self._lock_path = self._path + ".lock"
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if not os.path.exists(self._path):
            self._atomic_write(QueueSnapshot())
        self._lock_fd = open(self._lock_path, "a+", encoding="utf-8")

    @contextmanager
    def lock(self) -> Iterator[None]:
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

    def load(self) -> QueueSnapshot:
        if not os.path.exists(self._path):
            return QueueSnapshot()
        with open(self._path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return _snapshot_from_dict(data)

    def save(self, snapshot: QueueSnapshot) -> None:
        self._atomic_write(snapshot)

    def _atomic_write(self, snapshot: QueueSnapshot) -> None:
        data = _snapshot_to_dict(snapshot)
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(self._path) or ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
