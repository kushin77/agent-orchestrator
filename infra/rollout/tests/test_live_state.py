"""Live-state persistence tests (issue #914, child of #607 go-live).

``infra/rollout/rollout-state.yaml`` stays the declared-default document
forever (GR-28) - its validator still refuses any flag above ``off``,
unchanged. A promoted stage is instead persisted to
``infra/rollout/live-state.yaml`` (``RolloutEngine.write_live_state`` /
``cli.py promote --live-state-out``), validated by
``validate_live_state_doc`` + ``checks/check_rollout.py::check_live_state``:
every non-off entry must reference an existing audit record, transitions
must respect stage-model ordering, and ``full`` entries must carry a human
approval id (a policy auto-approval can never reach ``full``).
"""

from __future__ import annotations

import os

import pytest
import yaml

from infra.rollout.engine import RolloutEngine, RolloutError
from infra.rollout.model import RolloutStage, StageModel, validate_live_state_doc


def _model(stage_model_path: str) -> StageModel:
    with open(stage_model_path, encoding="utf-8") as fh:
        return StageModel.load(yaml.safe_load(fh))


# --------------------------------------------------------------------------- #
# 1. promote persists to live-state
# --------------------------------------------------------------------------- #


def test_promote_persists_to_live_state(engine: RolloutEngine, tmp_path) -> None:
    engine.promote("services.registry", "canary", verify_green=True, actor="deployer-sa")
    live_path = tmp_path / "live-state.yaml"
    engine.write_live_state(str(live_path), audit_record="audit/some-record.md")

    with open(live_path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)

    assert doc["schema_version"] == 1
    entry = doc["flags"]["services.registry"]
    assert entry["stage"] == "canary"
    assert entry["from_stage"] == "off"
    assert entry["policy"] == "low-risk-auto-approve"
    assert entry["audit_record"] == "audit/some-record.md"
    assert entry["since"]

    # And it round-trips through the pure model validator with no errors.
    model = engine.model
    assert validate_live_state_doc(doc, list(engine.flags), model) == []


def test_engine_load_overlays_live_state_on_defaults(
    stage_model_path: str, state_path: str, tmp_path
) -> None:
    live_path = tmp_path / "live-state.yaml"
    with open(live_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(
            {
                "schema_version": 1,
                "flags": {
                    "services.registry": {
                        "stage": "canary",
                        "from_stage": "off",
                        "since": "2026-09-16T00:00:00Z",
                        "policy": "low-risk-auto-approve",
                        "audit_record": "audit/some-record.md",
                    }
                },
            },
            fh,
        )
    engine = RolloutEngine.load(
        stage_model_path=stage_model_path,
        rollout_state_path=state_path,
        live_state_path=str(live_path),
    )
    assert engine.flag("services.registry").stage is RolloutStage.CANARY
    # every other flag is unaffected - still the committed default off.
    assert engine.flag("services.gateway").stage is RolloutStage.OFF


# --------------------------------------------------------------------------- #
# 2. rollout-state.yaml (the defaults file) still refuses non-off - unchanged
# --------------------------------------------------------------------------- #


def test_rollout_state_defaults_file_still_refuses_non_off() -> None:
    from infra.rollout.model import validate_rollout_state_doc

    doc = {
        "schema_version": 1,
        "flags": {"services.registry": {"stage": "canary", "rollout_pct": 5, "targeted": []}},
    }
    errors = validate_rollout_state_doc(doc)
    assert errors
    assert any("must default to off" in e for e in errors)


def test_engine_load_refuses_a_promoted_rollout_state_even_with_live_state(
    stage_model_path: str, tmp_path
) -> None:
    """Committing a promoted flag in rollout-state.yaml is still a hard error,
    even when a (separately validated) live-state.yaml also exists."""
    bad_state = tmp_path / "bad-rollout-state.yaml"
    bad_state.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "flags": {"services.registry": {"stage": "canary", "rollout_pct": 5, "targeted": []}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RolloutError):
        RolloutEngine.load(stage_model_path=stage_model_path, rollout_state_path=str(bad_state))


# --------------------------------------------------------------------------- #
# 3. live-state refuses a missing audit record
# --------------------------------------------------------------------------- #


def test_live_state_refuses_missing_audit_record(stage_model_path: str, tmp_path) -> None:
    from infra.rollout.checks.check_rollout import check_live_state

    model = _model(stage_model_path)
    doc = {
        "schema_version": 1,
        "flags": {
            "services.registry": {
                "stage": "canary",
                "from_stage": "off",
                "since": "2026-09-16T00:00:00Z",
                "policy": "low-risk-auto-approve",
                "audit_record": "audit/does-not-exist.md",
            }
        },
    }
    errors = check_live_state(doc, ["services.registry"], model, rollout_dir=str(tmp_path))
    assert errors
    assert any("does not exist" in e for e in errors)


def test_live_state_accepts_an_existing_audit_record(stage_model_path: str, tmp_path) -> None:
    from infra.rollout.checks.check_rollout import check_live_state

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "real-record.md").write_text("# real audit record\n", encoding="utf-8")

    model = _model(stage_model_path)
    doc = {
        "schema_version": 1,
        "flags": {
            "services.registry": {
                "stage": "canary",
                "from_stage": "off",
                "since": "2026-09-16T00:00:00Z",
                "policy": "low-risk-auto-approve",
                "audit_record": "audit/real-record.md",
            }
        },
    }
    assert check_live_state(doc, ["services.registry"], model, rollout_dir=str(tmp_path)) == []


