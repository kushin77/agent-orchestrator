"""Dashboard tests: static HTML + JSON + terminal report generation."""

from __future__ import annotations

import json

from telemetry.observability.breach import BreachDetector
from telemetry.observability.dashboard import Dashboard
from telemetry.observability.model import (
    KIND_MODEL_CALL,
    KIND_TRACE,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
)
from telemetry.observability.slos import (
    KIND_AVAILABILITY,
    SloDefinition,
    SloEvaluator,
)
from telemetry.observability.store import TraceStore
from telemetry.observability.tests.conftest import iso_at
from telemetry.observability.usage import UsageReporter


def availability(tenant="acme", target=0.95, window=3600):
    return SloDefinition(
        name=f"availability:{tenant}", tenant_id=tenant,
        kind=KIND_AVAILABILITY, target_ratio=target,
        window_seconds=window,
    )


def build_store(span_factory, failures=0, total=10, tenant="acme"):
    spans = [span_factory(trace_id="tr", span_id="r", kind=KIND_TRACE,
                          parent_span_id=None, tenant_id=tenant,
                          ts=iso_at(0), name="root")]
    for i in range(total):
        outcome = OUTCOME_FAILED if i < failures else OUTCOME_SUCCESS
        spans.append(span_factory(
            trace_id="tr", span_id=f"s{i}", kind=KIND_MODEL_CALL,
            parent_span_id="r", tenant_id=tenant, ts=iso_at(1 + i),
            outcome=outcome, input_tokens=100, output_tokens=50,
            latency_ms=80.0, estimated_cost_usd=0.001,
        ))
    return TraceStore(spans=spans)


def make_dashboard(store, slo_results):
    alerts = BreachDetector().detect(slo_results)
    usage_rows = UsageReporter(store).report()
    return Dashboard(store, slo_results=slo_results,
                     usage_rows=usage_rows, alerts=alerts)


class TestData:
    def test_data_sections(self, span_factory):
        store = build_store(span_factory, failures=0)
        result = SloEvaluator(store).evaluate(availability())
        dash = make_dashboard(store, [result])
        data = dash.data()
        assert data["schemaVersion"] == 1
        assert data["tenants"][0]["tenantId"] == "acme"
        assert data["tenants"][0]["calls"] == 10
        assert data["sloResults"][0]["verdict"] == "OK"
        assert data["alerts"] == []
        # outcome mix counts every span including the trace root
        assert data["outcomes"][OUTCOME_SUCCESS] == 11

    def test_burn_rate_view_sorted_worst_first(self, span_factory):
        ok_store = build_store(span_factory, failures=0, tenant="acme")
        bad_store = build_store(span_factory, failures=6, tenant="nimbus")
        merged = TraceStore(spans=ok_store.all_spans() + bad_store.all_spans())
        results = [
            SloEvaluator(merged).evaluate(availability(tenant="acme")),
            SloEvaluator(merged).evaluate(availability(tenant="nimbus")),
        ]
        dash = make_dashboard(merged, results)
        burn = dash.data()["burnRate"]
        assert burn[0]["tenantId"] == "nimbus"  # worst first
        assert burn[0]["verdict"] == "BREACHED"

    def test_guard_decisions_do_not_inflate_served(self, span_factory):
        """Guardrail successes are not serve attempts in the tenant row."""
        from telemetry.observability.model import KIND_GUARD, OUTCOME_DENIED

        spans = build_store(span_factory, failures=0).all_spans()
        spans.append(span_factory(trace_id="tr", span_id="g", kind=KIND_GUARD,
                                  parent_span_id="r", ts=iso_at(30),
                                  outcome=OUTCOME_DENIED))
        store = TraceStore(spans=spans)
        result = SloEvaluator(store).evaluate(availability())
        dash = make_dashboard(store, [result])
        row = dash.data()["tenants"][0]
        assert row["calls"] == 10
        assert row["served"] == 10  # guard denied never counted as served
        assert row["attempts"] == 10

    def test_data_json_serializable(self, span_factory):
        store = build_store(span_factory, failures=2)
        result = SloEvaluator(store).evaluate(availability())
        dash = make_dashboard(store, [result])
        json.dumps(dash.data())  # must not raise


class TestFiles:
    def test_render_html_and_json(self, tmp_path, span_factory):
        store = build_store(span_factory, failures=2)
        result = SloEvaluator(store).evaluate(availability())
        dash = make_dashboard(store, [result])
        html_path = dash.render_html(str(tmp_path / "dash.html"))
        json_path = dash.render_json(str(tmp_path / "dash.json"))
        assert html_path.endswith("dash.html")
        assert json_path.endswith("dash.json")
        html = open(html_path, encoding="utf-8").read()
        assert "observability dashboard" in html
        assert "acme" in html
        assert "BREACHED" in html
        data = json.load(open(json_path, encoding="utf-8"))
        assert data["tenants"][0]["tenantId"] == "acme"

    def test_render_terminal_report_shows_outcomes_and_slos(
        self, span_factory
    ):
        store = build_store(span_factory, failures=2)
        result = SloEvaluator(store).evaluate(availability())
        dash = make_dashboard(store, [result])
        report = dash.render_terminal_report()
        assert "observability health report" in report
        assert "OUTCOMES (actual results)" in report
        assert "acme" in report
        assert "BREACHED" in report

    def test_terminal_report_empty_store(self, span_factory):
        store = TraceStore(spans=[])
        dash = Dashboard(store)
        report = dash.render_terminal_report()
        assert "no spans recorded" in report
