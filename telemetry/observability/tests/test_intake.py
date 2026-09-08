"""Intake tests: sink contract, end-to-end correlation, gateway absorption."""

from __future__ import annotations

import pytest

from telemetry.observability.intake import (
    MemorySink,
    NoopSink,
    TelemetryRecorder,
    current_trace_id,
)
from telemetry.observability.model import (
    KIND_GUARD,
    KIND_MODEL_CALL,
    KIND_STEP,
    KIND_TRACE,
    OUTCOME_DENIED,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    SERVICE_ENGINE,
    SERVICE_GATEWAY,
    SERVICE_GUARDRAILS,
)


def make_recorder(fixed_timestamp, fixed_clock):
    sink = MemorySink()
    rec = TelemetryRecorder(
        sink, clock=fixed_clock, timestamp=fixed_timestamp(60.0)
    )
    return rec, sink


class TestSinks:
    def test_noop_sink_discards(self):
        sink = NoopSink()
        sink.write_span("anything")  # must not raise
        assert True

    def test_memory_sink_records_and_resets(self, span_factory):
        sink = MemorySink()
        span = span_factory()
        sink.write_span(span)
        assert sink.spans == [span]
        sink.reset()
        assert sink.spans == []


class TestCorrelation:
    def test_full_request_shares_one_trace_id(self, fixed_timestamp,
                                              fixed_clock):
        """gateway -> guardrails -> engine all correlate end-to-end."""
        rec, sink = make_recorder(fixed_timestamp, fixed_clock)
        with rec.trace(tenant_id="acme", request_id="req-1",
                       service=SERVICE_ENGINE, name="task.run") as ctx:
            with rec.span(SERVICE_GUARDRAILS, KIND_GUARD, "policy.gate",
                          outcome=OUTCOME_SUCCESS):
                pass
            with rec.span(SERVICE_GATEWAY, KIND_MODEL_CALL, "model.call",
                          outcome=OUTCOME_SUCCESS, provider="anthropic",
                          model="claude-3-5-sonnet", input_tokens=100,
                          output_tokens=50, latency_ms=120.5,
                          estimated_cost_usd=0.0015):
                pass
            with rec.span(SERVICE_ENGINE, KIND_STEP, "agent.step",
                          outcome=OUTCOME_SUCCESS, latency_ms=40.0):
                pass
        spans = sink.spans
        # root + guard + model call + agent step = 4 spans, one trace id
        assert len(spans) == 4
        ids = {s.trace_id for s in spans}
        assert len(ids) == 1
        trace_id = next(iter(ids))
        assert trace_id == ctx.trace_id
        # correlation: every span line carries the same trace id
        assert all(s.trace_id == trace_id for s in spans)
        # root span (kind trace) has no parent; children chain to it
        root = [s for s in spans if s.kind == KIND_TRACE][0]
        children = [s for s in spans if s.parent_span_id == root.span_id]
        assert len(children) == 3

    def test_nested_span_parenting(self, fixed_timestamp, fixed_clock):
        rec, sink = make_recorder(fixed_timestamp, fixed_clock)
        with rec.trace(tenant_id="acme", service=SERVICE_ENGINE):
            with rec.span(SERVICE_ENGINE, KIND_STEP, "outer") as outer:
                with rec.span(SERVICE_GATEWAY, KIND_MODEL_CALL, "inner") as inner:
                    pass
                assert inner.parent_span_id == outer.span_id
        # after the trace, correlation id is gone (contextvar reset)
        assert current_trace_id() is None

    def test_exception_records_failed_not_success(self, fixed_timestamp,
                                                  fixed_clock):
        rec, sink = make_recorder(fixed_timestamp, fixed_clock)
        with pytest.raises(RuntimeError):
            with rec.trace(tenant_id="acme"):
                with rec.span(SERVICE_GATEWAY, KIND_MODEL_CALL, "boom"):
                    raise RuntimeError("provider exploded")
        outcomes = {s.outcome for s in sink.spans}
        assert OUTCOME_FAILED in outcomes
        assert OUTCOME_SUCCESS not in {s.outcome for s in sink.spans if s.name == "boom"}

    def test_explicit_outcome_survives(self, fixed_timestamp, fixed_clock):
        rec, sink = make_recorder(fixed_timestamp, fixed_clock)
        with rec.trace(tenant_id="acme"):
            with rec.span(SERVICE_GUARDRAILS, KIND_GUARD, "gate",
                          outcome=OUTCOME_DENIED):
                pass
        guard = [s for s in sink.spans if s.kind == KIND_GUARD][0]
        assert guard.outcome == OUTCOME_DENIED

    def test_span_without_trace_requires_tenant(self, fixed_timestamp,
                                                fixed_clock):
        rec, _ = make_recorder(fixed_timestamp, fixed_clock)
        with pytest.raises(RuntimeError):
            with rec.span(SERVICE_GATEWAY, KIND_MODEL_CALL, "x"):
                pass

    def test_latency_explicit_overrides_clock(self, fixed_timestamp,
                                              fixed_clock):
        rec, sink = make_recorder(fixed_timestamp, fixed_clock)
        with rec.trace(tenant_id="acme"):
            with rec.span(SERVICE_GATEWAY, KIND_MODEL_CALL, "call",
                          latency_ms=777.0):
                pass
        call = [s for s in sink.spans if s.kind == KIND_MODEL_CALL][0]
        assert call.latency_ms == 777.0

    def test_pending_span_finish_twice_raises(self, fixed_timestamp,
                                              fixed_clock):
        rec, sink = make_recorder(fixed_timestamp, fixed_clock)
        with rec.trace(tenant_id="acme"):
            with rec.span(SERVICE_GATEWAY, KIND_MODEL_CALL, "call") as pend:
                pass
            with pytest.raises(RuntimeError):
                pend.finish()


