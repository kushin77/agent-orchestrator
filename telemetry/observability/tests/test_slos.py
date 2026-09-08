"""SLO tests: definitions, templates, honest evaluation + negatives."""

from __future__ import annotations

import pytest

from telemetry.observability.model import (
    KIND_GUARD,
    KIND_MODEL_CALL,
    KIND_STEP,
    KIND_TRACE,
    OUTCOME_DENIED,
    OUTCOME_FAILED,
    OUTCOME_NO_HEALTHY_ROUTE,
    OUTCOME_SUCCESS,
)
from telemetry.observability.slos import (
    KIND_AVAILABILITY,
    KIND_COST,
    KIND_LATENCY,
    VERDICT_AT_RISK,
    VERDICT_BREACHED,
    VERDICT_NO_DATA,
    VERDICT_OK,
    SloDefinition,
    SloEvaluator,
    definitions_from_templates,
    load_slo_templates,
)
from telemetry.observability.store import TraceStore
from telemetry.observability.tests.conftest import iso_at, load_slo_dir


def make_store(spans):
    return TraceStore(spans=spans)


def availability_def(target=0.95, tenant="acme", window=3600):
    return SloDefinition(
        name=f"availability:{tenant}", tenant_id=tenant,
        kind=KIND_AVAILABILITY, target_ratio=target,
        window_seconds=window,
    )


def latency_def(target_ms=250.0, pct=0.95, tenant="acme", window=3600):
    return SloDefinition(
        name=f"latency:{tenant}", tenant_id=tenant, kind=KIND_LATENCY,
        target_ms=target_ms, percentile=pct, window_seconds=window,
    )


def cost_def(budget=1.0, tenant="acme", window=3600):
    return SloDefinition(
        name=f"cost:{tenant}", tenant_id=tenant, kind=KIND_COST,
        budget_usd=budget, window_seconds=window,
    )


def root(span_factory, tid="tr", tenant="acme", t=0.0, name="root"):
    return span_factory(trace_id=tid, span_id=f"{tid}-r", kind=KIND_TRACE,
                        parent_span_id=None, tenant_id=tenant, ts=iso_at(t),
                        name=name, estimated_cost_usd=0.0)


def attempt(span_factory, tid="tr", sid="a", tenant="acme", t=1.0,
            outcome=OUTCOME_SUCCESS, latency=50.0, kind=KIND_MODEL_CALL,
            cost=0.0, **extra):
    return span_factory(trace_id=tid, span_id=sid, kind=kind,
                        parent_span_id=f"{tid}-r", tenant_id=tenant,
                        ts=iso_at(t), outcome=outcome, latency_ms=latency,
                        estimated_cost_usd=cost, **extra)


class TestDefinitionValidation:
    def test_availability_requires_target_ratio(self):
        with pytest.raises(ValueError):
            SloDefinition(name="a", tenant_id="t", kind=KIND_AVAILABILITY)
        with pytest.raises(ValueError):
            availability_def(target=1.5)

    def test_latency_requires_target_ms(self):
        with pytest.raises(ValueError):
            SloDefinition(name="l", tenant_id="t", kind=KIND_LATENCY,
                          percentile=0.95)
        with pytest.raises(ValueError):
            latency_def(target_ms=-1)

    def test_cost_requires_budget(self):
        with pytest.raises(ValueError):
            SloDefinition(name="c", tenant_id="t", kind=KIND_COST)

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError):
            SloDefinition(name="x", tenant_id="t", kind="mystery",
                          target_ratio=0.9)

    def test_window_must_be_positive(self):
        with pytest.raises(ValueError):
            availability_def(window=0)


