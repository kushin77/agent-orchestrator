"""The shipped matrix is the governance artifact — these tests hold it to its own rules."""

from __future__ import annotations

from typing import Any, Dict

import model
import importlib.util as _importlib_util  # noqa: E402
from pathlib import Path as _ConftestPath  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702, #1042).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_authority_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
SHA_A = _conftest.SHA_A


def _overlay(document: Dict[str, Any], overlay: Dict[str, Any]) -> model.Matrix:
    return model.matrix_from_overlay(document, overlay)


def _findings(matrix: model.Matrix) -> str:
    return " | ".join(matrix.violations)


def test_shipped_matrix_is_valid(matrix: model.Matrix) -> None:
    assert matrix.faults == (), matrix.faults
    assert matrix.violations == (), matrix.violations
    assert matrix.valid
    assert matrix.gate_decision().ok


def test_shipped_matrix_declares_two_repos_with_one_fleet_each(matrix: model.Matrix) -> None:
    assert len(matrix.repos) == 2
    for repo in matrix.repos:
        fleet = matrix.principal(repo.fleet)
        assert fleet is not None, repo.fleet
        assert fleet.kind == "repo-fleet"
        assert fleet.scope == (repo.id,)


def test_exactly_one_cross_repo_principal_and_it_is_the_enterprise_controller(matrix: model.Matrix) -> None:
    cross = [one for one in matrix.principals if one.cross_repo]
    assert [one.id for one in cross] == ["enterprise-controller"]
    assert cross[0].kind == "enterprise-controller"
    assert sorted(cross[0].scope) == sorted(one.id for one in matrix.repos)
    assert model.ROLLUP in cross[0].admin_rights


def test_a_second_cross_repo_principal_is_a_validation_failure(document) -> None:
    matrix = _overlay(document, {"principals": [{"id": "cu-fleet", "cross_repo": True}]})
    assert not matrix.valid
    assert "cross-repo-single" in _findings(matrix)


def test_cross_repo_principal_that_is_not_the_enterprise_controller_is_a_validation_failure(document) -> None:
    matrix = _overlay(document, {"principals": [{"id": "enterprise-controller", "kind": "repo-fleet"}]})
    assert not matrix.valid
    assert "cross-repo-kind" in _findings(matrix)


def test_a_fleet_scoped_to_two_repos_is_a_validation_failure(document) -> None:
    matrix = _overlay(
        document,
        {"principals": [{"id": "ao-fleet", "scope": ["kushin77/agent-orchestrator", "kushin77/capital-underwriting"]}]},
    )
    assert not matrix.valid
    assert "fleet-scope-single" in _findings(matrix)


def test_rollup_right_on_a_scoped_fleet_is_a_validation_failure(document) -> None:
    matrix = _overlay(
        document, {"principals": [{"id": "ao-fleet", "admin_rights": ["files", "issues", "state", "merge", "rollup"]}]}
    )
    assert not matrix.valid
    assert "rollup-cross-repo-only" in _findings(matrix)


def test_a_fleet_cannot_hold_rights_the_repo_never_delegated(document) -> None:
    matrix = _overlay(document, {"repos": [{"id": "kushin77/capital-underwriting", "admin_rights": ["files"]}]})
    assert not matrix.valid
    assert "fleet-rights-subset" in _findings(matrix)


def test_merge_authority_outside_the_commander_is_a_validation_failure(document) -> None:
    matrix = _overlay(
        document,
        {"principals": [{"id": "ao-fleet", "actors": [{"id": "ao-soldier-1", "merge_authority": True}]}]},
    )
    assert not matrix.valid
    assert "merge-authority-commander" in _findings(matrix)


def test_an_auditor_role_with_a_non_auditor_posture_is_a_validation_failure(document) -> None:
    matrix = _overlay(
        document, {"principals": [{"id": "ao-fleet", "actors": [{"id": "ao-auditor-1", "posture": "executor"}]}]}
    )
    assert not matrix.valid
    assert "auditor-posture" in _findings(matrix)