class TestStandaloneAndGateway:
    def test_emit_standalone_writes_single_span(self, fixed_timestamp,
                                                fixed_clock):
        rec, sink = make_recorder(fixed_timestamp, fixed_clock)
        span = rec.emit(SERVICE_GATEWAY, KIND_MODEL_CALL, "gateway.call",
                        outcome=OUTCOME_SUCCESS, tenant_id="acme",
                        provider="anthropic", model="claude-3-5-sonnet",
                        input_tokens=10, output_tokens=5, latency_ms=30.0)
        assert len(sink.spans) == 1
        assert sink.spans[0].trace_id == span.trace_id
        assert span.tenant_id == "acme"

    def test_emit_inside_trace_attaches(self, fixed_timestamp, fixed_clock):
        rec, sink = make_recorder(fixed_timestamp, fixed_clock)
        with rec.trace(tenant_id="acme", request_id="req-9"):
            rec.emit(SERVICE_GATEWAY, KIND_MODEL_CALL, "gateway.call",
                     outcome=OUTCOME_SUCCESS, provider="anthropic",
                     model="claude-3-5-sonnet", latency_ms=25.0)
        calls = [s for s in sink.spans if s.kind == KIND_MODEL_CALL]
        root = [s for s in sink.spans if s.kind == KIND_TRACE][0]
        assert len(calls) == 1
        assert calls[0].parent_span_id == root.span_id
        assert calls[0].request_id == "req-9"

    def test_emit_requires_tenant_outside_trace(self, fixed_clock):
        rec = TelemetryRecorder(MemorySink(), clock=fixed_clock)
        with pytest.raises(RuntimeError):
            rec.emit(SERVICE_GATEWAY, KIND_MODEL_CALL, "x")

    def test_ingest_gateway_call_maps_camelcase(self, fixed_timestamp,
                                                fixed_clock):
        """Consumes a GatewayCallRecord JSON dict (gateway/proxy shape)."""
        rec, sink = make_recorder(fixed_timestamp, fixed_clock)
        record = {
            "requestId": "gw-1",
            "ts": "2026-09-01T00:00:00Z",
            "tenantId": "acme",
            "agentId": "agent-1",
            "taskType": "summarize",
            "capability": "summarize",
            "taskClass": "generation",
            "tier": "MED",
            "provider": "anthropic",
            "model": "claude-3-5-sonnet",
            "outcome": "success",
            "inputTokens": 500,
            "outputTokens": 120,
            "latencyMs": 200.0,
            "estimatedCostUsd": 0.004,
            "budgetAction": "",
            "attempts": 1,
            "error": None,
        }
        span = rec.ingest_gateway_call(record)
        assert span.service == SERVICE_GATEWAY
        assert span.kind == KIND_MODEL_CALL
        assert span.tenant_id == "acme"
        assert span.provider == "anthropic"
        assert span.model == "claude-3-5-sonnet"
        assert span.input_tokens == 500
        assert span.output_tokens == 120
        assert span.tokens == 620
        assert span.latency_ms == 200.0
        assert span.estimated_cost_usd == 0.004
        assert span.outcome == OUTCOME_SUCCESS

    def test_ingest_gateway_call_requires_tenant(self, fixed_timestamp,
                                                 fixed_clock):
        rec, _ = make_recorder(fixed_timestamp, fixed_clock)
        with pytest.raises(ValueError):
            rec.ingest_gateway_call({"requestId": "x"})

    def test_ingest_gateway_call_inside_trace_uses_trace_id(
        self, fixed_timestamp, fixed_clock
    ):
        rec, sink = make_recorder(fixed_timestamp, fixed_clock)
        with rec.trace(tenant_id="acme", request_id="req-1"):
            rec.ingest_gateway_call({
                "requestId": "gw-1",
                "tenantId": "acme",
                "outcome": "success",
                "latencyMs": 10.0,
            })
        call = [s for s in sink.spans if s.kind == KIND_MODEL_CALL][0]
        assert call.trace_id != "gw-1"  # re-keyed to the active trace id

    def test_request_id_propagates_to_all_spans(self, fixed_timestamp,
                                                fixed_clock):
        rec, sink = make_recorder(fixed_timestamp, fixed_clock)
        with rec.trace(tenant_id="acme", request_id="req-abc"):
            rec.emit(SERVICE_ENGINE, KIND_STEP, "step", outcome=OUTCOME_SUCCESS)
        assert all(s.request_id == "req-abc" for s in sink.spans)
