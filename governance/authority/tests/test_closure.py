"""End-to-end closure: the gate AND the repo's make verify, with recorded output."""

from __future__ import annotations

from typing import Any, Dict

import model
from conftest import SHA_A, SHA_B, evidence, with_evidence

AO = "kushin77/agent-orchestrator"
AO_ITEM = "kushin77/agent-orchestrator#150"
CU_ITEM = "kushin77/capital-underwriting#1391"


def _patched(document: Dict[str, Any], **item_overrides: Any) -> model.Matrix:
    return model.build_matrix(with_evidence(document, AO_ITEM, **item_overrides))


def test_an_item_without_evidence_is_not_closed(matrix: model.Matrix) -> None:
    decision = model.is_closed(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "closure-gate-evidence-missing"
    assert decision.verdict.exit_code == 1


def test_the_shipped_matrix_reports_every_item_open(matrix: model.Matrix) -> None:
    report = model.closure_report(matrix)
    assert len(report) == len(matrix.work_items)
    assert all(one.denied for one in report), [one.render() for one in report]


def test_both_evidence_records_with_real_output_close_the_item(document) -> None:
    matrix = _patched(document)
    assert matrix.valid
    decision = model.is_closed(matrix, AO_ITEM)
    assert decision.ok, decision.render()


def test_a_missing_gate_evidence_record_is_a_denial(document) -> None:
    patched = with_evidence(document, AO_ITEM)
    for item in patched["work_items"]:
        if item["id"] == AO_ITEM:
            del item["gate_evidence"]
    decision = model.is_closed(model.build_matrix(patched), AO_ITEM)
    assert decision.denied
    assert decision.reason == "closure-gate-evidence-missing"


def test_a_missing_verify_evidence_record_is_a_denial(document) -> None:
    patched = with_evidence(document, AO_ITEM)
    for item in patched["work_items"]:
        if item["id"] == AO_ITEM:
            del item["verify_evidence"]
    decision = model.is_closed(model.build_matrix(patched), AO_ITEM)
    assert decision.denied
    assert decision.reason == "closure-verify-evidence-missing"


def test_an_empty_output_is_a_denial_never_a_pass(document) -> None:
    matrix = _patched(document, output="")
    assert matrix.valid, "an empty value is a closure denial, not a document defect"
    decision = model.is_closed(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "closure-gate-evidence-output-missing"


def test_a_whitespace_only_output_is_a_denial(document) -> None:
    for value in ("", " ", "\n\t "):
        matrix = _patched(document, output=value)
        decision = model.is_closed(matrix, AO_ITEM)
        assert decision.denied, f"{value!r} must not close an item"


def test_a_missing_output_field_is_a_denial(document) -> None:
    patched = with_evidence(document, AO_ITEM)
    for item in patched["work_items"]:
        if item["id"] == AO_ITEM:
            del item["gate_evidence"]["output"]
    decision = model.is_closed(model.build_matrix(patched), AO_ITEM)
    assert decision.denied
    assert decision.reason == "closure-gate-evidence-output-missing"


def test_a_missing_command_is_a_denial(document) -> None:
    patched = with_evidence(document, AO_ITEM)
    for item in patched["work_items"]:
        if item["id"] == AO_ITEM:
            del item["gate_evidence"]["command"]
    decision = model.is_closed(model.build_matrix(patched), AO_ITEM)
    assert decision.denied
    assert decision.reason == "closure-gate-evidence-command-missing"


def test_failing_work_never_closes(document) -> None:
    matrix = _patched(document, exit_code=1)
    decision = model.is_closed(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "closure-gate-evidence-failed"


def test_evidence_that_does_not_name_the_item_head_is_a_denial(document) -> None:
    patched = with_evidence(document, AO_ITEM, git_sha=SHA_B)
    for item in patched["work_items"]:
        if item["id"] == AO_ITEM:
            del item["verify_evidence"]
            item["verify_evidence"] = evidence(AO, command="make verify", output="verify: PASS", git_sha=SHA_A)
    decision = model.is_closed(model.build_matrix(patched), AO_ITEM)
    assert decision.denied
    assert decision.reason == "closure-gate-evidence-sha-mismatch"


def test_a_malformed_commit_identity_is_a_denial(document) -> None:
    matrix = _patched(document, git_sha="not-a-sha")
    decision = model.is_closed(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "closure-gate-evidence-sha-missing"


def test_divergent_commits_between_the_two_evidences_are_a_denial(document) -> None:
    patched = with_evidence(document, AO_ITEM)
    for item in patched["work_items"]:
        if item["id"] == AO_ITEM:
            del item["head_sha"]
            item["verify_evidence"] = evidence(AO, command="make verify", output="verify: PASS", git_sha=SHA_B)
    decision = model.is_closed(model.build_matrix(patched), AO_ITEM)
    assert decision.denied
    assert decision.reason == "closure-evidence-sha-divergence"


def test_evidence_recorded_in_another_repo_is_a_denial(document) -> None:
    patched = with_evidence(document, AO_ITEM)
    for item in patched["work_items"]:
        if item["id"] == AO_ITEM:
            item["gate_evidence"]["repo"] = "kushin77/capital-underwriting"
    decision = model.is_closed(model.build_matrix(patched), AO_ITEM)
    assert decision.denied
    assert decision.reason == "closure-gate-evidence-repo-mismatch"


def test_evidence_attributed_to_a_principal_from_another_fleet_is_a_denial(document) -> None:
    matrix = _patched(document, recorded_by="cu-scribe-1")
    decision = model.is_closed(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "closure-gate-evidence-recorder-out-of-scope"


def test_evidence_attributed_to_an_unknown_principal_is_a_denial(document) -> None:
    matrix = _patched(document, recorded_by="ghost-scribe")
    decision = model.is_closed(matrix, AO_ITEM)
    assert decision.denied
    assert decision.reason == "closure-gate-evidence-recorder-unknown"


def test_the_enterprise_controller_may_record_roll_up_evidence(document) -> None:
    matrix = _patched(document, recorded_by="enterprise-controller")
    decision = model.is_closed(matrix, AO_ITEM)
    assert decision.ok, decision.render()


def test_an_unknown_work_item_is_cannot_assess(matrix: model.Matrix) -> None:
    decision = model.is_closed(matrix, "kushin77/agent-orchestrator#999999")
    assert decision.cannot_assess
    assert decision.reason == "work-item-unknown"


def test_closure_on_an_invalid_matrix_is_cannot_assess(document) -> None:
    matrix = model.matrix_from_overlay(document, {"principals": [{"id": "cu-fleet", "cross_repo": True}]})
    decision = model.is_closed(matrix, AO_ITEM)
    assert decision.cannot_assess
    assert decision.reason == "matrix-invalid"


def test_each_repo_closes_its_own_work_item(document) -> None:
    matrix = _patched(document)
    patched = with_evidence(matrix.document, CU_ITEM, recorded_by="cu-scribe-1")
    closed = model.build_matrix(patched)
    assert closed.valid
    assert model.is_closed(closed, CU_ITEM).ok
    assert model.is_closed(closed, AO_ITEM).ok


def test_mutating_the_evidence_rule_flips_the_verdict(monkeypatch, document) -> None:
    """Negative control: closure comes from the evidence rule, not from the absence of one."""
    matrix = model.build_matrix(document)
    assert model.is_closed(matrix, AO_ITEM).reason == "closure-gate-evidence-missing"
    monkeypatch.setattr(model, "_check_evidence", lambda *args, **kwargs: None)
    assert model.is_closed(matrix, AO_ITEM).denied, "an evidence-less item must still not close"
    assert model.is_closed(matrix, AO_ITEM).reason == "closure-evidence-sha-divergence"
    monkeypatch.undo()
    assert model.is_closed(matrix, AO_ITEM).denied
