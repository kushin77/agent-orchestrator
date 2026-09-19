"""Tests for the pure runtime-liveness judge (issue #1271)."""

from __future__ import annotations

import pytest

from fleet import liveness

REGISTRY = {
    "claude-session": {"kind": "agent"},
    "deepseek-sister": {"kind": "agent"},
}


def test_healthy_fleet_has_no_findings():
    beats = {
        "claude-session": {"runtime": "claude-session", "commit": "a", "state": "running", "ts": 1000.0},
        "deepseek-sister": {"runtime": "deepseek-sister", "commit": "a", "state": "running", "ts": 1000.0},
    }
    findings = liveness.judge(beats=beats, registry=REGISTRY, now=1010.0)
    assert findings == []


def test_stale_beat_reds_by_name():
    beats = {
        "claude-session": {"runtime": "claude-session", "commit": "a", "state": "running", "ts": 0.0},
        "deepseek-sister": {"runtime": "deepseek-sister", "commit": "a", "state": "running", "ts": 1000.0},
    }
    findings = liveness.judge(beats=beats, registry=REGISTRY, now=1000.0, stale_window_seconds=100.0)
    names = [f.name for f in findings]
    assert "runtime-stale:claude-session" in names
    assert "runtime-stale:deepseek-sister" not in names


def test_missing_beat_is_stale():
    beats = {"claude-session": {"runtime": "claude-session", "commit": "a", "state": "running", "ts": 1000.0}}
    findings = liveness.judge(beats=beats, registry=REGISTRY, now=1000.0)
    assert [f.name for f in findings] == ["runtime-stale:deepseek-sister"]


def test_drift_beyond_n_with_no_directive_reds():
    beats = {
        "claude-session": {"runtime": "claude-session", "commit": "a", "state": "running", "ts": 1000.0},
        "deepseek-sister": {"runtime": "deepseek-sister", "commit": "old", "state": "running", "ts": 1000.0},
    }
    findings = liveness.judge(
        beats=beats,
        registry=REGISTRY,
        now=1000.0,
        drift_commits=5,
        commit_distance={"deepseek-sister": 10},
    )
    assert [f.name for f in findings] == ["runtime-drift:deepseek-sister"]


def test_drift_with_directive_in_flight_is_not_red():
    beats = {
        "claude-session": {"runtime": "claude-session", "commit": "a", "state": "running", "ts": 1000.0},
        "deepseek-sister": {"runtime": "deepseek-sister", "commit": "old", "state": "running", "ts": 1000.0},
    }
    findings = liveness.judge(
        beats=beats,
        registry=REGISTRY,
        now=1000.0,
        drift_commits=5,
        commit_distance={"deepseek-sister": 10},
        directives_in_flight={"deepseek-sister"},
    )
    assert findings == []


def test_drift_within_budget_is_not_red():
    beats = {
        "claude-session": {"runtime": "claude-session", "commit": "a", "state": "running", "ts": 1000.0},
        "deepseek-sister": {"runtime": "deepseek-sister", "commit": "old", "state": "running", "ts": 1000.0},
    }
    findings = liveness.judge(
        beats=beats,
        registry=REGISTRY,
        now=1000.0,
        drift_commits=5,
        commit_distance={"deepseek-sister": 3},
    )
    assert findings == []


def test_unregistered_runtime_id_reds():
    beats = {
        "claude-session": {"runtime": "claude-session", "commit": "a", "state": "running", "ts": 1000.0},
        "deepseek-sister": {"runtime": "deepseek-sister", "commit": "a", "state": "running", "ts": 1000.0},
        "ghost": {"runtime": "ghost", "commit": "a", "state": "running", "ts": 1000.0},
    }
    findings = liveness.judge(beats=beats, registry=REGISTRY, now=1000.0)
    assert [f.name for f in findings] == ["runtime-unregistered:ghost"]


def test_stale_outranks_drift_for_same_runtime():
    beats = {
        "claude-session": {"runtime": "claude-session", "commit": "a", "state": "running", "ts": 1000.0},
        "deepseek-sister": {"runtime": "deepseek-sister", "commit": "old", "state": "running", "ts": 0.0},
    }
    findings = liveness.judge(
        beats=beats,
        registry=REGISTRY,
        now=1000.0,
        stale_window_seconds=100.0,
        drift_commits=1,
        commit_distance={"deepseek-sister": 99},
    )
    names = [f.name for f in findings]
    assert names == ["runtime-stale:deepseek-sister"]


def test_cannot_assess_drift_when_distance_unknown_is_not_a_finding():
    beats = {
        "claude-session": {"runtime": "claude-session", "commit": "a", "state": "running", "ts": 1000.0},
        "deepseek-sister": {"runtime": "deepseek-sister", "commit": "old", "state": "running", "ts": 1000.0},
    }
    findings = liveness.judge(beats=beats, registry=REGISTRY, now=1000.0, commit_distance={})
    assert findings == []


def test_rejects_bad_config():
    with pytest.raises(ValueError):
        liveness.judge(beats={}, registry=REGISTRY, now=0.0, stale_window_seconds=0)
    with pytest.raises(ValueError):
        liveness.judge(beats={}, registry=REGISTRY, now=0.0, drift_commits=-1)
