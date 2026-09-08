"""Rolling-window measurement tests (issue #18 AC 1).

Asserts the monitor computes honest window statistics: sliding-window failure
rate, latency average + p95 (nearest-rank), per-error-class counts, and the
min-samples / unknown handling (no data is never a verdict).
"""

from __future__ import annotations

import pytest


def _status(monitor, provider="deepseek", model="deepseek-chat"):
    return monitor.health_status(provider, model)


def test_empty_window_is_unknown_and_admitted(monitor) -> None:
    st = _status(monitor)
    assert st["state"] == "healthy"
    assert st["verdict"] == "unknown"  # not "healthy" and not measured yet
    assert st["is_healthy"] is True  # admitted for routing (safe admission)
    assert st["window"]["total"] == 0
    assert st["window"]["failure_rate_pct"] == 0.0


def test_failure_rate_over_window(monitor) -> None:
    for _ in range(6):
        monitor.record_success("deepseek", "deepseek-chat")
    for _ in range(6):
        monitor.record_failure("deepseek", "deepseek-chat", error_class="timeout")
    st = _status(monitor)
    assert st["window"]["total"] == 12
    assert st["window"]["successes"] == 6
    assert st["window"]["failures"] == 6
    assert st["window"]["failure_rate_pct"] == 50.0


def test_window_slides_oldest_out(monitor, config) -> None:
    # Feed more than the window size: the oldest outcomes must roll off.
    for _ in range(config.window_size + 4):
        monitor.record_success("deepseek", "deepseek-chat")
    st = _status(monitor)
    assert st["window"]["total"] == config.window_size
    assert st["window"]["successes"] == config.window_size
    assert st["window"]["failures"] == 0


def test_latency_average_and_p95(config, clock, audit) -> None:
    # A 20-outcome window: latencies 100..2000 -> avg = 1050, p95
    # (nearest-rank rank 19 of 20) = 1900.
    from dataclasses import replace

    from health import HealthMonitor

    wide = HealthMonitor(
        config=replace(config, window_size=20), now=clock, audit_sinks=[audit]
    )
    for latency in range(100, 2001, 100):  # 100, 200, ..., 2000
        wide.record_success("deepseek", "deepseek-chat", latency_ms=float(latency))
    st = wide.health_status("deepseek", "deepseek-chat")
    assert st["window"]["latency_avg_ms"] == pytest.approx(1050.0)
    assert st["window"]["latency_p95_ms"] == pytest.approx(1900.0)


def test_slow_advisory_when_p95_above_threshold(monitor, config) -> None:
    for _ in range(3):  # >= min_samples
        monitor.record_success("deepseek", "deepseek-chat", latency_ms=5000.0)
    st = _status(monitor)
    assert config.slow_threshold_ms == 2000.0
    assert st["window"]["slow"] is True
    assert st["verdict"] == "healthy"  # slow is advisory; state stays healthy


def test_error_class_counts(monitor) -> None:
    for cls in ("timeout", "timeout", "rate_limit", "http_5xx"):
        monitor.record_failure("deepseek", "deepseek-chat", error_class=cls)
    st = _status(monitor)
    counts = st["window"]["error_counts"]
    assert counts["timeout"] == 2
    assert counts["rate_limit"] == 1
    assert counts["http_5xx"] == 1
    assert st["last_error_class"] == "http_5xx"


def test_below_min_samples_no_verdict_change(monitor, config) -> None:
    # A couple of failures under min_samples must not trip or degrade.
    for _ in range(config.min_samples - 1):
        monitor.record_failure("deepseek", "deepseek-chat", error_class="timeout")
    st = _status(monitor)
    assert st["state"] == "healthy"
    assert st["verdict"] == "unknown"
    assert st["is_healthy"] is True
