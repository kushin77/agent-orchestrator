"""HealthConfig validation tests (issue #18 deliverable 2 config in YAML).

A broken configuration must fail loudly at load/construct time (fail closed),
never silently fall back to defaults or produce contradictory thresholds.
"""

from __future__ import annotations

import pytest

from health import HealthConfig, load_config


def test_shipped_config_matches_health_yaml() -> None:
    config = load_config()
    assert config.window_size == 100
    assert config.min_samples == 10
    assert config.degrade_failure_pct == 20.0
    assert config.trip_failure_pct == 50.0
    assert config.recover_failure_pct == 10.0
    assert config.cool_off_seconds == 60.0
    assert config.probe_success_threshold == 2
    assert config.slow_threshold_ms == 30000.0


def test_thresholds_must_be_ordered_recover_degrade_trip() -> None:
    with pytest.raises(ValueError):
        HealthConfig(degrade_failure_pct=80.0, trip_failure_pct=50.0)
    with pytest.raises(ValueError):
        HealthConfig(recover_failure_pct=30.0, degrade_failure_pct=20.0)
    with pytest.raises(ValueError):
        HealthConfig(trip_failure_pct=120.0)


def test_window_and_min_samples_constraints() -> None:
    with pytest.raises(ValueError):
        HealthConfig(window_size=0)
    with pytest.raises(ValueError):
        HealthConfig(min_samples=0)
    with pytest.raises(ValueError):
        HealthConfig(window_size=5, min_samples=10)
    with pytest.raises(ValueError):
        HealthConfig(cool_off_seconds=-1.0)
    with pytest.raises(ValueError):
        HealthConfig(probe_success_threshold=0)


def test_config_dict_roundtrip() -> None:
    original = HealthConfig(
        window_size=40, min_samples=4, degrade_failure_pct=15.0,
        trip_failure_pct=60.0, recover_failure_pct=5.0,
        cool_off_seconds=90.0, probe_success_threshold=3,
        slow_threshold_ms=10000.0,
    )
    rebuilt = HealthConfig.from_dict(original.to_dict())
    assert rebuilt == original


def test_load_config_rejects_malformed_document(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("monitor:\n  windowSize: [not-a-number]\n", encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        load_config(str(bad))


def test_load_config_missing_file_raises(tmp_path) -> None:
    with pytest.raises(OSError):
        load_config(str(tmp_path / "does-not-exist.yaml"))
