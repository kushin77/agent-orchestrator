"""Gradual rollout + deterministic audience tests (issue #45).

A canary/gradual slice is deterministic: the same subject always lands in the
same bucket (adapted from the defragsuite consistent-hash rollout concept), an
explicitly targeted subject is always exposed, OFF never exposes and FULL
exposes everyone.
"""

from __future__ import annotations

import time

import pytest

from infra.rollout.engine import Approval, RolloutEngine, RolloutError
from infra.rollout.model import (
    Audience,
    RolloutStage,
    stable_bucket,
)


def test_stable_bucket_is_deterministic() -> None:
    for _ in range(5):
        assert stable_bucket("services.registry", "tenant-acme") == stable_bucket("services.registry", "tenant-acme")
    assert 0 <= stable_bucket("services.registry", "tenant-acme") < 100


def test_audience_off_never_exposes() -> None:
    audience = Audience(stage=RolloutStage.OFF, rollout_pct=0)
    assert not audience.exposes("services.registry", "tenant-acme")
    assert not audience.exposes("services.registry", "tenant-acme-targeted")


def test_audience_full_exposes_everyone() -> None:
    audience = Audience(stage=RolloutStage.FULL, rollout_pct=100)
    assert audience.exposes("services.registry", "tenant-acme")
    assert audience.exposes("services.registry", "anyone")


def test_audience_targeted_always_exposed() -> None:
    audience = Audience(stage=RolloutStage.CANARY, rollout_pct=5, targeted=("tenant-acme",))
    assert audience.exposes("services.registry", "tenant-acme")


def test_audience_percentage_is_consistent() -> None:
    audience = Audience(stage=RolloutStage.CANARY, rollout_pct=5)
    bucket = stable_bucket("services.registry", "tenant-acme")
    expected = bucket < 5
    for _ in range(3):
        assert audience.exposes("services.registry", "tenant-acme") is expected


def test_canary_pct_increases_exposure_monotonically() -> None:
    # The set of exposed subjects at a higher pct is a superset (bucket < pct).
    exposed_low = {f"t{i}" for i in range(200) if Audience(RolloutStage.CANARY, 10).exposes("services.registry", f"t{i}")}
    exposed_high = {f"t{i}" for i in range(200) if Audience(RolloutStage.CANARY, 50).exposes("services.registry", f"t{i}")}
    assert exposed_low <= exposed_high
    assert len(exposed_low) < len(exposed_high)


def _grant(engine: RolloutEngine, flag: str, target: str, approval_id: str) -> None:
    engine.approvals.grant(
        Approval(
            approval_id=approval_id,
            flag=flag,
            target_stage=RolloutStage.coerce(target),
            approver="auditor-sme",
            granted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
    )


def test_ramp_to_full_with_audit(engine: RolloutEngine) -> None:
    _grant(engine, "services.registry", "canary", "a1")
    engine.promote("services.registry", "canary", verify_green=True, approval_id="a1", actor="deployer-sa")
    _grant(engine, "services.registry", "gradual", "a2")
    engine.promote(
        "services.registry", "gradual", verify_green=True, approval_id="a2",
        actor="deployer-sa", canary_health_ok=True,
    )
    flag = engine.flag("services.registry")
    assert flag.stage is RolloutStage.GRADUAL
    assert flag.rollout_pct == 10

    for pct in (25, 50, 100):
        flag = engine.ramp("services.registry", pct, verify_green=True)
        assert flag.rollout_pct == pct

    assert engine.audit.verify()
    actions = [r["action"] for r in engine.audit.records()]
    assert "ramp" in actions
    assert "ramp-complete" in actions


def test_ramp_rejects_lower_or_unknown_pct(engine: RolloutEngine) -> None:
    _grant(engine, "services.registry", "canary", "a1")
    engine.promote("services.registry", "canary", verify_green=True, approval_id="a1", actor="deployer-sa")
    _grant(engine, "services.registry", "gradual", "a2")
    engine.promote(
        "services.registry", "gradual", verify_green=True, approval_id="a2",
        actor="deployer-sa", canary_health_ok=True,
    )
    with pytest.raises(RolloutError):
        engine.ramp("services.registry", 10, verify_green=True)  # not higher
    with pytest.raises(RolloutError):
        engine.ramp("services.registry", 37, verify_green=True)  # not a declared step


def test_ramp_requires_green_verify(engine: RolloutEngine) -> None:
    _grant(engine, "services.registry", "canary", "a1")
    engine.promote("services.registry", "canary", verify_green=True, approval_id="a1", actor="deployer-sa")
    _grant(engine, "services.registry", "gradual", "a2")
    engine.promote(
        "services.registry", "gradual", verify_green=True, approval_id="a2",
        actor="deployer-sa", canary_health_ok=True,
    )
    with pytest.raises(RolloutError):
        engine.ramp("services.registry", 25, verify_green=False)
