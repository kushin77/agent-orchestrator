"""Pytest bootstrap + fixtures for telemetry/budgets (issue #34).

``telemetry/`` carries no ``__init__.py`` (per-issue package directories),
so this inserts the repo root at the front of ``sys.path`` and every test
imports ``telemetry.budgets.*`` (and the consumed ``telemetry.metering`` /
``telemetry.observability`` packages) no matter where pytest is invoked.

Also provides deterministic merged-record-shape builders (the metering-feed
dicts issue #33 consumes) and a fixture that builds a real
``telemetry.metering.report.UsageReporter`` over ingested records, so the
budget/quota integration tests exercise the actual issue-#33 feed.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# --------------------------------------------------------------------------- #
# Fixed deterministic timestamps (UTC)
# --------------------------------------------------------------------------- #
T_SEP_07 = "2026-09-07T10:00:00Z"
T_SEP_08 = "2026-09-08T10:00:00Z"
DAY_08 = "2026-09-08"
MONTH_SEP = "2026-09"


# --------------------------------------------------------------------------- #
# Metering-feed record builders (dicts exactly as issue #33 intake consumes)
# --------------------------------------------------------------------------- #
def call_record(
    tenant: str = "acme",
    agent: str = "coder-1",
    model: str = "deepseek-chat",
    provider: str = "deepseek",
    estimate: float = 0.01,
    action: str = "allow",
    task_class: str = "code-author",
    tier: str = "L0",
    ts: str = T_SEP_08,
) -> Dict[str, Any]:
    """A gateway/finops ``CallRecord`` dict (issue #17 shape, attached cost)."""
    return {
        "tenant_id": tenant,
        "agent_id": agent,
        "task_class": task_class,
        "tier": tier,
        "model": model,
        "provider": provider,
        "estimated_cost_usd": estimate,
        "budget_action": action,
        "complexity": 20.0,
        "reasons": ["cheapest-capable"],
        "timestamp": ts,
    }


def model_call_event(
    tenant: str = "acme",
    agent: str = "coder-1",
    provider: str = "gemini",
    model: str = "gemini-2.5-flash",
    status: str = "success",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    ts: str = T_SEP_08,
) -> Dict[str, Any]:
    """A gateway/providers ``ModelCallEvent`` dict (issue #15 shape)."""
    return {
        "provider": provider,
        "model": model,
        "tenant_id": tenant,
        "agent_id": agent,
        "logical_key": "MED",
        "status": status,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tokens": input_tokens + output_tokens,
        },
        "latency_ms": 120.0,
        "attempts": 1,
        "error_type": None,
        "error_detail": None,
        "ts": ts,
    }


@pytest.fixture
def metering_reporter():
    """A real issue-#33 ``UsageReporter`` over an ingested in-memory store.

    Returns a zero-arg factory; call ``ingest(record_dict)`` to add a record
    then ``reporter()`` for the durable rollup reader.
    """
    from telemetry.metering.intake import MeteringIntake
    from telemetry.metering.report import UsageReporter
    from telemetry.metering.store import MemoryUsageStore

    store = MemoryUsageStore()
    intake = MeteringIntake(store=store)

    def ingest(source: Dict[str, Any]) -> None:
        outcome = intake.ingest(source)
        assert not outcome.duplicate

    def reporter() -> UsageReporter:
        return UsageReporter(store)

    return ingest, reporter
