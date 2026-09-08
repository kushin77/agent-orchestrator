"""Append-only event store seam (event-sourcing persistence).

Durability comes from the event log: the engine never mutates state and then
records it — every mutation *is* an event appended to a store, and a workflow
is rebuilt by replaying its events (deterministic replay).  Two
implementations of the :class:`EventStore` seam ship here:

* :class:`InMemoryEventStore` — fast, for tests and single-process use.
* :class:`FileJsonlEventStore` — one JSON object per line, append-only,
  flushed after every append, so a workflow survives a process restart and
  resumes mid-flight from the persisted log.  A corrupt/unparseable line is
  refused with :class:`PersistenceError` (never silently skipped).

Events are scoped by ``(namespace_id, workflow_id)``: reads filter on both,
so a workflow that lives in tenant A is invisible to tenant B's store reads
(namespace isolation even at the persistence layer).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol

from .errors import PersistenceError
from .model import EventKind


@dataclass
class EventRecord:
    """One immutable line of a workflow transcript."""

    seq: int
    namespace_id: str
    workflow_id: str
    kind: EventKind
    ts: str
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "namespace_id": self.namespace_id,
            "workflow_id": self.workflow_id,
            "kind": self.kind.value if isinstance(self.kind, EventKind) else self.kind,
            "ts": self.ts,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventRecord":
        try:
            return cls(
                seq=int(data["seq"]),
                namespace_id=str(data["namespace_id"]),
                workflow_id=str(data["workflow_id"]),
                kind=EventKind(data["kind"]),
                ts=str(data["ts"]),
                payload=dict(data.get("payload") or {}),
            )
        except (KeyError, TypeError, ValueError) as exc:  # corrupt record
            raise PersistenceError(f"malformed event record: {exc}") from exc


class EventStore(Protocol):
    """Persistence seam the engine depends on."""

    def append(self, record: EventRecord) -> EventRecord:
        """Persist a record and return it with its sequence assigned."""

    def events(self, namespace_id: str, workflow_id: str) -> List[EventRecord]:
        """All records for one workflow in sequence order (tenant-scoped)."""

    def close(self) -> None:
        """Release resources, if any."""


class InMemoryEventStore:
    """In-process append-only store (tests / single-process runs)."""

    def __init__(self) -> None:
        self._records: List[EventRecord] = []
        self._seq: int = 0

    def append(self, record: EventRecord) -> EventRecord:
        self._seq += 1
        stored = EventRecord(
            seq=self._seq,
            namespace_id=record.namespace_id,
            workflow_id=record.workflow_id,
            kind=record.kind,
            ts=record.ts,
            payload=record.payload,
        )
        self._records.append(stored)
        return stored

    def events(self, namespace_id: str, workflow_id: str) -> List[EventRecord]:
        return [
            r
            for r in self._records
            if r.namespace_id == namespace_id and r.workflow_id == workflow_id
        ]

    def all_records(self) -> List[EventRecord]:
        return list(self._records)

    def close(self) -> None:
        pass


class FileJsonlEventStore:
    """Append-only JSONL event store that survives process restarts.

    Every append is flushed to disk immediately.  Opening an existing file
    validates every line (a corrupt line is :class:`PersistenceError`) and
    resumes the sequence counter from the highest seen sequence so a resumed
    engine keeps appending after the interrupted run's events.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._seq: int = 0
        self._scan_on_open()

    def _scan_on_open(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    record = EventRecord.from_dict(data)
                except (json.JSONDecodeError, PersistenceError) as exc:
                    raise PersistenceError(
                        f"{self.path}:{line_no}: corrupt event log line: {exc}"
                    ) from exc
                if record.seq > self._seq:
                    self._seq = record.seq

    def append(self, record: EventRecord) -> EventRecord:
        self._seq += 1
        stored = EventRecord(
            seq=self._seq,
            namespace_id=record.namespace_id,
            workflow_id=record.workflow_id,
            kind=record.kind,
            ts=record.ts,
            payload=record.payload,
        )
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(stored.to_dict(), sort_keys=True) + "\n")
            fh.flush()
        return stored

    def events(self, namespace_id: str, workflow_id: str) -> List[EventRecord]:
        found: List[EventRecord] = []
        if not os.path.exists(self.path):
            return found
        with open(self.path, "r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = EventRecord.from_dict(json.loads(line))
                except (json.JSONDecodeError, PersistenceError) as exc:
                    raise PersistenceError(
                        f"{self.path}:{line_no}: corrupt event log line: {exc}"
                    ) from exc
                if (
                    record.namespace_id == namespace_id
                    and record.workflow_id == workflow_id
                ):
                    found.append(record)
        found.sort(key=lambda r: r.seq)
        return found

    def close(self) -> None:
        pass
