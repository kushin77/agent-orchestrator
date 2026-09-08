"""Shared fixtures for the telemetry/observability test suite (issue #32).

Bootstraps the repo root onto ``sys.path`` so the PEP-420 namespace package
``telemetry.observability`` (and the consumed sibling contracts under
``gateway/``) import cleanly, and provides deterministic span/fixture helpers.
"""

from __future__ import annotations

import os
import sys

import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from telemetry.observability.model import (  # noqa: E402
    KIND_MODEL_CALL,
    OUTCOME_SUCCESS,
    SERVICE_GATEWAY,
    SpanRecord,
    now_utc_iso,
)

#: A fixed epoch so demo/test timestamps are deterministic.
BASE_EPOCH = 1_700_000_000.0  # 2023-11-14T22:13:20Z


def iso_at(epoch: float) -> str:
    """ISO-8601 ``Z`` string for an epoch offset from the test base."""
    from datetime import UTC, datetime

    return datetime.fromtimestamp(BASE_EPOCH + epoch, UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


@pytest.fixture
def span_factory():
    """Build deterministic SpanRecords with sensible defaults."""

    def make(**overrides):
        fields = {
            "trace_id": "trc-1",
            "span_id": "spn-1",
            "tenant_id": "acme",
            "service": SERVICE_GATEWAY,
            "kind": KIND_MODEL_CALL,
            "name": "model.call",
            "outcome": OUTCOME_SUCCESS,
            "ts": iso_at(0),
            "provider": "anthropic",
            "model": "claude-3-5-sonnet",
            "tier": "MED",
            "input_tokens": 100,
            "output_tokens": 50,
            "latency_ms": 80.0,
            "estimated_cost_usd": 0.001,
        }
        fields.update(overrides)
        return SpanRecord(**fields)

    return make


@pytest.fixture
def fixed_timestamp():
    """A timestamp callable that advances by ``step`` seconds each call."""
    counters = {"n": 0}

    def make(step: float = 60.0):
        def _next() -> str:
            counters["n"] += 1
            return iso_at(counters["n"] * step)

        return _next

    return make


@pytest.fixture
def fixed_clock():
    """A monotonic clock callable that advances 1.0s per call."""
    state = {"t": 0.0}

    def _clock() -> float:
        state["t"] += 1.0
        return state["t"]

    return _clock


def load_slo_dir() -> str:
    """Absolute path to the packaged ``slo_templates`` directory."""
    pkg = os.path.join(REPO_ROOT, "telemetry", "observability")
    return os.path.join(pkg, "slo_templates")


__all__ = ["REPO_ROOT", "BASE_EPOCH", "iso_at", "span_factory",
           "fixed_timestamp", "fixed_clock", "load_slo_dir", "now_utc_iso"]
