"""Model tests: SpanRecord/Trace, vocabulary, JSON round-trip, quantile."""

from __future__ import annotations

import pytest

from telemetry.observability.model import (
    KIND_STEP,
    KIND_TRACE,
    OUTCOME_BLOCKED,
    OUTCOME_CACHE_HIT,
    OUTCOME_DENIED,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    SERVICES,
    SERVED_OUTCOMES,
    SPAN_KINDS,
    Trace,
    epoch_of,
    is_attempt,
    is_failure,
    is_served,
    quantile,
)
from telemetry.observability.tests.conftest import iso_at


class TestSpanRecord:
    def test_construct_with_required_fields(self, span_factory):
        span = span_factory()
        assert span.tenant_id == "acme"
        assert span.service == "gateway"
        assert span.tokens == 150
        assert span.is_served
        assert not span.is_failure
        assert span.is_attempt

    def test_json_roundtrip(self, span_factory):
        span = span_factory(request_id="req-9", error=None)
        record = span.to_dict()
        # camelCase shape (the fleet model-call-audit keys)
        assert record["tenantId"] == "acme"
        assert record["requestId"] == "req-9"
        assert record["inputTokens"] == 100
        rebuilt = type(span).from_dict(record)
        assert rebuilt == span

    def test_json_roundtrip_rounds_cost(self, span_factory):
        span = span_factory(estimated_cost_usd=0.0012, input_tokens=7,
                            output_tokens=3)
        rebuilt = type(span).from_dict(span.to_dict())
        assert rebuilt.estimated_cost_usd == pytest.approx(0.0012)

    def test_requires_trace_span_tenant(self):
        from telemetry.observability.model import SpanRecord

        with pytest.raises(ValueError):
            SpanRecord(trace_id="", span_id="s", tenant_id="t",
                       service="gateway", kind="model_call",
                       name="x", outcome="success", ts="2026-01-01T00:00:00Z")
        with pytest.raises(ValueError):
            SpanRecord(trace_id="tr", span_id="s", tenant_id="",
                       service="gateway", kind="model_call",
                       name="x", outcome="success", ts="2026-01-01T00:00:00Z")

    def test_rejects_unknown_service_kind_outcome(self, span_factory):
        with pytest.raises(ValueError):
            span_factory(service="not-a-pillar")
        with pytest.raises(ValueError):
            span_factory(kind="not-a-kind")
        with pytest.raises(ValueError):
            span_factory(outcome="not-an-outcome")

    def test_rejects_negative_tokens_and_latency(self, span_factory):
        with pytest.raises(ValueError):
            span_factory(input_tokens=-1)
        with pytest.raises(ValueError):
            span_factory(output_tokens=-1)
        with pytest.raises(ValueError):
            span_factory(latency_ms=-0.5)


class TestOutcomeHelpers:
    def test_served_outcomes_are_good(self):
        assert is_served(OUTCOME_SUCCESS)
        assert is_served(OUTCOME_CACHE_HIT)
        assert not is_served(OUTCOME_FAILED)

    def test_failure_outcomes_are_bad(self):
        assert is_failure(OUTCOME_FAILED)
        assert not is_failure(OUTCOME_SUCCESS)
        # policy decisions are neither served nor failures
        assert not is_served(OUTCOME_BLOCKED)
        assert not is_failure(OUTCOME_BLOCKED)
        assert not is_failure(OUTCOME_DENIED)

    def test_attempt_sample_excludes_policy(self):
        assert is_attempt(OUTCOME_SUCCESS)
        assert is_attempt(OUTCOME_FAILED)
        assert not is_attempt(OUTCOME_BLOCKED)
        assert not is_attempt(OUTCOME_DENIED)

    def test_consumed_vocabulary_matches_gateway_contract(self):
        # The phase-5 telemetry outcome set is the gateway's closed set.
        assert SERVED_OUTCOMES == frozenset({"success", "cache_hit"})
        assert set(SERVICES) == {
            "gateway", "guardrails", "engine", "agent_loop"
        }
        assert set(SPAN_KINDS) == {"trace", "model_call", "guard", "step"}


