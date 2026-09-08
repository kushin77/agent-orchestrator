"""telemetry/budgets — durable audit of budget/quota/kill-switch decisions (#34).

Every BLOCK / WARN budget decision (and its observe-mode would-* equivalent,
a kill-switch refuse, or a pause/clear transition) is recorded to an
append-only audit feed so operators can answer *what was refused, why, for
which tenant, and when* — the audit trail the acceptance criteria require.
The feed is the same append-only JSONL shape as the sibling telemetry stores
(one camelCase JSON object per line, ``_schemaVersion`` envelope marker).

``MemoryAuditStore`` is for tests/offline; ``JsonlAuditStore`` is the durable
store.  Enforcers accept an optional ``BudgetAuditStore`` and auto-record
auditable decisions; the ``preflight`` runner records the composed outcome.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Protocol

from telemetry.budgets.model import (
    EnforcerDecision,
    KIND_BUDGET,
    KIND_KILL_SWITCH,
    KIND_QUOTA,
    now_utc_iso,
)

#: Audit event types (what happened).
EVENT_DECISION = "decision"     # a budget/quota decision (warn/block/would-*)
EVENT_PAUSE = "pause"           # kill switch engaged
EVENT_CLEAR = "clear"           # kill switch cleared
EVENT_EXEMPT = "exempt"         # a call allowed through a paused switch (critical)

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BudgetAuditEvent:
    """One audit record of a budget/quota/kill-switch action."""

    kind: str                     # budget | quota | kill_switch
    event_type: str               # decision | pause | clear | exempt
    tenant_id: str
    decision: str
    code: str
    reason: str
    ts: str = field(default_factory=now_utc_iso)
    agent_id: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    resource: Optional[str] = None
    window: Optional[str] = None
    current: Optional[float] = None
    requested: Optional[float] = None
    limit: Optional[float] = None
    outcome: Optional[str] = None
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if self.kind not in {KIND_BUDGET, KIND_QUOTA, KIND_KILL_SWITCH}:
            raise ValueError(f"unknown audit kind: {self.kind!r}")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the camelCase JSONL shape."""
        return {
            "schemaVersion": SCHEMA_VERSION,
            "eventId": self.event_id,
            "ts": self.ts,
            "kind": self.kind,
            "eventType": self.event_type,
            "tenantId": self.tenant_id,
            "agentId": self.agent_id,
            "decision": self.decision,
            "code": self.code,
            "reason": self.reason,
            "vendor": self.vendor,
            "model": self.model,
            "resource": self.resource,
            "window": self.window,
            "current": None if self.current is None else round(self.current, 8),
            "requested": None if self.requested is None else round(self.requested, 8),
            "limit": None if self.limit is None else round(self.limit, 8),
            "outcome": self.outcome,
        }

    @classmethod
    def from_decision(
        cls,
        decision: EnforcerDecision,
        *,
        event_type: str = EVENT_DECISION,
    ) -> "BudgetAuditEvent":
        """Build an audit event from an :class:`EnforcerDecision`."""
        return cls(
            kind=decision.kind,
            event_type=event_type,
            tenant_id=decision.tenant_id,
            decision=decision.decision,
            code=decision.code,
            reason=decision.reason,
            agent_id=decision.agent_id,
            vendor=decision.vendor,
            model=decision.model,
            resource=decision.resource,
            window=decision.window,
            current=decision.current,
            requested=decision.requested,
            limit=decision.limit,
            outcome=decision.outcome,
        )


class BudgetAuditStore(Protocol):
    """Append-only store for budget/quota/kill-switch audit events."""

    def record(self, event: BudgetAuditEvent) -> None:
        """Persist one audit event."""
        raise NotImplementedError

    def read(self) -> List[BudgetAuditEvent]:
        """Every recorded event, in append order."""
        raise NotImplementedError

    def count(self) -> int:
        """Number of recorded events."""
        raise NotImplementedError


class MemoryAuditStore:
    """In-memory audit store (tests and offline runs)."""

    def __init__(self) -> None:
        self._events: List[BudgetAuditEvent] = []

    def record(self, event: BudgetAuditEvent) -> None:
        self._events.append(event)

    def read(self) -> List[BudgetAuditEvent]:
        return list(self._events)

    def count(self) -> int:
        return len(self._events)

    def clear(self) -> None:
        self._events = []


class JsonlAuditStore:
    """Append-only JSONL audit store (durable across restarts)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: BudgetAuditEvent) -> None:
        payload = event.to_dict()
        payload["_schemaVersion"] = SCHEMA_VERSION
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")

    def read(self) -> List[BudgetAuditEvent]:
        events: List[BudgetAuditEvent] = []
        if not self.path.is_file():
            return events
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue  # corrupt tail line: skip rather than crash the read
                events.append(_event_from_payload(payload))
        return events

    def count(self) -> int:
        return len(self.read())


def record_decision(
    store: BudgetAuditStore,
    decision: EnforcerDecision,
    *,
    event_type: str = EVENT_DECISION,
) -> Optional[BudgetAuditEvent]:
    """Record an auditable decision; plain allows are skipped (no signal).

    Returns the recorded event, or ``None`` when the decision carried nothing
    worth auditing (a bare ``allow``).
    """
    if not decision.auditable:
        return None
    event = BudgetAuditEvent.from_decision(decision, event_type=event_type)
    store.record(event)
    return event


def _event_from_payload(payload: dict[str, Any]) -> BudgetAuditEvent:
    return BudgetAuditEvent(
        kind=str(payload.get("kind") or KIND_BUDGET),
        event_type=str(payload.get("eventType") or EVENT_DECISION),
        tenant_id=str(payload.get("tenantId") or ""),
        decision=str(payload.get("decision") or ""),
        code=str(payload.get("code") or ""),
        reason=str(payload.get("reason") or ""),
        ts=str(payload.get("ts") or now_utc_iso()),
        agent_id=payload.get("agentId"),
        vendor=payload.get("vendor"),
        model=payload.get("model"),
        resource=payload.get("resource"),
        window=payload.get("window"),
        current=payload.get("current"),
        requested=payload.get("requested"),
        limit=payload.get("limit"),
        outcome=payload.get("outcome"),
        event_id=str(payload.get("eventId") or uuid.uuid4().hex),
    )
