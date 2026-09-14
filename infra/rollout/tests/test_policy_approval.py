"""Policy-based approval tier tests (issue #700).

Low-risk promotions (``off -> canary``, and ``canary -> gradual`` when the
canary health is green) are auto-approved on green verification evidence with
NO human approval code; the auto-approval is recorded in the audit trail
(who/when/why/which policy), never silent. The final promotion
(``gradual -> full``) and the downstream apply remain human-gated - the policy
can never bypass them.
"""

from __future__ import annotations

import time

import pytest

from infra.rollout.engine import Approval, RolloutEngine, RolloutError
from infra.rollout.model import RolloutStage


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


def _to_gradual(engine: RolloutEngine, flag: str = "services.registry") -> None:
    """Drive a flag to GRADUAL and ramp it to 100% (auto-approved hops)."""
    engine.promote(flag, "canary", verify_green=True, actor="deployer-sa")
    engine.promote(
        flag, "gradual", verify_green=True, actor="deployer-sa", canary_health_ok=True,
    )
    for pct in (25, 50, 100):
        engine.ramp(flag, pct, verify_green=True)


def test_off_to_canary_auto_approved_without_code(engine: RolloutEngine) -> None:
    flag = engine.promote("services.registry", "canary", verify_green=True, actor="deployer-sa")
    assert flag.stage is RolloutStage.CANARY
    assert flag.rollout_pct == 5
    assert engine.audit.verify()


def test_canary_to_gradual_auto_approved_with_health_ok(engine: RolloutEngine) -> None:
    engine.promote("services.registry", "canary", verify_green=True, actor="deployer-sa")
    flag = engine.promote(
        "services.registry", "gradual", verify_green=True,
        actor="deployer-sa", canary_health_ok=True,
    )
    assert flag.stage is RolloutStage.GRADUAL
    assert flag.rollout_pct == 10


def test_canary_to_gradual_still_requires_health(engine: RolloutEngine) -> None:
    engine.promote("services.registry", "canary", verify_green=True, actor="deployer-sa")
    with pytest.raises(RolloutError) as exc:
        engine.promote("services.registry", "gradual", verify_green=True, actor="deployer-sa")
    assert "canary_health_ok" in str(exc.value)


def test_auto_approval_is_recorded_in_audit(engine: RolloutEngine) -> None:
    engine.promote("services.registry", "canary", verify_green=True, actor="deployer-sa")
    records = engine.audit.records()
    assert len(records) == 1
    record = records[0]
    assert record["action"] == "promote"
    assert record["to_stage"] == "canary"
    assert record["approval_kind"] == "policy"
    assert record["policy"] == "low-risk-auto-approve"
    assert record["approval_id"] == ""
    assert "auto-approved by policy" in record["reason"]
    # who + when are always present on the audit record.
    assert record["actor"]
    assert record["ts"]
    assert engine.audit.verify()


def test_gradual_to_full_requires_human_approval(engine: RolloutEngine) -> None:
    _to_gradual(engine)
    with pytest.raises(RolloutError) as exc:
        engine.promote(
            "services.registry", "full", verify_green=True, actor="deployer-sa",
            canary_health_ok=True, gradual_complete=True,
        )
    assert "approval_code" in str(exc.value)


def test_full_promotion_with_human_code_is_not_policy(engine: RolloutEngine) -> None:
    _to_gradual(engine)
    _grant(engine, "services.registry", "full", "a-full")
    flag = engine.promote(
        "services.registry", "full", verify_green=True, approval_id="a-full",
        actor="deployer-sa", canary_health_ok=True, gradual_complete=True,
    )
    assert flag.stage is RolloutStage.FULL
    record = engine.audit.records()[-1]
    assert record["approval_kind"] == "human"
    assert record["approval_id"] == "a-full"
    assert engine.audit.verify()
