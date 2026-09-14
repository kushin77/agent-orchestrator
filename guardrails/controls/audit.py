"""Append-only audit records for guardrail control toggles (issue #343).

Every flip of a :class:`~controls.model.PolicyControl` writes exactly ONE
record to an append-only log.  The record is a complete "who/what/when/before/
after" transcript — actor, control id, the before/after state, the Portkey-style
guardrail status codes those states map to (246 PASSED / 446 BLOCKED, see
:mod:`controls.model`), a reason, and the control's own audit reference — so a
downstream reader can render or verify a toggle without the registry present.

Two sinks, both append-only by construction (no update/delete/rewrite API):

* :class:`InMemoryControlAuditLog` — default in-process sink.
* :class:`JsonlControlAuditLog` — an append-only JSON-lines file sink.  A
  second reader opens the same path and sees every toggle already recorded,
  which is what makes the flip observable across processes.

The hash-chained, tamper-evident ledger is owned by the observability lane
(``telemetry/ledger``); this lane provides the structured record seam it
consumes, mirroring ``guardrails/policy/audit.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (audit timestamps)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ControlAuditRecord:
    """One structured control-toggle record (append-only)."""

    sequence: int
    timestamp: str
    actor: str
    action: str
    control_id: str
    before: bool
    after: bool
    status_before: int
    status_after: int
    audit_ref: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "action": self.action,
            "control_id": self.control_id,
            "before": self.before,
            "after": self.after,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "audit_ref": self.audit_ref,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ControlAuditRecord":
        return cls(
            sequence=int(data["sequence"]),
            timestamp=str(data["timestamp"]),
            actor=str(data["actor"]),
            action=str(data["action"]),
            control_id=str(data["control_id"]),
            before=bool(data["before"]),
            after=bool(data["after"]),
            status_before=int(data["status_before"]),
            status_after=int(data["status_after"]),
            audit_ref=str(data.get("audit_ref") or ""),
            reason=str(data.get("reason") or ""),
        )


def build_toggle_record(
    *,
    sequence: int,
    control_id: str,
    actor: str,
    before: bool,
    after: bool,
    status_before: int,
    status_after: int,
    audit_ref: str = "",
    reason: str = "",
    action: str = "toggle",
    timestamp: str | None = None,
) -> ControlAuditRecord:
    """Build one toggle record (the single record a flip writes)."""
    return ControlAuditRecord(
        sequence=int(sequence),
        timestamp=timestamp or utc_now_iso(),
        actor=actor,
        action=action,
        control_id=control_id,
        before=bool(before),
        after=bool(after),
        status_before=int(status_before),
        status_after=int(status_after),
        audit_ref=audit_ref,
        reason=reason,
    )


class InMemoryControlAuditLog:
    """Append-only in-memory sink (default)."""

    def __init__(self, records: Sequence[ControlAuditRecord] = ()) -> None:
        self._records: list[ControlAuditRecord] = list(records)

    def next_sequence(self) -> int:
        return len(self._records) + 1

    def append(self, record: ControlAuditRecord) -> ControlAuditRecord:
        self._records.append(record)
        return record

    def records(self) -> tuple[ControlAuditRecord, ...]:
        return tuple(self._records)

    def __len__(self) -> int:
        return len(self._records)


class JsonlControlAuditLog:
    """Append-only JSON-lines sink on disk.

    ``append`` opens the file in append mode and writes one JSON object per
    line; there is deliberately no update, delete or rewrite surface.  A
    second reader constructs a new instance over the same path and sees every
    record — the observable proof that a toggle happened.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._sequence = self._count_existing()

    def _count_existing(self) -> int:
        if not self.path.exists():
            return 0
        count = 0
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    count += 1
        return count

    def next_sequence(self) -> int:
        return self._sequence + 1

    def append(self, record: ControlAuditRecord) -> ControlAuditRecord:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
        self._sequence += 1
        return record

    def records(self) -> tuple[ControlAuditRecord, ...]:
        if not self.path.exists():
            return ()
        out: list[ControlAuditRecord] = []
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                out.append(ControlAuditRecord.from_dict(json.loads(line)))
        return tuple(out)

    def __len__(self) -> int:
        return self._sequence
