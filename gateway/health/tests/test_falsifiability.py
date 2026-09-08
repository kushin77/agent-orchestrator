"""Falsifiability (no-false-green) tests — issue #18 AC 5.

A health check that can never fail is a formality. These negative tests prove
the health signal is honest:

- a dead/unhealthy model MUST actually report unhealthy (feed failures past
  the trip threshold -> status degraded/quarantined, is_healthy False);
- a recovery probe against a fake dead model MUST fail and keep the model
  quarantined — recovery can never be earned by an absent log, a stray
  production success, an unauthorized probe success, or time alone.
"""

from __future__ import annotations

from health import HealthMonitor


def _quarantine(m, provider="deepseek", model="deepseek-chat") -> None:
    """Drive a healthy model into quarantine by feeding failures."""
    for _ in range(30):
        if m.health_status(provider, model)["state"] == "quarantined":
            return
        m.record_failure(provider, model, error_class="timeout")
    raise AssertionError("model never reached quarantined after 30 failures")


def test_dead_model_must_report_unhealthy(config, clock) -> None:
    m = HealthMonitor(config=config, now=clock)
    _quarantine(m)
    st = m.health_status("deepseek", "deepseek-chat")
    assert st["verdict"] == "unhealthy"
    assert st["state"] == "quarantined"
    assert st["is_healthy"] is False
    assert st["window"]["failure_rate_pct"] > config.trip_failure_pct
    # The routing signal the gateway/chooser consumes is False.
    assert m.is_healthy("deepseek", "deepseek-chat") is False


def test_degraded_model_must_report_unhealthy(config, clock) -> None:
    m = HealthMonitor(config=config, now=clock)
    for _ in range(3):
        m.record_success("deepseek", "deepseek-chat")
    for _ in range(2):
        m.record_failure("deepseek", "deepseek-chat", error_class="timeout")
    # 5 outcomes, 2 failures = 40% -> above degrade (20), below trip (50).
    st = m.health_status("deepseek", "deepseek-chat")
    assert st["verdict"] == "degraded"
    assert st["is_healthy"] is False
    assert m.is_healthy("deepseek", "deepseek-chat") is False


def test_probe_against_dead_model_fails_and_stays_down(config, clock) -> None:
    m = HealthMonitor(config=config, now=clock)
    _quarantine(m)
    # Many cool-off + probe rounds against a model that never recovers.
    for _ in range(5):
        clock.advance(31.0)
        assert m.may_probe("deepseek", "deepseek-chat") is True
        m.record_probe_failure("deepseek", "deepseek-chat",
                               error_class="unavailable")
        st = m.health_status("deepseek", "deepseek-chat")
        assert st["state"] == "quarantined"
        assert st["is_healthy"] is False
    assert m.is_healthy("deepseek", "deepseek-chat") is False


def test_stray_production_success_cannot_clear_quarantine(config, clock) -> None:
    m = HealthMonitor(config=config, now=clock)
    _quarantine(m)
    # Feed a burst of successes (a misrouted caller would do this) - the
    # quarantine must hold.
    for _ in range(20):
        m.record_success("deepseek", "deepseek-chat", latency_ms=200.0)
    st = m.health_status("deepseek", "deepseek-chat")
    assert st["state"] == "quarantined"
    assert st["is_healthy"] is False


def test_unauthorized_probe_success_cannot_clear_quarantine(config, clock) -> None:
    m = HealthMonitor(config=config, now=clock)
    _quarantine(m)
    # Probe successes before the cool-off has elapsed must be ignored.
    assert m.may_probe("deepseek", "deepseek-chat") is False
    m.record_probe_success("deepseek", "deepseek-chat")
    m.record_probe_success("deepseek", "deepseek-chat")
    st = m.health_status("deepseek", "deepseek-chat")
    assert st["state"] == "quarantined"
    assert st["is_healthy"] is False


def test_absence_of_data_never_clears_quarantine(config, clock) -> None:
    m = HealthMonitor(config=config, now=clock)
    _quarantine(m)
    # No observations at all, and a very long time passes.
    clock.advance(10_000.0)
    st = m.health_status("deepseek", "deepseek-chat")
    assert st["state"] == "quarantined"
    assert st["is_healthy"] is False
    # Even authorizing a probe is not recovery: only probe successes count.
    assert m.may_probe("deepseek", "deepseek-chat") is True
    st = m.health_status("deepseek", "deepseek-chat")
    assert st["state"] == "probing"
    assert st["is_healthy"] is False


def test_healthy_model_is_not_falsely_degraded(config, clock) -> None:
    """A model with a healthy window must keep reporting healthy."""
    m = HealthMonitor(config=config, now=clock)
    for _ in range(10):
        m.record_success("deepseek", "deepseek-chat", latency_ms=300.0)
    st = m.health_status("deepseek", "deepseek-chat")
    assert st["state"] == "healthy"
    assert st["verdict"] == "healthy"
    assert st["is_healthy"] is True
