"""Usage tests: per-tenant chargeback aggregation over the store."""

from __future__ import annotations

import json

import pytest

from telemetry.observability.model import (
    KIND_GUARD,
    KIND_MODEL_CALL,
    KIND_STEP,
    KIND_TRACE,
    OUTCOME_FAILED,
)
from telemetry.observability.store import TraceStore
from telemetry.observability.tests.conftest import iso_at
from telemetry.observability.usage import (
    GROUP_PROVIDER,
    GROUP_TENANT,
    UsageReporter,
)


def build_store(span_factory):
    spans = [
        # tenant acme
        span_factory(trace_id="t1", span_id="r", kind=KIND_TRACE,
                     parent_span_id=None, ts=iso_at(0), name="root"),
        span_factory(trace_id="t1", span_id="a", kind=KIND_MODEL_CALL,
                     parent_span_id="r", ts=iso_at(1),
                     provider="anthropic", model="claude-3-5-sonnet",
                     input_tokens=100, output_tokens=50,
                     latency_ms=100.0, estimated_cost_usd=0.010),
        span_factory(trace_id="t1", span_id="b", kind=KIND_MODEL_CALL,
                     parent_span_id="r", ts=iso_at(2), outcome=OUTCOME_FAILED,
                     provider="anthropic", model="claude-3-5-sonnet",
                     input_tokens=200, output_tokens=0,
                     latency_ms=5000.0, estimated_cost_usd=0.0),
        span_factory(trace_id="t1", span_id="c", kind=KIND_GUARD,
                     parent_span_id="r", ts=iso_at(3), name="guard",
                     input_tokens=0, output_tokens=0,
                     estimated_cost_usd=0.0),
        # tenant nimbus
        span_factory(trace_id="t2", span_id="d", kind=KIND_STEP,
                     parent_span_id=None, tenant_id="nimbus",
                     service="engine", ts=iso_at(4), provider=None,
                     model=None,
                     input_tokens=10, output_tokens=5,
                     latency_ms=20.0, estimated_cost_usd=0.001),
    ]
    return TraceStore(spans=spans)


class TestPerTenant:
    def test_tenant_rows_exclude_trace_and_guard(self, span_factory):
        store = build_store(span_factory)
        rows = UsageReporter(store).report()
        by_tenant = {r.tenant_id: r for r in rows}
        assert set(by_tenant) == {"acme", "nimbus"}
        acme = by_tenant["acme"]
        # 2 billable calls (model calls a+b); guard + trace excluded
        assert acme.calls == 2
        assert acme.served == 1
        assert acme.failed == 1
        assert acme.input_tokens == 300
        assert acme.output_tokens == 50
        assert acme.total_tokens == 350
        assert acme.estimated_cost_usd == pytest.approx(0.010)

    def test_time_window_filter(self, span_factory):
        store = build_store(span_factory)
        rows = UsageReporter(store).report(since_iso=iso_at(2),
                                           until_iso=iso_at(4))
        # only span b (acme) and step d (nimbus) fall in [2,4]
        total_calls = sum(r.calls for r in rows)
        assert total_calls == 2

    def test_detailed_by_provider(self, span_factory):
        store = build_store(span_factory)
        rows = UsageReporter(
            store, dimensions=(GROUP_TENANT, GROUP_PROVIDER)
        ).report()
        keyed = {(r.tenant_id, r.provider): r for r in rows}
        assert ("acme", "anthropic") in keyed
        assert keyed[("acme", "anthropic")].calls == 2
        # engine step for nimbus has no provider -> bucket under None
        assert ("nimbus", None) in keyed

    def test_json_report(self, span_factory):
        store = build_store(span_factory)
        text = UsageReporter(store).report_json()
        data = json.loads(text)
        assert data["dimensions"] == ["tenant"]
        assert len(data["rows"]) == 2


class TestValidation:
    def test_unknown_dimension_rejected(self, span_factory):
        store = build_store(span_factory)
        with pytest.raises(ValueError):
            UsageReporter(store, dimensions=("bogus",))
