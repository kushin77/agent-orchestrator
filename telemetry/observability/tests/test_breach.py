"""Breach tests: alerts fire on actual outcomes, never on heartbeat presence."""

from __future__ import annotations

import pytest

from telemetry.observability.breach import (
    REASON_AT_RISK,
    REASON_BREACHED,
    REASON_MISSING_WINDOW,
    REASON_NO_ATTEMPTS,
    SEV_CRITICAL,
    SEV_WARNING,
    AlertPolicy,
    BreachDetector,
)
from telemetry.observability.model import (
    KIND_TRACE,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
)
from telemetry.observability.slos import (
    KIND_AVAILABILITY,
    VERDICT_AT_RISK,
    VERDICT_BREACHED,
    VERDICT_NO_DATA,
    SloDefinition,
    SloEvaluator,
)
from telemetry.observability.store import TraceStore
from telemetry.observability.tests.conftest import iso_at


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
            trace_id="tr", span_id=f"s{i}", parent_span_id="r",
            tenant_id=tenant, ts=iso_at(1 + i), outcome=outcome,
        ))
    return TraceStore(spans=spans)


def eval_result(store, definition):
    return SloEvaluator(store).evaluate(definition)


class TestDetector:
    def test_healthy_no_alerts(self, span_factory):
        result = eval_result(build_store(span_factory, failures=0),
                             availability())
        assert result.verdict == "OK"
        alerts = BreachDetector().detect([result])
        assert alerts == []

    def test_breach_alerts_critical(self, span_factory):
        result = eval_result(build_store(span_factory, failures=4),
                             availability())
        assert result.verdict == VERDICT_BREACHED
        alerts = BreachDetector().detect([result])
        assert len(alerts) == 1
        assert alerts[0].severity == SEV_CRITICAL
        assert alerts[0].reason == REASON_BREACHED
        assert alerts[0].tenant_id == "acme"

    def test_at_risk_alerts_warning(self, span_factory):
        # 1 bad in 40 = exactly 50% of the 5% error budget -> AT_RISK
        result = eval_result(build_store(span_factory, failures=1, total=40),
                             availability())
        assert result.verdict == VERDICT_AT_RISK
        alerts = BreachDetector().detect([result])
        assert len(alerts) == 1
        assert alerts[0].severity == SEV_WARNING
        assert alerts[0].reason == REASON_AT_RISK

    def test_ok_results_never_alert(self, span_factory):
        result = eval_result(build_store(span_factory, failures=0),
                             availability(target=0.5))
        assert result.verdict == "OK"
        assert BreachDetector().detect([result]) == []

    def test_policy_severity_overrides(self, span_factory):
        result = eval_result(build_store(span_factory, failures=4),
                             availability())
        policy = AlertPolicy(breached_severity=SEV_WARNING)
        alerts = BreachDetector(policy=policy).detect([result])
        assert alerts[0].severity == SEV_WARNING


class TestOutcomeNotLiveness:
    """HEALTHY IS NOT WORKING: silence must alert, never pass silently."""

    def test_silent_tenant_alerts_missing_window(self, span_factory):
        """A tenant missing the SLO window fires a critical alert."""
        store = build_store(span_factory, tenant="acme")
        result = eval_result(store, availability(tenant="ghost"))
        assert result.verdict == VERDICT_NO_DATA
        assert result.missed_window
        alerts = BreachDetector().detect([result])
        assert len(alerts) == 1
        assert alerts[0].severity == SEV_CRITICAL
        assert alerts[0].reason == REASON_MISSING_WINDOW

    def test_heartbeats_without_outcomes_alert(self, span_factory):
        """Trace roots / guard decisions are not outcomes — they alert."""
        spans = [
            span_factory(trace_id="tr", span_id="r", kind=KIND_TRACE,
                         parent_span_id=None, ts=iso_at(0), name="root"),
        ]
        result = eval_result(TraceStore(spans=spans), availability())
        assert result.verdict == VERDICT_NO_DATA
        assert result.missed_window is False
        alerts = BreachDetector().detect([result])
        assert len(alerts) == 1
        assert alerts[0].reason == REASON_NO_ATTEMPTS
        # NO_DATA is critical — silence is never healthy
        assert alerts[0].severity == SEV_CRITICAL

    def test_group_by_tenant(self, span_factory):
        bad = eval_result(build_store(span_factory, failures=5,
                                      tenant="acme"),
                          availability(tenant="acme"))
        silent = eval_result(build_store(span_factory, tenant="acme"),
                             availability(tenant="nimbus"))
        alerts = BreachDetector().detect([bad, silent])
        grouped = BreachDetector().group_by_tenant(alerts)
        assert set(grouped) == {"acme", "nimbus"}


class TestAlertShape:
    def test_alert_to_dict_roundtrip(self, span_factory):
        result = eval_result(build_store(span_factory, failures=5),
                             availability())
        alert = BreachDetector().detect([result])[0]
        data = alert.to_dict()
        assert data["tenantId"] == "acme"
        assert data["severity"] == SEV_CRITICAL
        assert data["reason"] == REASON_BREACHED
        assert data["slo"] == "availability:acme"
        assert "firedAt" in data

    def test_unknown_verdict_severity_raises(self):
        policy = AlertPolicy()
        with pytest.raises(ValueError):
            policy.severity_for("NOPE")
