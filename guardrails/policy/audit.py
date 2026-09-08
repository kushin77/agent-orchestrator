"""Audit log for gate decisions (issue #26 acceptance #2).

Every BLOCK the gate engine emits is audit-logged; WARN and LOG decisions are
recorded too so the ledger is a complete, structured transcript of *what was
asked and what the gate answered*.  Each record is self-describing (action,
subject, tenant, decision, outcome, contributing policies/rules, reason,
evidence) so a downstream consumer can render or verify it without the policy
bundle present.

Two implementations:

* :class:`InMemoryAuditLog` — default engine sink; records in memory.
* :class:`JsonlAuditLog` — append-only JSON-lines file sink.  It has no
  update/delete API: append-only by construction.  (The durable,
  hash-chained, per-tenant tamper-evident ledger is owned by the
  observability lane, issue #31; this lane provides the structured record
  seam that ledger consumes.)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


@dataclass
class AuditRecord:
    """One structured gate-decision record."""

    sequence: int
    timestamp: str
    decision: str
    action: str
    outcome: str
    subject: Optional[str] = None
    tenant: Optional[str] = None
    policy_ids: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()
    reason: Optional[str] = None
    error: Optional[str] = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "decision": self.decision,
            "action": self.action,
            "outcome": self.outcome,
            "subject": self.subject,
            "tenant": self.tenant,
            "policy_ids": list(self.policy_ids),
            "rule_ids": list(self.rule_ids),
            "reason": self.reason,
            "error": self.error,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuditRecord":
        return cls(
            sequence=int(data["sequence"]),
            timestamp=str(data["timestamp"]),
            decision=str(data["decision"]),
            action=str(data["action"]),
            outcome=str(data["outcome"]),
            subject=data.get("subject"),
            tenant=data.get("tenant"),
            policy_ids=tuple(data.get("policy_ids") or ()),
            rule_ids=tuple(data.get("rule_ids") or ()),
            reason=data.get("reason"),
            error=data.get("error"),
            evidence=data.get("evidence") or {},
        )


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (audit timestamps)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AuditLog:
    """Append-only audit sink interface."""

    def append(self, record: AuditRecord) -> None:
        raise NotImplementedError

    def records(self) -> Sequence[AuditRecord]:
        raise NotImplementedError

    def __len__(self) -> int:
        return len(self.records())


class InMemoryAuditLog(AuditLog):
    """Audit log held in memory (default engine sink)."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def append(self, record: AuditRecord) -> None:
        self._records.append(record)

    def records(self) -> Sequence[AuditRecord]:
        return tuple(self._records)


class JsonlAuditLog(AuditLog):
    """Append-only JSON-lines audit log on disk.

    ``append`` opens the file in append mode and writes one JSON object per
    line; there is deliberately no update, delete or rewrite surface.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, record: AuditRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.to_dict(), sort_keys=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def records(self) -> Sequence[AuditRecord]:
        if not self.path.exists():
            return ()
        records: list[AuditRecord] = []
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                records.append(AuditRecord.from_dict(json.loads(line)))
        return tuple(records)
