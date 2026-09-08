"""Stage model tests (issue #45): closed vocabulary, order and adjacency."""

from __future__ import annotations

import pytest
import yaml

from infra.rollout.model import (
    RolloutStage,
    StageModel,
    check_promotion,
    PromotionSignals,
)


@pytest.fixture(scope="module")
def model(stage_model_path: str) -> StageModel:
    with open(stage_model_path, encoding="utf-8") as fh:
        return StageModel.load(yaml.safe_load(fh))


def test_stage_order_is_monotonic(model: StageModel) -> None:
    orders = [model.spec(s).order for s in (RolloutStage.OFF, RolloutStage.CANARY, RolloutStage.GRADUAL, RolloutStage.FULL)]
    assert orders == [0, 1, 2, 3]


def test_exposure_only_above_off(model: StageModel) -> None:
    assert RolloutStage.OFF.exposed is False
    for stage in (RolloutStage.CANARY, RolloutStage.GRADUAL, RolloutStage.FULL):
        assert stage.exposed is True


def test_default_policy_is_off(model: StageModel) -> None:
    assert model.default_policy == "off"


def test_strict_forward_adjacency(model: StageModel) -> None:
    # Every one-step forward promotion is allowed; anything else is not.
    for current in RolloutStage:
        for target in RolloutStage:
            if target.order == current.order + 1:
                assert current.can_promote_to(target), f"{current} -> {target}"
            else:
                assert not current.can_promote_to(target), f"{current} -> {target}"


def test_unknown_stage_is_rejected() -> None:
    with pytest.raises(ValueError):
        RolloutStage.coerce("oops")


def test_bare_boolean_stage_is_rejected() -> None:
    # YAML 1.1 would coerce a bare `off`/`on` to a bool; that is never a stage.
    with pytest.raises(ValueError):
        RolloutStage.coerce(False)
    with pytest.raises(ValueError):
        RolloutStage.coerce(True)


def test_load_rejects_unknown_stage() -> None:
    doc = {
        "stages": {
            "off": {"order": 0, "rollout_pct": 0, "exposed": False},
            "canary": {"order": 1, "rollout_pct": 5, "exposed": True},
            "gradual": {"order": 2, "rollout_pct": 100, "exposed": True},
            "full": {"order": 3, "rollout_pct": 100, "exposed": True},
            "surprise": {"order": 4, "rollout_pct": 100, "exposed": True},
        },
        "promotion_rules": {"mode": "strict-forward", "every_transition_requires": ["verify_green"]},
    }
    with pytest.raises(ValueError):
        StageModel.load(doc)


def test_load_rejects_missing_stage() -> None:
    doc = {
        "stages": {
            "off": {"order": 0, "rollout_pct": 0, "exposed": False},
            "canary": {"order": 1, "rollout_pct": 5, "exposed": True},
            "gradual": {"order": 2, "rollout_pct": 100, "exposed": True},
        },
        "promotion_rules": {"mode": "strict-forward", "every_transition_requires": []},
    }
    with pytest.raises(ValueError):
        StageModel.load(doc)


def test_load_rejects_unknown_requirement() -> None:
    doc = {
        "stages": {
            "off": {"order": 0, "rollout_pct": 0, "exposed": False},
            "canary": {"order": 1, "rollout_pct": 5, "exposed": True},
            "gradual": {"order": 2, "rollout_pct": 100, "exposed": True},
            "full": {"order": 3, "rollout_pct": 100, "exposed": True},
        },
        "promotion_rules": {
            "mode": "strict-forward",
            "every_transition_requires": ["verify_magic"],
        },
    }
    with pytest.raises(ValueError):
        StageModel.load(doc)


def test_rollback_target_is_off(model: StageModel) -> None:
    assert model.rollback_target == "off"


def test_requirement_matrix(model: StageModel) -> None:
    # to-full requires the gradual ramp to be complete + canary health.
    assert "canary_health_ok" in model.target_requirements(RolloutStage.FULL)
    assert "gradual_complete" in model.target_requirements(RolloutStage.FULL)
    assert "canary_health_ok" in model.target_requirements(RolloutStage.GRADUAL)


def test_check_promotion_rejects_jump(model: StageModel) -> None:
    from infra.rollout.model import FlagState

    flag = FlagState(name="services.registry", stage=RolloutStage.OFF)
    verdict = check_promotion(
        model, flag, RolloutStage.FULL,
        PromotionSignals(verify_green=True, approval_id="a1", canary_health_ok=True, gradual_complete=True),
    )
    assert verdict.blocked
    assert any("not an allowed step" in r for r in verdict.reasons)
