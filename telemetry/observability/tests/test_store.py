"""Store tests: JSONL writer/reader round-trip, queries, honesty on corrupt."""

from __future__ import annotations

import json

import pytest

from telemetry.observability.intake import TelemetryRecorder
from telemetry.observability.model import (
    KIND_GUARD,
    KIND_MODEL_CALL,
    KIND_TRACE,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
)
from telemetry.observability.store import (
    CorruptRecordError,
    JsonlSpanSink,
    TraceStore,
)
from telemetry.observability.tests.conftest import iso_at


def write_spans(tmp_path, spans):
    path = str(tmp_path / "spans.jsonl")
    with JsonlSpanSink(path) as sink:
        for span in spans:
            sink.write_span(span)
    return path


class TestWriterReader:
    def test_sink_writes_one_json_line_per_span(self, tmp_path, span_factory):
        path = str(tmp_path / "spans.jsonl")
        with JsonlSpanSink(path) as sink:
            sink.write_span(span_factory(span_id="a"))
            sink.write_span(span_factory(span_id="b"))
        lines = open(path, encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            data = json.loads(line)
            assert data["tenantId"] == "acme"
            assert data["_schemaVersion"] == 1

    def test_store_reads_back_equal(self, tmp_path, span_factory):
        spans = [
            span_factory(span_id="a", ts=iso_at(0)),
            span_factory(span_id="b", ts=iso_at(1)),
        ]
        path = write_spans(tmp_path, spans)
        store = TraceStore(path=path)
        assert store.all_spans() == spans
        assert len(store) == 2

    def test_recorder_through_file_sink_roundtrip(
        self, tmp_path, fixed_timestamp, fixed_clock
    ):
        path = str(tmp_path / "spans.jsonl")
        with JsonlSpanSink(path) as sink:
            rec = TelemetryRecorder(
                sink, clock=fixed_clock, timestamp=fixed_timestamp(60.0)
            )
            with rec.trace(tenant_id="acme", request_id="req-1"):
                rec.emit("gateway", KIND_MODEL_CALL, "model.call",
                         outcome=OUTCOME_SUCCESS, provider="anthropic",
                         model="claude-3-5-sonnet", input_tokens=10,
                         output_tokens=5, latency_ms=50.0)
        store = TraceStore(path=path)
        assert len(store.trace_ids()) == 1
        trace = store.traces()[0]
        assert trace.request_id == "req-1"
        assert trace.total_tokens == 15

    def test_missing_store_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            TraceStore(path=str(tmp_path / "nope.jsonl"))

    def test_corrupt_line_raises_not_silently_skipped(
        self, tmp_path, span_factory
    ):
        """A corrupt store line must fail loudly (no-false-green)."""
        path = write_spans(tmp_path, [span_factory(span_id="ok")])
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("{ this is not json\n")
        with pytest.raises(CorruptRecordError):
            TraceStore(path=path)

    def test_source_selection_requires_exactly_one(self, span_factory):
        with pytest.raises(ValueError):
            TraceStore()
        with pytest.raises(ValueError):
            TraceStore(spans=[span_factory()], records=[span_factory().to_dict()])

    def test_construct_from_records_and_spans(self, span_factory):
        store_from_records = TraceStore(records=[span_factory().to_dict()])
        assert len(store_from_records) == 1
        store_from_spans = TraceStore(spans=[span_factory()])
        assert len(store_from_spans) == 1


class TestQueries:
    def make_store(self, span_factory):
        spans = [
            span_factory(trace_id="t1", span_id="a", kind=KIND_TRACE,
                         parent_span_id=None, ts=iso_at(0), name="root"),
            span_factory(trace_id="t1", span_id="b", parent_span_id="a",
                         ts=iso_at(1), outcome=OUTCOME_SUCCESS),
            span_factory(trace_id="t1", span_id="c", parent_span_id="a",
                         ts=iso_at(2), outcome=OUTCOME_FAILED),
            span_factory(trace_id="t2", span_id="d", kind=KIND_TRACE,
                         parent_span_id=None, ts=iso_at(3), tenant_id="other"),
            span_factory(trace_id="t2", span_id="e", parent_span_id="d",
                         ts=iso_at(4), tenant_id="other",
                         outcome=OUTCOME_SUCCESS),
            span_factory(trace_id="t3", span_id="f", kind=KIND_GUARD,
                         parent_span_id=None, ts=iso_at(5),
                         outcome=OUTCOME_SUCCESS, name="guard.decision"),
        ]
        return TraceStore(spans=spans), spans

    def test_tenant_filter(self, span_factory):
        store, _ = self.make_store(span_factory)
        assert {s.tenant_id for s in store.query(tenant_id="acme")} == {"acme"}
        assert len(store.tenants()) == 2

    def test_outcome_filter(self, span_factory):
        store, _ = self.make_store(span_factory)
        failed = store.query(outcome=OUTCOME_FAILED)
        assert len(failed) == 1
        assert failed[0].span_id == "c"

    def test_time_window_filter(self, span_factory):
        store, _ = self.make_store(span_factory)
        windowed = store.query(since_iso=iso_at(2), until_iso=iso_at(4))
        ids = {s.span_id for s in windowed}
        assert ids == {"c", "d", "e"}

    def test_traces_rebuild_grouped(self, span_factory):
        store, _ = self.make_store(span_factory)
        traces = store.traces()
        assert len(traces) == 3
        t1 = next(t for t in traces if t.trace_id == "t1")
        assert t1.request_id == "t1"  # root carries no explicit request_id
        assert len(t1.spans) == 3
        assert t1.root.span_id == "a"

    def test_spans_for_trace(self, span_factory):
        store, _ = self.make_store(span_factory)
        spans = store.spans_for_trace("t1")
        assert [s.span_id for s in spans] == ["a", "b", "c"]

    def test_attempt_spans_exclude_trace_and_policy(self, span_factory):
        store, _ = self.make_store(span_factory)
        attempts = store.attempt_spans(tenant_id="acme")
        # b (success) and c (failed) are attempts; a (trace root) is not
        assert {s.span_id for s in attempts} == {"b", "c"}

    def test_trace_ids_first_seen_order(self, span_factory):
        store, _ = self.make_store(span_factory)
        assert store.trace_ids() == ["t1", "t2", "t3"]


class TestSpanLinesHelper:
    def test_store_span_lines_jsonl(self, span_factory):
        from telemetry.observability.store import store_span_lines

        lines = store_span_lines([span_factory()])
        assert len(lines) == 1
        assert json.loads(lines[0])["spanId"] == "spn-1"