class TestAvailability:
    def test_healthy_tenants_are_ok(self, span_factory):
        spans = [root(span_factory)]
        for i in range(10):
            spans.append(attempt(span_factory, sid=f"s{i}", t=1.0 + i))
        store = make_store(spans)
        result = SloEvaluator(store).evaluate(availability_def())
        assert result.verdict == VERDICT_OK
        assert result.attempts == 10
        assert result.good_count == 10
        assert result.is_ok

    def test_failures_breach_availability(self, span_factory):
        spans = [root(span_factory)]
        # 4 of 10 attempts fail -> ratio 0.6 << 0.95 target
        for i in range(10):
            outcome = OUTCOME_FAILED if i < 4 else OUTCOME_SUCCESS
            spans.append(attempt(span_factory, sid=f"s{i}", t=1.0 + i,
                                 outcome=outcome))
        result = SloEvaluator(make_store(spans)).evaluate(availability_def())
        assert result.verdict == VERDICT_BREACHED
        assert result.budget_consumed_ratio > 1.0
        assert not result.is_ok

    def test_error_budget_at_risk_is_not_ok(self, span_factory):
        # 1 bad in 40 = 2.5% bad == 50% of a 5% error budget -> AT_RISK
        spans = [root(span_factory)]
        for i in range(40):
            outcome = OUTCOME_FAILED if i == 0 else OUTCOME_SUCCESS
            spans.append(attempt(span_factory, sid=f"s{i}", t=1.0 + i,
                                 outcome=outcome))
        result = SloEvaluator(make_store(spans)).evaluate(availability_def())
        assert result.verdict == VERDICT_AT_RISK
        assert not result.is_ok

    def test_policy_denials_do_not_pollute_sample(self, span_factory):
        # guard decisions (denied) + trace root are not serve attempts
        spans = [
            root(span_factory),
            span_factory(trace_id="tr", span_id="g1", kind=KIND_GUARD,
                         parent_span_id="tr-r", ts=iso_at(1),
                         outcome=OUTCOME_DENIED),
            attempt(span_factory, sid="s1", t=2.0),
            attempt(span_factory, sid="s2", t=3.0),
        ]
        result = SloEvaluator(make_store(spans)).evaluate(availability_def())
        assert result.attempts == 2
        assert result.good_count == 2
        assert result.verdict == VERDICT_OK


class TestLatency:
    def test_latency_within_target_is_ok(self, span_factory):
        spans = [root(span_factory)]
        for i in range(20):
            spans.append(attempt(span_factory, sid=f"s{i}", t=1.0 + i,
                                 latency=100.0))
        result = SloEvaluator(make_store(spans)).evaluate(latency_def())
        assert result.verdict == VERDICT_OK
        assert result.measured_ms == 100.0

    def test_p95_over_target_is_breached(self, span_factory):
        spans = [root(span_factory)]
        for i in range(18):
            spans.append(attempt(span_factory, sid=f"s{i}", t=1.0 + i,
                                 latency=100.0))
        for i in range(18, 20):
            spans.append(attempt(span_factory, sid=f"s{i}", t=1.0 + i,
                                 latency=1000.0))
        result = SloEvaluator(make_store(spans)).evaluate(latency_def())
        assert result.measured_ms == 1000.0
        assert result.verdict == VERDICT_BREACHED

    def test_latency_at_allowance_boundary_at_risk(self, span_factory):
        # 1 slow in 20 == exactly the 5% allowance -> AT_RISK (never silent OK)
        spans = [root(span_factory)]
        for i in range(19):
            spans.append(attempt(span_factory, sid=f"s{i}", t=1.0 + i,
                                 latency=100.0))
        spans.append(attempt(span_factory, sid="slow", t=20.0, latency=900.0))
        result = SloEvaluator(make_store(spans)).evaluate(latency_def())
        assert result.verdict == VERDICT_AT_RISK


class TestCost:
    def test_within_budget_is_ok(self, span_factory):
        spans = [root(span_factory)]
        for i in range(10):
            spans.append(attempt(span_factory, sid=f"s{i}", t=1.0 + i,
                                 cost=0.05))
        result = SloEvaluator(make_store(spans)).evaluate(cost_def(budget=1.0))
        assert result.verdict == VERDICT_OK
        assert result.spent_usd == pytest.approx(0.5)

    def test_over_budget_is_breached(self, span_factory):
        spans = [root(span_factory)]
        for i in range(5):
            spans.append(attempt(span_factory, sid=f"s{i}", t=1.0 + i,
                                 cost=0.30))
        result = SloEvaluator(make_store(spans)).evaluate(cost_def(budget=1.0))
        assert result.spent_usd == pytest.approx(1.5)
        assert result.verdict == VERDICT_BREACHED

    def test_cost_near_budget_at_risk(self, span_factory):
        spans = [root(span_factory)]
        for i in range(9):
            spans.append(attempt(span_factory, sid=f"s{i}", t=1.0 + i,
                                 cost=0.10))
        result = SloEvaluator(make_store(spans)).evaluate(cost_def(budget=1.0))
        assert result.spent_usd == pytest.approx(0.9)
        assert result.verdict == VERDICT_AT_RISK


