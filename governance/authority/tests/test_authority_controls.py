"""The declared behavioral controls are the gate's teeth: they must be real and all met."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

import cli
import model

CONTROLS_PATH = cli.DEFAULT_CONTROLS_PATH


@pytest.fixture(scope="session")
def controls() -> List[Dict[str, Any]]:
    document = model.read_matrix_document(CONTROLS_PATH)
    return list(document["controls"])


def test_the_control_set_is_declared_and_non_empty(controls: List[Dict[str, Any]]) -> None:
    assert len(controls) >= 30, "a thin control set proves little"


def test_control_ids_are_unique(controls: List[Dict[str, Any]]) -> None:
    ids = [one["id"] for one in controls]
    assert len(set(ids)) == len(ids), "duplicate control id"


def test_every_control_declares_a_known_kind_and_expectation(controls: List[Dict[str, Any]]) -> None:
    kinds = {"can-act", "sod", "closure", "validate", "load", "isolation"}
    for control in controls:
        assert control["kind"] in kinds, control
        assert control["expect"] in {"OK", "NOT-OK", "ALLOW", "DENY", "CANNOT-ASSESS"}, control


def test_every_family_has_controls(controls: List[Dict[str, Any]]) -> None:
    declared = {one["kind"] for one in controls}
    assert declared == {"can-act", "sod", "closure", "validate", "load", "isolation"}
    for kind in ("sod", "closure", "validate"):
        denials = [one for one in controls if one["kind"] == kind and one["expect"] in ("DENY", "NOT-OK")]
        assert len(denials) >= 3, f"{kind} needs denial controls"


def test_controls_that_expect_a_verdict_carry_their_subject(controls: List[Dict[str, Any]]) -> None:
    for control in controls:
        if control["kind"] == "can-act":
            assert {"principal", "repo", "action"} <= set(control), control
        elif control["kind"] in ("sod", "closure"):
            assert "work_item" in control, control
        elif control["kind"] == "load":
            assert "raw" in control or control.get("missing"), control


def test_every_declared_control_meets_its_verdict(capsys) -> None:
    """The gate's own run: every control, against the live engine."""
    rc = cli.main(["--controls", str(CONTROLS_PATH), "selfcheck"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "MISMATCH" not in captured.out
    assert "selfcheck: OK" in captured.out


def test_selfcheck_is_not_a_no_op_when_the_engine_is_weakened(monkeypatch, capsys) -> None:
    """Mutation proof: disabling the scoping rule must turn selfcheck red (rc 1)."""
    monkeypatch.setattr(model, "_principal_covers_repo", lambda principal, repo: True)
    rc = cli.main(["--controls", str(CONTROLS_PATH), "selfcheck"])
    captured = capsys.readouterr()
    assert rc == 1, captured.out
    assert "MISMATCH" in captured.out
    assert "on-cu-repo-files-denied" in captured.out
    assert "selfcheck: FAIL" in captured.err
    monkeypatch.undo()


def test_selfcheck_is_not_a_no_op_when_the_sod_rule_is_weakened(monkeypatch, capsys) -> None:
    """Both SoD rules must be weakened before a collision gets through (defence in depth)."""
    monkeypatch.setattr(model, "_distinct_duty_violation", lambda assigned: None)
    monkeypatch.setattr(model, "_posture_violation", lambda assigned: None)
    rc = cli.main(["--controls", str(CONTROLS_PATH), "selfcheck"])
    captured = capsys.readouterr()
    assert rc == 1, captured.out
    assert "sod-executor-equals-reviewer-denied" in captured.out
    assert "MISMATCH" in captured.out
    monkeypatch.undo()


def test_selfcheck_is_not_a_no_op_when_the_closure_rule_is_weakened(monkeypatch, capsys) -> None:
    monkeypatch.setattr(model, "_check_evidence", lambda *args, **kwargs: None)
    rc = cli.main(["--controls", str(CONTROLS_PATH), "selfcheck"])
    captured = capsys.readouterr()
    assert rc == 1, captured.out
    failed = [line for line in captured.out.splitlines() if "MISMATCH" in line]
    assert any("closure-" in line for line in failed), failed
    monkeypatch.undo()


def test_an_empty_control_set_is_cannot_assess(tmp_path, capsys) -> None:
    path = tmp_path / "controls.yaml"
    path.write_text("version: 1\ncontrols: []\n", encoding="utf-8")
    rc = cli.main(["--controls", str(path), "selfcheck"])
    assert rc == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_a_missing_control_file_is_cannot_assess(tmp_path, capsys) -> None:
    rc = cli.main(["--controls", str(tmp_path / "absent.yaml"), "selfcheck"])
    assert rc == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_the_controls_file_lists_every_control(capsys) -> None:
    rc = cli.main(["--controls", str(CONTROLS_PATH), "controls"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "controls: " in captured.out
    assert "expect" in captured.out
