#!/usr/bin/env python3
"""Metering hook for cost attribution (issue #17).

Every model call routed by the FinOps chooser emits one ``CallRecord`` to a
``MeteringSink``. The sink is the Phase-5 metering-store interface the gateway
calls — the telemetry pillar (issue #31, phase 5) consumes these records to
build full-trace token/cost attribution per tenant, per agent, per task class.

Design mirrors the gateway's injectable-hook pattern: the chooser depends only
on the ``MeteringSink`` protocol (``record(CallRecord)``), so the real Phase-5
store, the shipped JSONL file sink, or an in-memory test sink all plug in
without touching the chooser. The JSONL shape is adapted from the leaderboard
``finops-router.sh`` model-call audit log (append-only JSON lines, one line per
real call).

Call semantics:

- ``ALLOW`` / ``WARN`` / ``FALLBACK`` decisions emit a ``CallRecord`` for the
  model that was actually chosen.
- A per-role cap ``STOP`` (issue #633) emits **no** ``CallRecord`` — no model
  call happened — but **does** emit a ``RefusalRecord``, because a cap refusal
  is itself a decision the metering store must account for: a budget alert is
  raised, never silently absorbed.
- A tenant ``STOP`` (issue #17) emits nothing at all, preserving the original
  behaviour; ``RefusalRecord`` exists for the per-role axis that requires the
  refusal to be metered.

Standalone module (stdlib only); no cross-package imports.

---knowledge---
module_id: gateway.finops.metering
system: gateway
app: finops
solution_class: enterprise
patterns: [injectable-hook, append-only-sink, refusal-visible]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [CallRecord, MeteringSink, RefusalRecord, JsonlMeteringSink, ListMeteringSink, NoopMeteringSink]
invariants: "a refused call is recorded too, so a cap decision is never invisible to cost attribution"
gotchas: "the chooser depends only on the record(record) protocol, so the Phase-5 store, a JSONL sink and a test sink all plug in unchanged"
related: ["#17", "#31"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class CallRecord:
    """One routed model call, recorded for cost attribution."""

    tenant_id: str
    agent_id: str
    task_class: str
    tier: str  # L0/L1/L2
    model: str
    provider: str
    estimated_cost_usd: float
    budget_action: str = "allow"
    complexity: Optional[float] = None
    reasons: List[str] = field(default_factory=list)
    # Per-role cap attribution (issue #633): the persona the call was
    # dispatched for and the ceiling it was checked against, when one applied.
    role_id: Optional[str] = None
    role_cap_usd: Optional[float] = None
    role_pct_used: Optional[float] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


@dataclass(frozen=True)
class RefusalRecord:
    """A refused call, recorded so a cap decision is never invisible.

    A ``STOP`` produces no ``CallRecord`` (no model call happened), but a
    per-role cap refusal is still a metered event: the workbook rule is that a
    budget alert is raised, never silently absorbed. This record carries the
    scope that refused (``role`` or ``tenant``) so the metering store can
    attribute the refusal to the ceiling that fired.
    """

    tenant_id: str
    agent_id: str
    task_class: str
    tier: str
    model: str
    provider: str
    estimated_cost_usd: float
    budget_action: str = "stop"
    reason: str = ""
    cap_usd: Optional[float] = None
    pct_used: Optional[float] = None
    scope: str = "tenant"  # role | tenant
    role_id: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        if payload.get("role_id") is None:
            payload["role_id"] = self.agent_id if self.scope == "role" else None
        return payload


@runtime_checkable
class MeteringSink(Protocol):
    """Interface the gateway/chooser calls to persist a call record."""

    def record(self, record: CallRecord) -> None:
        """Persist one routed model call (or refusal) record."""
        ...


class ListMeteringSink:
    """In-memory sink capturing records (used by tests and offline runs)."""

    def __init__(self) -> None:
        self.records: List[CallRecord] = []

    def record(self, record: CallRecord) -> None:
        self.records.append(record)


class JsonlMeteringSink:
    """Append-only JSONL file sink (fleet model-call audit pattern)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, record: CallRecord) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")

    def read_records(self) -> List[Dict[str, Any]]:
        """Read back and parse every line (for reports/evidence)."""
        if not self.path.is_file():
            return []
        records: List[Dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records


class NoopMeteringSink:
    """Discards records (default when no metering store is configured)."""

    def record(self, record: CallRecord) -> None:
        return None
