"""Append-only audit ledger (default in-memory/file store).

Every control-plane mutation appends one audit record before it returns —
"all mutations append to the audit ledger" (issue #38 AC3). The store here is
the offline default (in-memory for tests, JSON Lines file for durable
single-node use); production wiring maps this seam onto the merged
tamper-evident per-tenant ledger in ``telemetry/ledger`` (issue #31) — the
record vocabulary below mirrors that contract (``actor`` is ``kind:id``,
``action`` is a dotted verb, ``resource`` names the target), so the adapter is
a thin append.

AuditRecordView is returned on append so a handler can echo the stamp; query
filters by tenant (mandatory for tenant-scoped callers), action and actor and
returns newest-first.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .errors import validation_error
from .model import AuditRecordView


class AuditStore:
    """Append-only audit store (in-memory by default, optional file backing)."""

    def __init__(self, *, clock: Any, path: Optional[str] = None) -> None:
        self._clock = clock
        self._path = path
        self._records: List[AuditRecordView] = []
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    self._records.append(AuditRecordView(**data))

    def append(
        self,
        *,
        tenant_id: str,
        actor: str,
        action: str,
        resource: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> AuditRecordView:
        if not tenant_id or not actor or not action:
            raise validation_error("tenant_id, actor and action are required")
        record = AuditRecordView(
            seq=len(self._records) + 1,
            ts=self._clock.now_utc(),
            tenantId=tenant_id,
            actor=actor,
            action=action,
            resource=resource,
            detail=dict(detail) if detail else None,
        )
        self._records.append(record)
        if self._path:
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(record.to_dict(), sort_keys=True) + "\n"
                )
        return record

    def query(
        self,
        *,
        tenant_id: Optional[str] = None,
        action: Optional[str] = None,
        actor: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditRecordView]:
        matches = self._records
        if tenant_id is not None:
            matches = [r for r in matches if r.tenantId == tenant_id]
        if action is not None:
            matches = [r for r in matches if r.action == action]
        if actor is not None:
            matches = [r for r in matches if r.actor == actor]
        # newest-first
        return list(reversed(matches))[:limit]

    @property
    def records(self) -> List[AuditRecordView]:
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)
