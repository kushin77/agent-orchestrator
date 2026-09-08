"""Promotion-gate tests (issue #45): verify evidence + approval-as-code.

A promotion only happens when the gate is green: verification evidence AND an
approval-as-code record from a distinct approver (the issue #43
``merge_verdict`` gate shape applied to rollout, AO-GR-14 separation of
duties). Missing or self-granted approval blocks the promotion.
"""

from __future__ import annotations

import time

import pytest

from infra.rollout.engine import Approval, RolloutEngine, RolloutError
from infra.rollout.model import RolloutStage


def _grant(engine: RolloutEngine, flag: str, target: str, approval_id: str, approver: str = "auditor-sme") -> None:
    engine.approvals.grant(
        Approval(
            approval_id=approval_id,
            flag=flag,
            target_stage=RolloutStage.coerce(target),
            approver=approver,
            granted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
    )


def test_promote_off_to_canary_with_green_gate(engine: RolloutEngine) -> None:
    _grant(engine, "services.registry", "canary", "a-canary")
    flag = engine.promote(
        "services.registry", "canary", verify_green=True,
        approval_id="a-canary", actor="deployer-sa",
    )
    assert flag.stage is RolloutStage.CANARY
    assert flag.rollout_pct == 5  # stage-model canary default
    assert engine.audit.verify()


def test_promotion_without_approval_is_blocked(engine: RolloutEngine) -> None:
    with pytest.raises(RolloutError) as exc:
        engine.promote("services.registry", "canary", verify_green=True, actor="deployer-sa")
    assert "approval_code" in str(exc.value) or "approval" in str(exc.value)


def test_promotion_without_verify_evidence_is_blocked(engine: RolloutEngine) -> None:
    _grant(engine, "services.registry", "canary", "a-canary")
    with pytest.raises(RolloutError) as exc:
        engine.promote("services.registry", "canary", verify_green=False, approval_id="a-canary", actor="deployer-sa")
    assert "verify_green" in str(exc.value)


def test_approval_for_wrong_flag_is_blocked(engine: RolloutEngine) -> None:
    _grant(engine, "services.gateway", "canary", "a-gateway")
    with pytest.raises(RolloutError):
        engine.promote("services.registry", "canary", verify_green=True, approval_id="a-gateway", actor="deployer-sa")


def test_approval_for_wrong_stage_is_blocked(engine: RolloutEngine) -> None:
    _grant(engine, "services.registry", "gradual", "a-gradual")
    with pytest.raises(RolloutError):
        engine.promote("services.registry", "canary", verify_green=True, approval_id="a-gradual", actor="deployer-sa")


def test_self_granted_approval_is_blocked(engine: RolloutEngine) -> None:
    # The approver must be distinct from the executing actor (AO-GR-14).
    _grant(engine, "services.registry", "canary", "a-self", approver="deployer-sa")
    with pytest.raises(RolloutError) as exc:
        engine.promote("services.registry", "canary", verify_green=True, approval_id="a-self", actor="deployer-sa")
    assert "distinct" in str(exc.value)


def test_unknown_flag_promotion_is_blocked(engine: RolloutEngine) -> None:
    _grant(engine, "services.nope", "canary", "a-nope")
    with pytest.raises(RolloutError):
        engine.promote("services.nope", "canary", verify_green=True, approval_id="a-nope", actor="deployer-sa")


def test_canary_to_gradual_requires_canary_health(engine: RolloutEngine) -> None:
    _grant(engine, "services.registry", "canary", "a1")
    engine.promote("services.registry", "canary", verify_green=True, approval_id="a1", actor="deployer-sa")

    _grant(engine, "services.registry", "gradual", "a2")
    # Missing canary-health signal blocks the ramp-up.
    with pytest.raises(RolloutError) as exc:
        engine.promote("services.registry", "gradual", verify_green=True, approval_id="a2", actor="deployer-sa")
    assert "canary_health_ok" in str(exc.value)

    # With canary health green the promotion is allowed and starts the ramp.
    flag = engine.promote(
        "services.registry", "gradual", verify_green=True, approval_id="a2",
        actor="deployer-sa", canary_health_ok=True,
    )
    assert flag.stage is RolloutStage.GRADUAL
    assert flag.rollout_pct == 10  # first ramp step


def test_jump_off_to_full_is_blocked(engine: RolloutEngine) -> None:
    _grant(engine, "services.registry", "full", "a-full")
    with pytest.raises(RolloutError):
        engine.promote(
            "services.registry", "full", verify_green=True, approval_id="a-full",
            actor="deployer-sa", canary_health_ok=True, gradual_complete=True,
        )


def test_every_promotion_is_audited(engine: RolloutEngine) -> None:
    _grant(engine, "services.registry", "canary", "a1")
    engine.promote("services.registry", "canary", verify_green=True, approval_id="a1", actor="deployer-sa")
    records = engine.audit.records()
    assert len(records) == 1
    assert records[0]["action"] == "promote"
    assert records[0]["flag"] == "services.registry"
    assert records[0]["from_stage"] == "off"
    assert records[0]["to_stage"] == "canary"
    assert records[0]["verify_green"] is True
    assert engine.audit.verify()
