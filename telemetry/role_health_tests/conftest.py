"""Pytest bootstrap + fixtures for telemetry/role_health (issue #637).

---knowledge---
module_id: telemetry.role_health_tests.conftest
system: telemetry
app: role_health_tests
solution_class: pattern
patterns: [path-bootstrap, fixture-injection]
derives_from: null
owner_sme: qa-sme
tier: L0
interfaces: ["fixtures consumed by the role_health suites"]
invariants: "the bootstrap inserts the repo root so the suites import their lanes no matter where pytest is invoked"
gotchas: "the directory is named role_health_tests rather than tests because duplicate basenames break pytest module naming"
related: ["#637", "#1510"]
do_not_duplicate: null
---knowledge---


``telemetry/`` carries no ``__init__.py`` (per-issue package directories), so
this inserts the repo root at the front of ``sys.path`` and every test imports
``telemetry.role_health`` plus the consumed sibling lanes
(``telemetry.metering`` / ``gateway.finops``) no matter where pytest is invoked.

The directory is deliberately named ``role_health_tests`` rather than
``tests``: ``scripts/pytest-suites.txt`` enumerates suites per path and duplicate
basenames break pytest's default rootdir-relative module naming — the same
reason the sibling telemetry suites live in uniquely named directories.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict

import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# --------------------------------------------------------------------------- #
# Fixed deterministic timestamps (UTC) — one per declared cadence, so an
# offline check is reproducible and a test never depends on the wall clock.
# --------------------------------------------------------------------------- #
NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z
MONTH = "2026-12"
T_DEC_01 = "2026-12-01T10:00:00Z"


def call_record(
    tenant: str = "platform",
    agent: str = "ceo",
    model: str = "deepseek-chat",
    provider: str = "deepseek",
    estimate: float = 0.01,
    action: str = "allow",
    task_class: str = "strategy",
    tier: str = "L2",
    ts: str = T_DEC_01,
) -> Dict[str, Any]:
    """A gateway/finops ``CallRecord`` dict (issue #17 shape, attached cost).

    ``agent`` is the persona the call was dispatched for, which is the role axis
    this lane groups by (the metering reporter's ``GROUP_AGENT`` dimension).
    """
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


@pytest.fixture
def metering_reporter():
    """A real issue-#33 ``UsageReporter`` over an ingested in-memory store.

    Returns ``(ingest, reporter)``: ``ingest(record)`` adds one merged-shape
    record, ``reporter()`` returns the durable rollup reader the role axis reads.
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


@pytest.fixture
def caps():
    """The LIVE declared role caps (consumed from registry + finops)."""
    from telemetry.role_health import load_role_caps

    return load_role_caps()
