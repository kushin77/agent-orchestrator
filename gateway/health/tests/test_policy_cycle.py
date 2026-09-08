"""Degradation policy state machine tests (issue #18 AC 2 + deliverable 2).

Asserts the full cycle: healthy -> degrade (trip threshold not yet crossed)
-> quarantined (trip crossed) -> (production diverted) -> cool-off ->
recovery probe -> recovered, plus the no-false-green rule that production
traffic can never clear a quarantine.
"""

from __future__ import annotations


def _healthy(monitor, n=5, provider="deepseek", model="deepseek-chat") -> None:
    for _ in range(n):
        monitor.record_success(provider, model, latency_ms=400.0)


def _fail(monitor, n, provider="deepseek", model="deepseek-chat", cls="timeout"):
    for _ in range(n):
        monitor.record_failure(provider, model, error_class=cls)


def _st(monitor, provider="deepseek", model="deepseek-chat"):
    return monitor.health_status(provider, model)


def test_straight_to_quarantine_past_trip_threshold(monitor, audit) -> None:
    # Failures only: at min_samples the window is 100% failed -> quarantine
    # directly (no stop at the degraded band).
    _fail(monitor, 3)
    st = _st(monitor)
    assert st["state"] == "quarantined"
    assert st["verdict"] == "unhealthy"
    assert st["is_healthy"] is False
    kinds = audit.kinds()
    assert "quarantined" in kinds
    assert "degraded" not in kinds  # trip supersedes the degraded band


def test_healthy_to_degraded_band(monitor, audit) -> None:
    _healthy(monitor, 3)
    _fail(monitor, 1)  # 4 outcomes, 25% failure -> degraded (band, not trip)
    st = _st(monitor)
    assert st["state"] == "healthy"  # still serving-capable state
    assert st["verdict"] == "degraded"
    assert st["is_healthy"] is False  # routing avoids it
    assert "degraded" in audit.kinds()


def test_degraded_recovers_by_observed_successes(monitor, audit) -> None:
    _healthy(monitor, 3)
    _fail(monitor, 1)  # -> degraded (25% of 4)
    assert _st(monitor)["verdict"] == "degraded"
    _healthy(monitor, 8)  # slide the window: 1 failure in 12 = 8.3% <= recover
    st = _st(monitor)
    assert st["state"] == "healthy"
    assert st["verdict"] == "healthy"
    assert st["is_healthy"] is True
    assert "healthy" in audit.kinds()  # recovered event (degraded cleared)


def test_full_trip_quarantine_cooloff_probe_recovery_cycle(
    monitor, audit, alerts, clock
) -> None:
    _healthy(monitor, 5)
    # Push past the trip threshold (50% of the window).
    for _ in range(20):
        if _st(monitor)["state"] == "quarantined":
            break
        monitor.record_failure("deepseek", "deepseek-chat", error_class="timeout")
    st = _st(monitor)
    assert st["state"] == "quarantined"
    assert st["is_healthy"] is False
    assert "quarantined" in audit.kinds()

    # Production successes recorded while quarantined cannot clear it.
    _healthy(monitor, 5)
    st = _st(monitor)
    assert st["state"] == "quarantined"
    assert st["is_healthy"] is False

    # Cool-off has not elapsed: no probe authorized.
    assert monitor.may_probe("deepseek", "deepseek-chat") is False

    # Cool-off elapses -> recovery probe authorized.
    clock.advance(31.0)
    assert monitor.may_probe("deepseek", "deepseek-chat") is True
    st = _st(monitor)
    assert st["state"] == "probing"
    assert st["is_healthy"] is False  # probing is not serving production
    assert "recovery_probe_started" in audit.kinds()

    # A failed probe re-asserts quarantine (cool-off resets).
    monitor.record_probe_failure("deepseek", "deepseek-chat",
                                 error_class="unavailable")
    st = _st(monitor)
    assert st["state"] == "quarantined"
    assert st["is_healthy"] is False
    assert "quarantine_reasserted" in audit.kinds()

    # Cool-off again, then two consecutive successful probes restore health.
    clock.advance(31.0)
    assert monitor.may_probe("deepseek", "deepseek-chat") is True
    monitor.record_probe_success("deepseek", "deepseek-chat")
    assert _st(monitor)["state"] == "probing"  # not yet at the threshold
    monitor.record_probe_success("deepseek", "deepseek-chat")
    st = _st(monitor)
    assert st["state"] == "healthy"
    assert st["verdict"] == "unknown"  # clean slate after earned recovery
    assert st["is_healthy"] is True
    assert "recovered" in audit.kinds()

    # Recovered is info: audit only, not the alert sink.
    assert "recovered" not in alerts.kinds()
    assert "quarantined" in alerts.kinds()
    assert "quarantine_reasserted" in alerts.kinds()


def test_probing_needs_probes_not_production_successes(config, clock, audit) -> None:
    """In PROBING, production-style successes must not count as probe successes.

    Recovery from quarantine is earned by probe calls alone: a burst of
    ordinary ``record_success`` calls while probing must leave the model in
    PROBING (never flip it healthy).
    """
    from health import HealthMonitor

    m = HealthMonitor(config=config, now=clock, audit_sinks=[audit])
    for _ in range(20):
        if m.health_status("deepseek", "deepseek-chat")["state"] == "quarantined":
            break
        m.record_failure("deepseek", "deepseek-chat", error_class="timeout")
    assert m.health_status("deepseek", "deepseek-chat")["state"] == "quarantined"
    clock.advance(31.0)
    assert m.may_probe("deepseek", "deepseek-chat") is True
    # A burst of production-style successes (not probe successes).
    for _ in range(6):
        m.record_success("deepseek", "deepseek-chat", latency_ms=400.0)
    st = m.health_status("deepseek", "deepseek-chat")
    assert st["state"] == "probing"  # production traffic never restores it
    assert st["is_healthy"] is False
    assert "recovered" not in audit.kinds()
    # Only actual probe successes cross the threshold.
    m.record_probe_success("deepseek", "deepseek-chat")
    m.record_probe_success("deepseek", "deepseek-chat")
    assert m.health_status("deepseek", "deepseek-chat")["state"] == "healthy"
