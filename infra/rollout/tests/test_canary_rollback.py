"""Canary-failure auto-rollback tests (issue #45) - the negative guarantee.

A failed canary/gradual health check must revert the flag to OFF - it can
never silently stay on. Both the pure decision (``rollback_decision``) and the
engine path (``observe_health``) are exercised, plus the audit record of the
auto-rollback.
"""

from __future__ import annotations

import time

import pytest

from infra.rollout.engine import Approval, RolloutEngine, RolloutError
from infra.rollout.model import (
    FlagState,
    RolloutStage,
    rollback_decision,
)


def _stage_model():
    from infra.rollout.model import StageModel
    import os
    import yaml

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    path = os.path.join(root, "infra", "rollout", "stage-model.yaml")
    with open(path, encoding="utf-8") as fh:
        return StageModel.load(yaml.safe_load(fh))


def test_rollback_decision_health_fail_returns_off() -> None:
    model = _stage_model()
    flag = FlagState(name="services.registry", stage=RolloutStage.CANARY, rollout_pct=5)
    assert rollback_decision(model, flag, health_ok=False) is RolloutStage.OFF


def test_rollback_decision_health_ok_is_no_move() -> None:
    model = _stage_model()
    flag = FlagState(name="services.registry", stage=RolloutStage.CANARY, rollout_pct=5)
    assert rollback_decision(model, flag, health_ok=True) is None


def test_rollback_decision_off_stays_off_on_failure() -> None:
    model = _stage_model()
    flag = FlagState(name="services.registry", stage=RolloutStage.OFF, rollout_pct=0)
    assert rollback_decision(model, flag, health_ok=False) is None


def test_rollback_decision_full_also_reverts_on_failure() -> None:
    # Even a FULL flag reverts to OFF on a health failure - never stays on.
    model = _stage_model()
    flag = FlagState(name="services.registry", stage=RolloutStage.FULL, rollout_pct=100)
    assert rollback_decision(model, flag, health_ok=False) is RolloutStage.OFF


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


def _promote_to_canary(engine: RolloutEngine) -> None:
    _grant(engine, "services.registry", "canary", "a1")
    engine.promote("services.registry", "canary", verify_green=True, approval_id="a1", actor="deployer-sa")


def test_engine_auto_rolls_back_failed_canary_to_off(engine: RolloutEngine) -> None:
    _promote_to_canary(engine)
    assert engine.flag("services.registry").stage is RolloutStage.CANARY

    flag = engine.observe_health("services.registry", health_ok=False, actor="health-monitor")
    # The negative guarantee: after a canary health failure the flag is OFF,
    # never silently left on.
    assert flag.stage is RolloutStage.OFF
    assert flag.rollout_pct == 0


def test_engine_healthy_canary_is_left_alone(engine: RolloutEngine) -> None:
    _promote_to_canary(engine)
    flag = engine.observe_health("services.registry", health_ok=True, actor="health-monitor")
    assert flag.stage is RolloutStage.CANARY


def test_auto_rollback_is_audited_with_health_reason(engine: RolloutEngine) -> None:
    _promote_to_canary(engine)
    engine.observe_health("services.registry", health_ok=False, actor="health-monitor")
    records = engine.audit.records()
    rollback_records = [r for r in records if r["action"] == "rollback"]
    assert len(rollback_records) == 1
    assert rollback_records[0]["from_stage"] == "canary"
    assert rollback_records[0]["to_stage"] == "off"
    assert rollback_records[0]["reason"] == "canary_health_failure"
    assert engine.audit.verify()


def test_health_failure_during_gradual_rolls_back(engine: RolloutEngine) -> None:
    _promote_to_canary(engine)
    _grant(engine, "services.registry", "gradual", "a2")
    engine.promote(
        "services.registry", "gradual", verify_green=True, approval_id="a2",
        actor="deployer-sa", canary_health_ok=True,
    )
    flag = engine.observe_health("services.registry", health_ok=False, actor="health-monitor")
    assert flag.stage is RolloutStage.OFF


def test_manual_rollback_of_exposed_flag(engine: RolloutEngine) -> None:
    _promote_to_canary(engine)
    flag = engine.manual_rollback("services.registry", actor="operator", reason="manual_rollback")
    assert flag.stage is RolloutStage.OFF
    assert engine.audit.verify()


def test_manual_rollback_of_off_flag_is_blocked(engine: RolloutEngine) -> None:
    with pytest.raises(RolloutError):
        engine.manual_rollback("services.registry", actor="operator")
