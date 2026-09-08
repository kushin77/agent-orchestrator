"""Security-telemetry tests (injection-attempt telemetry surfaced to the view).

The security view must surface exactly the security-relevant events (injection
attempts, scrub blocks, egress denials, output quarantines, tamper detections)
newest-first, filter by tenant, and never leak benign traffic (call_sent is
not security-view material).
"""

from __future__ import annotations

import pytest

from dlp.telemetry import SecurityTelemetry


def _telemetry() -> SecurityTelemetry:
    return SecurityTelemetry()


def test_security_view_surfaces_injection_attempts():
    tel = _telemetry()
    tel.emit("injection_attempt", tenant_id="acme", agent_id="a1",
             severity="critical", verdict="blocked")
    tel.emit("call_sent", tenant_id="acme", agent_id="a1")
    view = tel.security_view()
    assert len(view) == 1
    assert view[0].event_type == "injection_attempt"
    assert view[0].severity == "critical"
    assert view[0].detail["verdict"] == "blocked"


def test_security_view_includes_all_security_event_types():
    tel = _telemetry()
    tel.emit("injection_attempt", tenant_id="acme", severity="warning")
    for kind in ("scrub_blocked", "egress_denied", "output_quarantined",
                 "tamper_detected"):
        tel.emit(kind, tenant_id="acme", severity="warning")
    tel.emit("call_sent", tenant_id="acme")
    kinds = {e.event_type for e in tel.security_view()}
    assert kinds == {
        "injection_attempt", "scrub_blocked", "egress_denied",
        "output_quarantined", "tamper_detected",
    }


def test_security_view_orders_newest_first():
    tel = _telemetry()
    tel.emit("injection_attempt", tenant_id="acme", severity="warning", n=1)
    tel.emit("injection_attempt", tenant_id="acme", severity="critical", n=2)
    view = tel.security_view()
    assert [e.detail["n"] for e in view] == [2, 1]


def test_security_view_filters_by_tenant():
    tel = _telemetry()
    tel.emit("injection_attempt", tenant_id="acme", severity="critical")
    tel.emit("injection_attempt", tenant_id="globex", severity="critical")
    view = tel.security_view(tenant_id="acme")
    assert len(view) == 1 and view[0].tenant_id == "acme"


def test_security_view_limit():
    tel = _telemetry()
    for i in range(5):
        tel.emit("injection_attempt", tenant_id="acme", severity="warning", i=i)
    assert len(tel.security_view(limit=2)) == 2


def test_call_sent_is_never_security_view_material():
    tel = _telemetry()
    tel.emit("call_sent", tenant_id="acme", severity="info")
    tel.emit("call_sent", tenant_id="acme", severity="info")
    assert tel.security_view() == []


def test_counts_and_all_events():
    tel = _telemetry()
    tel.emit("injection_attempt", tenant_id="acme", severity="critical")
    tel.emit("call_sent", tenant_id="acme")
    counts = tel.counts()
    assert counts["injection_attempt"] == 1
    assert counts["call_sent"] == 1
    assert len(tel.all_events()) == 2


def test_unknown_event_type_and_severity_rejected():
    tel = _telemetry()
    with pytest.raises(ValueError):
        tel.emit("not_an_event", tenant_id="acme")
    with pytest.raises(ValueError):
        tel.emit("call_sent", tenant_id="acme", severity="extreme")


def test_telemetry_file_sink_records_events(tmp_path):
    log_path = tmp_path / "security.jsonl"
    tel = SecurityTelemetry(path=str(log_path))
    tel.emit("injection_attempt", tenant_id="acme", severity="critical")
    content = log_path.read_text(encoding="utf-8").strip()
    assert "injection_attempt" in content


def test_agent_id_and_detail_round_trip():
    tel = _telemetry()
    event = tel.emit(
        "tamper_detected", tenant_id="acme", agent_id="agent-7",
        severity="critical", record_id="call-9",
    )
    d = event.as_dict()
    assert d["agent_id"] == "agent-7"
    assert d["detail"]["record_id"] == "call-9"
