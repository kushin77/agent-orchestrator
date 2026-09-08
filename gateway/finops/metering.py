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

- ``ALLOW`` / ``WARN`` / ``FALLBACK`` decisions emit a record for the model
  that was actually chosen.
- ``STOP`` (budget block) emits nothing — no call was made.

Standalone module (stdlib only); no cross-package imports.
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
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


@runtime_checkable
class MeteringSink(Protocol):
    """Interface the gateway/chooser calls to persist a call record."""

    def record(self, record: CallRecord) -> None:
        """Persist one routed model call."""
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