def test_live_state_requires_an_audit_record_field(stage_model_path: str) -> None:
    model = _model(stage_model_path)
    doc = {
        "schema_version": 1,
        "flags": {
            "services.registry": {
                "stage": "canary",
                "from_stage": "off",
                "since": "2026-09-16T00:00:00Z",
                "policy": "low-risk-auto-approve",
            }
        },
    }
    errors = validate_live_state_doc(doc, ["services.registry"], model)
    assert any("audit_record" in e for e in errors)


# --------------------------------------------------------------------------- #
# 4. full refuses a policy approval - only a human approval_id reaches full
# --------------------------------------------------------------------------- #


def test_live_state_full_refuses_policy_approval(stage_model_path: str) -> None:
    model = _model(stage_model_path)
    doc = {
        "schema_version": 1,
        "flags": {
            "services.registry": {
                "stage": "full",
                "from_stage": "gradual",
                "since": "2026-09-16T00:00:00Z",
                "policy": "low-risk-auto-approve",
                "audit_record": "audit/some-record.md",
            }
        },
    }
    errors = validate_live_state_doc(doc, ["services.registry"], model)
    assert errors
    assert any("full" in e and "human approval_id" in e for e in errors)


def test_live_state_full_with_human_approval_id_is_accepted(stage_model_path: str) -> None:
    model = _model(stage_model_path)
    doc = {
        "schema_version": 1,
        "flags": {
            "services.registry": {
                "stage": "full",
                "from_stage": "gradual",
                "since": "2026-09-16T00:00:00Z",
                "approval_id": "ao-2026-09-16-registry-full",
                "audit_record": "audit/some-record.md",
            }
        },
    }
    assert validate_live_state_doc(doc, ["services.registry"], model) == []


def test_live_state_rejects_a_non_adjacent_jump(stage_model_path: str) -> None:
    model = _model(stage_model_path)
    doc = {
        "schema_version": 1,
        "flags": {
            "services.registry": {
                "stage": "full",
                "from_stage": "off",
                "since": "2026-09-16T00:00:00Z",
                "approval_id": "ao-2026-09-16-registry-full",
                "audit_record": "audit/some-record.md",
            }
        },
    }
    errors = validate_live_state_doc(doc, ["services.registry"], model)
    assert errors
    assert any("not an allowed step" in e for e in errors)


def test_live_state_rejects_recording_off(stage_model_path: str) -> None:
    model = _model(stage_model_path)
    doc = {
        "schema_version": 1,
        "flags": {
            "services.registry": {
                "stage": "off",
                "since": "2026-09-16T00:00:00Z",
                "audit_record": "audit/some-record.md",
            }
        },
    }
    errors = validate_live_state_doc(doc, ["services.registry"], model)
    assert errors
    assert any("must not record 'off'" in e for e in errors)


# --------------------------------------------------------------------------- #
# 5. rollback removes the live-state entry (never leaves a stale promotion)
# --------------------------------------------------------------------------- #


def test_rollback_removes_the_live_state_entry(engine: RolloutEngine, tmp_path) -> None:
    engine.promote("services.registry", "canary", verify_green=True, actor="deployer-sa")
    live_path = tmp_path / "live-state.yaml"
    engine.write_live_state(str(live_path), audit_record="audit/some-record.md")
    with open(live_path, encoding="utf-8") as fh:
        before = yaml.safe_load(fh)
    assert "services.registry" in before["flags"]

    engine.observe_health("services.registry", health_ok=False, actor="health-monitor")
    engine.write_live_state(str(live_path), audit_record="audit/some-record.md")
    with open(live_path, encoding="utf-8") as fh:
        after = yaml.safe_load(fh)
    assert "services.registry" not in after["flags"]


def test_manual_rollback_removes_the_live_state_entry(engine: RolloutEngine, tmp_path) -> None:
    engine.promote("services.registry", "canary", verify_green=True, actor="deployer-sa")
    engine.manual_rollback("services.registry", actor="deployer-sa")
    live_path = tmp_path / "live-state.yaml"
    engine.write_live_state(str(live_path))
    with open(live_path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert doc["flags"] == {}


# --------------------------------------------------------------------------- #
# Committed tree: live-state.yaml is empty and valid today
# --------------------------------------------------------------------------- #


def test_committed_live_state_is_empty_and_valid(rollout_dir: str, stage_model_path: str, state_path: str) -> None:
    live_path = os.path.join(rollout_dir, "live-state.yaml")
    assert os.path.isfile(live_path)
    with open(live_path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert doc == {"schema_version": 1, "flags": {}}

    with open(state_path, encoding="utf-8") as fh:
        known_flags = list(yaml.safe_load(fh)["flags"])
    model = _model(stage_model_path)
    assert validate_live_state_doc(doc, known_flags, model) == []
