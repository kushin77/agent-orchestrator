"""Go-live plan tests (issue #45): every Phase 0-8 surface maps to a flag.

The plan must cover every phase 0-8, each surface's flag must resolve to a
known (rollout-state-declared) flag, and every declared go-live stage must be
part of the closed stage vocabulary.
"""

from __future__ import annotations

import copy

import yaml

from infra.rollout.model import StageModel, validate_go_live_plan_doc


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _model(stage_model_path: str) -> StageModel:
    return StageModel.load(_load(stage_model_path))


def test_committed_plan_is_valid(plan_path: str, state_path: str, stage_model_path: str) -> None:
    plan = _load(plan_path)
    state = _load(state_path)
    known = list(state["flags"].keys())
    assert validate_go_live_plan_doc(plan, known, _model(stage_model_path)) == []


def test_plan_covers_all_phases(plan_path: str) -> None:
    plan = _load(plan_path)
    covered = set(plan["phases"].keys())
    assert {"0", "1", "2", "3", "4", "5", "6", "7", "8"} <= covered


def test_plan_phase_order_is_complete(plan_path: str) -> None:
    plan = _load(plan_path)
    assert plan["phase_order"] == ["0", "1", "2", "3", "4", "5", "6", "7", "8"]


def test_every_phase_declares_a_surface(plan_path: str) -> None:
    plan = _load(plan_path)
    for phase, body in plan["phases"].items():
        assert isinstance(body.get("surfaces"), list) and body["surfaces"], f"phase {phase}"


def test_missing_phase_is_rejected(plan_path: str, state_path: str, stage_model_path: str) -> None:
    plan = copy.deepcopy(_load(plan_path))
    del plan["phases"]["5"]
    known = list(_load(state_path)["flags"].keys())
    errors = validate_go_live_plan_doc(plan, known, _model(stage_model_path))
    assert errors
    assert any("does not cover phase" in e for e in errors)


def test_unresolved_flag_is_rejected(plan_path: str, state_path: str, stage_model_path: str) -> None:
    plan = copy.deepcopy(_load(plan_path))
    plan["phases"]["5"]["surfaces"].append({"flag": "services.ghost", "go_live_stage": "full"})
    known = list(_load(state_path)["flags"].keys())
    errors = validate_go_live_plan_doc(plan, known, _model(stage_model_path))
    assert errors
    assert any("does not resolve" in e for e in errors)


def test_invalid_go_live_stage_is_rejected(plan_path: str, state_path: str, stage_model_path: str) -> None:
    plan = copy.deepcopy(_load(plan_path))
    plan["phases"]["5"]["surfaces"][0]["go_live_stage"] = "vapor"
    known = list(_load(state_path)["flags"].keys())
    errors = validate_go_live_plan_doc(plan, known, _model(stage_model_path))
    assert errors

