"""Pytest bootstrap + fixtures for telemetry/metering (issue #33).

``telemetry/`` carries no ``__init__.py`` (per-issue package directories,
mirroring ``registry/`` and ``telemetry/observability``), so this inserts the
repo root at the front of ``sys.path`` and every test imports
``telemetry.metering.*`` no matter where pytest is invoked from.

Also provides the merged-record-shape builders (ModelCallEvent / CallRecord /
MeteringRecord / gateway-record dicts) that the intake consumes — each with a
fixed, deterministic timestamp so rollup/budget tests are stable.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# --------------------------------------------------------------------------- #
# Fixed deterministic timestamps (UTC) for rollup-window tests
# --------------------------------------------------------------------------- #
T_SEP_07 = "2026-09-07T10:00:00Z"  # September day 07
T_SEP_08 = "2026-09-08T10:00:00Z"  # September day 08
T_SEP_08_LATE = "2026-09-08T18:30:00Z"
T_OCT_01 = "2026-10-01T09:00:00Z"  # October (a different month bucket)


# --------------------------------------------------------------------------- #
# Merged record-shape builders (dicts exactly as the JSONL feed would carry)
# --------------------------------------------------------------------------- #
def model_call_event(
    provider: str = "gemini",
    model: str = "gemini-2.5-flash",
    tenant: str = "acme",
    agent: str = "coder-1",
    logical_key: str = "MED",
    status: str = "success",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    ts: str = T_SEP_08,
    usage: Optional[Dict[str, Any]] = None,
    include_usage: bool = True,
) -> Dict[str, Any]:
    """A gateway/providers ``ModelCallEvent`` dict (issue #15 shape).

    ``include_usage=False`` (or ``usage=None`` explicitly with that flag)
    emits ``usage: None`` — a provider event that carried no usage block
    (e.g. a transport failure).
    """
    if not include_usage:
        usage = None
    elif usage is None:
        usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tokens": input_tokens + output_tokens,
        }
    return {
        "provider": provider,
        "model": model,
        "tenant_id": tenant,
        "agent_id": agent,
        "logical_key": logical_key,
        "status": status,
        "usage": usage,
        "latency_ms": 320.0,
        "attempts": 1,
        "error_type": None,
        "error_detail": None,
        "ts": ts,
    }


def call_record(
    tenant: str = "globex",
    agent: str = "arch-1",
    model: str = "deepseek-reasoner",
    provider: str = "deepseek",
    estimate: float = 0.0042,
    action: str = "allow",
    task_class: str = "research",
    tier: str = "L1",
    ts: str = T_SEP_08,
) -> Dict[str, Any]:
    """A gateway/finops ``CallRecord`` dict (issue #17 shape, no tokens)."""
    return {
        "tenant_id": tenant,
        "agent_id": agent,
        "task_class": task_class,
        "tier": tier,
        "model": model,
        "provider": provider,
        "estimated_cost_usd": estimate,
        "budget_action": action,
        "complexity": 40.0,
        "reasons": ["cheapest-capable"],
        "timestamp": ts,
    }


def metering_record(
    tenant: str = "acme",
    agent: str = "coder-1",
    model_tier: str = "LOW",
    request_id: str = "req-1",
    outcome: str = "provider",
    input_tokens: int = 200,
    output_tokens: int = 50,
    cached: bool = False,
    zero_cost: bool = False,
    ts: str = T_SEP_08,
) -> Dict[str, Any]:
    """A gateway/limits ``MeteringRecord`` dict (issue #19 shape)."""
    return {
        "tenant": tenant,
        "agent": agent,
        "model_tier": model_tier,
        "task_type": "summarize",
        "request_id": request_id,
        "outcome": outcome,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_key": None,
        "cached": cached,
        "zero_cost": zero_cost,
        "reason": None,
        "at": ts,
    }


def gateway_record(
    tenant_id: str = "acme",
    agent_id: Optional[str] = "coder-1",
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-5",
    outcome: str = "success",
    input_tokens: int = 800,
    output_tokens: int = 200,
    estimated_cost_usd: Optional[float] = None,
    tier: str = "MED",
    ts: str = T_SEP_08,
) -> Dict[str, Any]:
    """A camelCase gateway-proxy / observability record (issue #32 shape)."""
    return {
        "requestId": "gw-1",
        "tenantId": tenant_id,
        "agentId": agent_id,
        "taskType": "summarize",
        "outcome": outcome,
        "ts": ts,
        "provider": provider,
        "model": model,
        "tier": tier,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "latencyMs": 120.5,
        "estimatedCostUsd": estimated_cost_usd,
        "error": None,
    }


@pytest.fixture()
def memory_store():
    """A fresh in-memory usage store wired to the default rate cards."""
    from telemetry.metering.intake import MeteringIntake
    from telemetry.metering.store import MemoryUsageStore

    store = MemoryUsageStore()
    intake = MeteringIntake(store=store)
    return store, intake
