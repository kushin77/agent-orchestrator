"""Health + degradation event tests (issue #18 AC 4, deliverable 4).

Every health transition emits a structured ``HealthEvent`` to the injected
audit (all events) and alert (severity >= warning) sinks — the Phase-5
observability hook. Events carry provider/model/state/verdict/rates but no
key material.
"""

from __future__ import annotations

import json

import pytest

from health import HealthMonitor, JsonlHealthSink, ListHealthSink


def _drive_to_quarantine(m) -> None:
    for _ in range(30):
        if m.health_status("deepseek", "deepseek-chat")["state"] == "quarantined":
            return
        m.record_failure("deepseek", "deepseek-chat", error_class="timeout")
    raise AssertionError("never reached quarantine")


def test_quarantine_event_carries_full_context(config, clock, audit) -> None:
    m = HealthMonitor(config=config, now=clock, audit_sinks=[audit])
    for _ in range(3):
        m.record_success("deepseek", "deepseek-chat", latency_ms=400.0)
    # 3 successes + 4 failures = 7 outcomes, 57.1% failure -> quarantine.
    for _ in range(4):
        m.record_failure("deepseek", "deepseek-chat", error_class="timeout")
    quarantined = [e for e in audit.events if e.kind == "quarantined"]
    assert len(quarantined) == 1
    event = quarantined[0]
    assert event.provider == "deepseek"
    assert event.model == "deepseek-chat"
    assert event.state == "quarantined"
    assert event.verdict == "unhealthy"
    assert event.severity == "critical"
    assert event.failure_rate_pct > 50.0
    assert event.window_samples >= 3
    assert event.error_class == "timeout"


def test_audit_gets_all_alert_gets_severity_only(config, clock, audit, alerts) -> None:
    m = HealthMonitor(
        config=config, now=clock, audit_sinks=[audit], alert_sinks=[alerts]
    )
    for _ in range(3):
        m.record_success("deepseek", "deepseek-chat")
    m.record_failure("deepseek", "deepseek-chat", error_class="timeout")
    m.record_failure("deepseek", "deepseek-chat", error_class="timeout")
    _drive_to_quarantine(m)
    clock.advance(31.0)
    assert m.may_probe("deepseek", "deepseek-chat") is True
    m.record_probe_success("deepseek", "deepseek-chat")
    m.record_probe_success("deepseek", "deepseek-chat")

    assert audit.kinds() == [
        "degraded",
        "quarantined",
        "recovery_probe_started",
        "recovered",
    ]
    # Alert sink: warning/critical only (degraded + quarantined), never the
    # info-level probe/recovery events.
    assert alerts.kinds() == ["degraded", "quarantined"]


def test_operator_mark_events(config, clock, audit, alerts) -> None:
    m = HealthMonitor(
        config=config, now=clock, audit_sinks=[audit], alert_sinks=[alerts]
    )
    m.mark_unhealthy("ollama", "llama3.2", detail="ollama daemon down")
    m.mark_healthy("ollama", "llama3.2")
    assert "operator_marked_unhealthy" in audit.kinds()
    assert "operator_marked_healthy" in audit.kinds()
    assert "operator_marked_unhealthy" in alerts.kinds()  # critical
    assert "operator_marked_healthy" not in alerts.kinds()  # info


def test_raising_sink_propagates_fail_closed(config, clock) -> None:
    class ExplodingSink:
        def record(self, event) -> None:
            raise RuntimeError("audit store down")

    m = HealthMonitor(config=config, now=clock, audit_sinks=[ExplodingSink()])
    for _ in range(3):
        m.record_success("deepseek", "deepseek-chat")
    # The degraded transition must raise: a lost audit record is never silent.
    with pytest.raises(RuntimeError, match="audit store down"):
        m.record_failure("deepseek", "deepseek-chat", error_class="timeout")


def test_jsonl_sink_roundtrip(tmp_path, config, clock) -> None:
    path = tmp_path / "health-audit.jsonl"
    sink = JsonlHealthSink(path)
    m = HealthMonitor(config=config, now=clock, audit_sinks=[sink])
    # 3 successes + 2 failures = 40% failure -> a degraded transition fires.
    for _ in range(3):
        m.record_success("deepseek", "deepseek-chat", latency_ms=200.0)
    for _ in range(2):
        m.record_failure("deepseek", "deepseek-chat", error_class="timeout")
    records = sink.read_events()
    assert len(records) == 1
    payload = records[0]
    for key in ("kind", "provider", "model", "state", "verdict", "severity", "ts"):
        assert key in payload
    # Events are JSON-lines (one dict per line, parseable).
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            assert isinstance(json.loads(line), dict)


def test_events_carry_no_secret_material(config, clock) -> None:
    sink = ListHealthSink()
    m = HealthMonitor(config=config, now=clock, audit_sinks=[sink])
    m.mark_unhealthy("deepseek", "deepseek-chat", detail="provider incident")
    for event in sink.events:
        text = json.dumps(event.to_dict())
        assert "api_key" not in text
        assert "secret" not in text
        assert "password" not in text
        assert "Bearer " not in text