class TestTrace:
    def test_groups_spans_sorted_parents_first(self, span_factory):
        root = span_factory(trace_id="tr", span_id="a", kind=KIND_TRACE,
                            parent_span_id=None, ts=iso_at(0),
                            name="task.run")
        child = span_factory(trace_id="tr", span_id="b",
                             parent_span_id="a", ts=iso_at(1),
                             name="model.call")
        trace = Trace(trace_id="tr", tenant_id="acme", request_id="req-1",
                      started_at=iso_at(0), spans=[child, root])
        assert [s.span_id for s in trace.spans] == ["a", "b"]
        assert trace.root.span_id == "a"
        assert trace.total_tokens == child.tokens * 2
        assert trace.children_of("a")[0].span_id == "b"

    def test_duration_uses_root_window(self, span_factory):
        root = span_factory(trace_id="tr", span_id="a", kind=KIND_TRACE,
                            parent_span_id=None, ts=iso_at(0),
                            name="task.run")
        child = span_factory(trace_id="tr", span_id="b",
                             parent_span_id="a", ts=iso_at(5),
                             name="model.call")
        trace = Trace(trace_id="tr", tenant_id="acme", request_id="req-1",
                      started_at=iso_at(0), spans=[root, child])
        assert trace.duration_ms == 5 * 1000

    def test_add_span_rejects_mismatched_trace_or_tenant(self, span_factory):
        root = span_factory(trace_id="tr", span_id="a", kind=KIND_TRACE,
                            parent_span_id=None, ts=iso_at(0))
        trace = Trace(trace_id="tr", tenant_id="acme", request_id="r",
                      started_at=iso_at(0), spans=[root])
        other_trace = span_factory(trace_id="other", span_id="b")
        with pytest.raises(ValueError):
            trace.add_span(other_trace)
        other_tenant = span_factory(trace_id="tr", span_id="c", tenant_id="x")
        with pytest.raises(ValueError):
            trace.add_span(other_tenant)

    def test_trace_dict_roundtrip(self, span_factory):
        root = span_factory(trace_id="tr", span_id="a", kind=KIND_TRACE,
                            parent_span_id=None, ts=iso_at(0), name="r")
        child = span_factory(trace_id="tr", span_id="b", parent_span_id="a",
                             ts=iso_at(1), name="c")
        trace = Trace(trace_id="tr", tenant_id="acme", request_id="req-1",
                      started_at=iso_at(0), spans=[root, child])
        rebuilt = Trace.from_dict(trace.to_dict())
        assert rebuilt.request_id == "req-1"
        assert [s.span_id for s in rebuilt.spans] == ["a", "b"]

    def test_outcome_counts(self, span_factory):
        root = span_factory(trace_id="tr", span_id="a", kind=KIND_TRACE,
                            parent_span_id=None, ts=iso_at(0))
        f = span_factory(trace_id="tr", span_id="b", parent_span_id="a",
                         ts=iso_at(1), outcome=OUTCOME_FAILED)
        trace = Trace(trace_id="tr", tenant_id="acme", request_id="r",
                      started_at=iso_at(0), spans=[root, f])
        assert trace.outcome_counts[OUTCOME_FAILED] == 1
        assert trace.outcome_counts[OUTCOME_SUCCESS] == 1


class TestQuantile:
    def test_nearest_rank(self):
        values = [10.0, 20.0, 30.0, 40.0]
        assert quantile(values, 0.5) == 20.0
        assert quantile(values, 0.95) == 40.0

    def test_empty_returns_none(self):
        # An empty latency sample must NOT read as 0ms (no-false-green).
        assert quantile([], 0.95) is None

    def test_single_value(self):
        assert quantile([42.0], 0.99) == 42.0


class TestEpoch:
    def test_epoch_of_z_timestamp(self):
        assert epoch_of("1970-01-01T00:00:00Z") == 0.0
        assert epoch_of("2023-11-14T22:13:20Z") == 1_700_000_000.0

    def test_epoch_of_unparseable_is_zero(self):
        assert epoch_of("not-a-time") == 0.0

    def test_kind_step_is_valid(self):
        # engine/agent-loop steps are spans too
        from telemetry.observability.model import SpanRecord

        s = SpanRecord(trace_id="tr", span_id="x", tenant_id="t",
                       service="engine", kind=KIND_STEP, name="step",
                       outcome=OUTCOME_SUCCESS, ts="2026-01-01T00:00:00Z")
        assert s.kind == "step"
