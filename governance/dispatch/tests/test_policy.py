"""Dispatch's declared controls: read, cross-checked, and load-bearing (issue #885)."""

from __future__ import annotations

import claims
import policy
import pytest
import snapshot as snapshot_mod
from model import Issue, Snapshot


def test_load_default_controls_matches_model_vocabulary():
    controls = policy.load()
    assert controls.schema == policy.SCHEMA
    assert set(controls.allowed_claim_reasons) == set(__import__("model").ALLOWED_CLAIM_REASONS)
    assert set(controls.terminal_claim_reasons) == set(__import__("model").TERMINAL_CLAIM_REASONS)
    assert set(controls.arbitration_refusals) == set(__import__("model").ARBITRATION_REFUSALS)


def test_claim_ttl_matches_lease_single_source():
    from governance.policy import lease

    controls = policy.load()
    assert controls.claim_ttl_hours == lease.CLAIM_TTL_HOURS


def _write_controls(tmp_path, **overrides):
    import yaml

    base = {
        "schema": policy.SCHEMA,
        "stale_minutes": 15,
        "claim_ttl_hours": 24,
        "allowed_claim_reasons": list(__import__("model").ALLOWED_CLAIM_REASONS),
        "terminal_claim_reasons": list(__import__("model").TERMINAL_CLAIM_REASONS),
        "arbitration_refusals": list(__import__("model").ARBITRATION_REFUSALS),
    }
    base.update(overrides)
    path = tmp_path / "controls.yaml"
    path.write_text(yaml.safe_dump(base), encoding="utf-8")
    return path


def test_mutation_dropped_reason_is_refused(tmp_path):
    """Mutation test: a controls.yaml that drops a real reason is CANNOT-ASSESS, not a pass."""
    reasons = list(__import__("model").ALLOWED_CLAIM_REASONS)[:-1]
    path = _write_controls(tmp_path, allowed_claim_reasons=reasons)
    with pytest.raises(policy.PolicyUnavailable, match="allowed_claim_reasons"):
        policy.load(path)


def test_mutation_ttl_drift_is_refused(tmp_path):
    path = _write_controls(tmp_path, claim_ttl_hours=999)
    with pytest.raises(policy.PolicyUnavailable, match="drifted"):
        policy.load(path)


def test_stale_minutes_is_read_and_changes_real_behaviour(tmp_path):
    """The control this package's code actually reads: a lower `stale_minutes`
    makes `snapshot.is_stale` refuse a board that the default threshold accepted —
    the same parameter `claims.arbitrate` defaults to `policy.load().stale_minutes`
    style wiring for."""
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    board = Snapshot(
        generated_at=(now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        source="test",
        issues={1: Issue(1, "x")},
    )

    lenient = _write_controls(tmp_path, stale_minutes=30)
    strict = _write_controls(tmp_path.parent, stale_minutes=5) if tmp_path.parent != tmp_path else None
    strict = tmp_path / "strict.yaml"
    import yaml

    strict.write_text(
        yaml.safe_dump(
            {
                "schema": policy.SCHEMA,
                "stale_minutes": 5,
                "claim_ttl_hours": 24,
                "allowed_claim_reasons": list(__import__("model").ALLOWED_CLAIM_REASONS),
                "terminal_claim_reasons": list(__import__("model").TERMINAL_CLAIM_REASONS),
                "arbitration_refusals": list(__import__("model").ARBITRATION_REFUSALS),
            }
        ),
        encoding="utf-8",
    )

    assert not snapshot_mod.is_stale(board, policy.load(lenient).stale_minutes, now)
    assert snapshot_mod.is_stale(board, policy.load(strict).stale_minutes, now)


def test_default_staleness_minutes_is_sourced_from_controls():
    assert snapshot_mod.DEFAULT_STALENESS_MINUTES == policy.load().stale_minutes