def test_a_fleet_without_an_auditor_posture_cannot_satisfy_separation_of_duties(document) -> None:
    matrix = _overlay(
        document,
        {"principals": [{"id": "cu-fleet", "actors": [{"id": "cu-auditor-1", "role": "soldier", "posture": "executor"}]}]},
    )
    assert not matrix.valid
    assert "fleet-duty-coverage" in _findings(matrix)


def test_an_undeclared_repo_in_a_scope_is_a_validation_failure(document) -> None:
    matrix = _overlay(document, {"principals": [{"id": "cu-fleet", "scope": ["kushin77/ghost-repo"]}]})
    assert not matrix.valid
    assert "scope-repo-declared" in _findings(matrix)


def test_a_work_item_on_an_undeclared_repo_is_a_validation_failure(document) -> None:
    matrix = _overlay(
        document,
        {
            "work_items": [
                {
                    "id": "kushin77/ghost-repo#7",
                    "repo": "kushin77/ghost-repo",
                    "sod": {"executor": "ao-soldier-1", "reviewer": "ao-platoon-leader-1", "auditor": "ao-auditor-1"},
                }
            ]
        },
    )
    assert not matrix.valid
    assert "work-item-repo-undeclared" in _findings(matrix)


def test_unknown_vocabulary_value_is_a_defect_not_a_pass(document) -> None:
    matrix = _overlay(document, {"principals": [{"id": "ao-fleet", "kind": "freelancer"}]})
    assert not matrix.valid
    assert any("schema:" in one for one in matrix.violations)


def test_duplicate_actor_ids_across_principals_are_a_validation_failure(document) -> None:
    matrix = _overlay(
        document,
        {"principals": [{"id": "cu-fleet", "actors": [{"id": "ao-soldier-1", "role": "soldier", "posture": "executor", "model_tier": "LOW"}]}]},
    )
    assert not matrix.valid
    assert "actor-ids-unique" in _findings(matrix)


def test_an_unsupported_version_is_cannot_assess_never_a_pass(document) -> None:
    patched = dict(document)
    patched["version"] = 99
    matrix = model.build_matrix(patched)
    assert matrix.faults
    assert matrix.verdict()[0] is model.Verdict.CANNOT_ASSESS
    assert matrix.gate_decision().cannot_assess


def test_shipped_work_items_declare_a_valid_separation_of_duties(matrix: model.Matrix) -> None:
    for item in matrix.work_items:
        decision = model.separation_of_duties(matrix, item.id)
        assert decision.ok, decision.render()


def test_shipped_work_items_carry_no_committed_evidence(matrix: model.Matrix) -> None:
    """Evidence names a commit, so a committed attestation is stale by construction."""
    for item in matrix.work_items:
        assert item.evidence == {}, item.id


def test_matrix_from_overlay_does_not_mutate_the_source_document(document) -> None:
    before = repr(document)
    model.matrix_from_overlay(document, {"principals": [{"id": "ao-fleet", "cross_repo": True}]})
    assert repr(document) == before


def test_evidence_records_are_shape_checked_but_emptiness_is_a_closure_decision(document) -> None:
    """A wrong TYPE is a document defect; an empty VALUE is a closure denial (issue #150)."""
    wrong_type = _overlay(
        document,
        {"work_items": [{"id": "kushin77/agent-orchestrator#150", "head_sha": SHA_A, "gate_evidence": {"output": 42}}]},
    )
    assert not wrong_type.valid
    assert any("schema:" in one for one in wrong_type.violations)

    empty_value = _overlay(
        document,
        {"work_items": [{"id": "kushin77/agent-orchestrator#150", "gate_evidence": {"output": ""}}]},
    )
    assert empty_value.valid
    assert model.is_closed(empty_value, "kushin77/agent-orchestrator#150").denied
