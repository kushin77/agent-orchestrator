"""Default-OFF guarantee tests (issue #45 / AO-GR-6).

A new flag that declares a default of anything but OFF must be rejected -
this is the negative test the acceptance criteria demand (a flag defaulting ON
is refused, never silently accepted).
"""

from __future__ import annotations

import yaml

import pytest

from infra.rollout.model import validate_rollout_state_doc


def test_committed_state_is_all_off(state_path: str) -> None:
    with open(state_path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert validate_rollout_state_doc(doc) == []


def test_default_on_flag_is_rejected() -> None:
    doc = {
        "schema_version": 1,
        "flags": {
            "services.registry": {"stage": "full", "rollout_pct": 100, "targeted": []},
        },
    }
    errors = validate_rollout_state_doc(doc)
    assert errors
    assert any("must default to off" in e for e in errors)


def test_canary_default_is_rejected() -> None:
    doc = {
        "schema_version": 1,
        "flags": {
            "rollout.pipeline": {"stage": "canary", "rollout_pct": 5, "targeted": []},
        },
    }
    errors = validate_rollout_state_doc(doc)
    assert errors
    assert any("must default to off" in e for e in errors)


def test_off_flag_with_nonzero_pct_is_rejected() -> None:
    doc = {
        "schema_version": 1,
        "flags": {
            "services.gateway": {"stage": "off", "rollout_pct": 40, "targeted": []},
        },
    }
    errors = validate_rollout_state_doc(doc)
    assert errors
    assert any("off but rollout_pct" in e for e in errors)


def test_unknown_stage_default_is_rejected() -> None:
    doc = {
        "schema_version": 1,
        "flags": {
            "services.gateway": {"stage": "mystery", "rollout_pct": 0, "targeted": []},
        },
    }
    errors = validate_rollout_state_doc(doc)
    assert errors


def test_engine_load_refuses_default_on_state(stage_model_path: str, tmp_path) -> None:
    from infra.rollout.engine import RolloutEngine, RolloutError

    bad_state = tmp_path / "bad-state.yaml"
    bad_state.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "flags": {
                    "services.registry": {"stage": "canary", "rollout_pct": 5, "targeted": []},
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RolloutError):
        RolloutEngine.load(stage_model_path=stage_model_path, rollout_state_path=str(bad_state))


def test_empty_flags_are_rejected() -> None:
    errors = validate_rollout_state_doc({"schema_version": 1, "flags": {}})
    assert errors