class TestNoDataIsNotOk:
    def test_tenant_missing_the_window_is_no_data(self, span_factory):
        """A tenant with no telemetry in the SLO window is NOT-OK."""
        spans = [root(span_factory, tenant="acme"),
                 attempt(span_factory, tenant="acme", sid="s1", t=1.0)]
        store = make_store(spans)
        result = SloEvaluator(store).evaluate(
            availability_def(tenant="ghost-tenant")
        )
        assert result.verdict == VERDICT_NO_DATA
        assert result.missed_window is True
        assert not result.is_ok

    def test_data_but_no_serve_attempts_is_no_data(self, span_factory):
        """Heartbeats (trace roots / guard decisions) are not outcomes."""
        spans = [
            root(span_factory, tenant="acme"),
            span_factory(trace_id="tr", span_id="g1", kind=KIND_GUARD,
                         parent_span_id="tr-r", tenant_id="acme",
                         ts=iso_at(1), outcome=OUTCOME_DENIED),
        ]
        result = SloEvaluator(make_store(spans)).evaluate(
            availability_def(tenant="acme")
        )
        assert result.verdict == VERDICT_NO_DATA
        assert result.missed_window is False  # data exists, but no outcomes

    def test_latency_no_data_not_ok(self, span_factory):
        result = SloEvaluator(make_store([])).evaluate(
            latency_def(tenant="ghost")
        )
        assert result.verdict == VERDICT_NO_DATA

    def test_cost_no_data_not_ok(self, span_factory):
        result = SloEvaluator(make_store([])).evaluate(cost_def(tenant="ghost"))
        assert result.verdict == VERDICT_NO_DATA
        assert result.missed_window is True

    def test_outcome_mix_honest(self, span_factory):
        # gateway call records feed availability directly (real outcomes)
        spans = [root(span_factory)]
        for i in range(5):
            spans.append(attempt(span_factory, sid=f"s{i}", t=1.0 + i,
                                 outcome=OUTCOME_NO_HEALTHY_ROUTE))
        result = SloEvaluator(make_store(spans)).evaluate(availability_def())
        assert result.verdict == VERDICT_BREACHED
        assert result.good_count == 0


class TestTemplates:
    def test_packaged_templates_load(self):
        templates = load_slo_templates(load_slo_dir())
        kinds = {t.kind for t in templates}
        assert kinds == {KIND_AVAILABILITY, KIND_LATENCY, KIND_COST}
        by_name = {t.name: t for t in templates}
        assert by_name["availability-requests"].target_ratio == 0.95
        assert by_name["latency-p95"].target_ms == 250.0
        assert by_name["latency-p95"].percentile == 0.95
        assert by_name["cost-budget"].budget_usd == 25.0

    def test_missing_template_dir_raises(self, tmp_path):
        with pytest.raises(OSError):
            load_slo_templates(str(tmp_path / "does-not-exist"))

    def test_invalid_template_yaml_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("spec:\n  sloKind: [unclosed\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_slo_templates(str(tmp_path))

    def test_instantiate_scopes_to_tenant(self, span_factory):
        templates = load_slo_templates(load_slo_dir())
        tpl = next(t for t in templates if t.kind == KIND_AVAILABILITY)
        definition = tpl.instantiate("acme")
        assert definition.tenant_id == "acme"
        assert definition.name.startswith("availability-requests:acme")
        assert definition.kind == KIND_AVAILABILITY

    def test_definitions_from_templates_cartesian(self):
        templates = load_slo_templates(load_slo_dir())
        definitions = definitions_from_templates(templates, ["a", "b"])
        assert len(definitions) == 3 * 2

    def test_templates_evaluate_end_to_end(self, span_factory):
        """Real templates + real spans: a healthy tenant passes."""
        spans = [root(span_factory)]
        for i in range(10):
            spans.append(attempt(span_factory, sid=f"s{i}", t=1.0 + i,
                                 cost=0.05, latency=100.0))
        templates = load_slo_templates(load_slo_dir())
        definitions = definitions_from_templates(templates, ["acme"])
        results = SloEvaluator(make_store(spans)).evaluate_all(definitions)
        assert len(results) == 3
        assert all(r.verdict == VERDICT_OK for r in results)


class TestServiceFilter:
    def test_service_filter_restricts_sample(self, span_factory):
        spans = [root(span_factory)]
        for i in range(5):
            spans.append(attempt(span_factory, sid=f"g{i}", t=1.0 + i,
                                 outcome=OUTCOME_FAILED))
        for i in range(5):
            spans.append(attempt(span_factory, sid=f"s{i}", t=6.0 + i,
                                 kind=KIND_STEP, service="engine",
                                 outcome=OUTCOME_SUCCESS))
        definition = SloDefinition(
            name="engine-steps", tenant_id="acme", kind=KIND_AVAILABILITY,
            target_ratio=0.99, window_seconds=3600, service="engine",
        )
        result = SloEvaluator(make_store(spans)).evaluate(definition)
        # only the engine step spans count -> all success
        assert result.attempts == 5
        assert result.verdict == VERDICT_OK
