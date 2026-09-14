"""Authority decisions: scoped admin rights, one cross-repo actor, fail-closed unknowns."""

from __future__ import annotations

import pytest

import model

AO = "kushin77/agent-orchestrator"
CU = "kushin77/capital-underwriting"
ENTERPRISE = "enterprise-controller"


@pytest.mark.parametrize("action", ["files", "issues", "state"])
def test_a_fleet_may_act_on_its_own_repo(matrix: model.Matrix, action: str) -> None:
    decision = model.can_act(matrix, "ao-soldier-1", AO, action)
    assert decision.ok, decision.render()
    assert decision.reason == "ok"


@pytest.mark.parametrize("action", ["files", "issues", "state", "merge"])
def test_a_fleet_may_not_act_on_another_repos_artifacts(matrix: model.Matrix, action: str) -> None:
    principal = "ao-commander" if action == "merge" else "ao-soldier-1"
    decision = model.can_act(matrix, principal, CU, action)
    assert decision.denied, decision.render()
    assert decision.reason == "out-of-scope"


def test_the_denial_is_symmetric_between_repos(matrix: model.Matrix) -> None:
    assert model.can_act(matrix, "cu-soldier-1", AO, "files").reason == "out-of-scope"
    assert model.can_act(matrix, "cu-soldier-1", CU, "files").ok


def test_the_fleet_principal_itself_is_subject_to_the_same_scope(matrix: model.Matrix) -> None:
    assert model.can_act(matrix, "cu-fleet", CU, "state").ok
    assert model.can_act(matrix, "cu-fleet", AO, "state").denied


def test_the_enterprise_controller_is_the_only_cross_repo_actor(matrix: model.Matrix) -> None:
    for repo in (AO, CU):
        assert model.can_act(matrix, ENTERPRISE, repo, "files").ok
        assert model.can_act(matrix, ENTERPRISE, repo, "issues").ok
        assert model.can_act(matrix, ENTERPRISE, repo, "state").ok
    acts_everywhere = {
        one.id
        for one in matrix.principals
        if all(model.can_act(matrix, one.id, repo, "files").ok for repo in (AO, CU))
    }
    assert acts_everywhere == {ENTERPRISE}
    assert [one.id for one in matrix.principals if one.cross_repo] == [ENTERPRISE]


def test_rollup_is_reserved_for_the_cross_repo_actor(matrix: model.Matrix) -> None:
    assert model.can_act(matrix, ENTERPRISE, AO, model.ROLLUP).ok
    refused = model.can_act(matrix, "ao-lieutenant", AO, model.ROLLUP)
    assert refused.denied
    assert refused.reason == "cross-repo-denied"


def test_merge_authority_stays_with_the_repo_commander(matrix: model.Matrix) -> None:
    assert model.can_act(matrix, "ao-commander", AO, "merge").ok
    refused = model.can_act(matrix, "ao-lieutenant", AO, "merge")
    assert refused.reason == "merge-authority-denied"
    cross_repo_merge = model.can_act(matrix, ENTERPRISE, AO, "merge")
    assert cross_repo_merge.denied
    assert cross_repo_merge.reason == "merge-authority-denied"


def test_an_action_outside_the_delegated_rights_is_denied(document) -> None:
    matrix = model.matrix_from_overlay(document, {"principals": [{"id": "cu-fleet", "admin_rights": ["files"]}]})
    assert matrix.valid
    decision = model.can_act(matrix, "cu-soldier-1", CU, "issues")
    assert decision.denied
    assert decision.reason == "admin-right-not-granted"


def test_an_unknown_principal_is_cannot_assess_never_a_pass(matrix: model.Matrix) -> None:
    decision = model.can_act(matrix, "ghost-principal", AO, "files")
    assert decision.cannot_assess
    assert decision.reason == "principal-unknown"
    assert decision.verdict.exit_code == 2


def test_an_unknown_repo_is_cannot_assess(matrix: model.Matrix) -> None:
    decision = model.can_act(matrix, "ao-soldier-1", "kushin77/undeclared-repo", "files")
    assert decision.cannot_assess
    assert decision.reason == "repo-unknown"


def test_an_unknown_action_is_cannot_assess(matrix: model.Matrix) -> None:
    decision = model.can_act(matrix, "ao-soldier-1", AO, "deploy-to-production")
    assert decision.cannot_assess
    assert decision.reason == "action-unknown"


def test_every_decision_carries_a_closed_vocabulary_reason_code(matrix: model.Matrix) -> None:
    decisions = [
        model.can_act(matrix, "ao-soldier-1", AO, "files"),
        model.can_act(matrix, "ao-soldier-1", CU, "files"),
        model.can_act(matrix, "nobody", AO, "files"),
    ]
    for decision in decisions:
        assert decision.reason in model.REASONS


def test_unknown_reason_codes_cannot_be_constructed() -> None:
    with pytest.raises(model.AuthorityError):
        model.Decision(model.Verdict.DENY, "because-i-said-so")


def test_a_decision_on_an_invalid_matrix_is_cannot_assess(document) -> None:
    matrix = model.matrix_from_overlay(document, {"principals": [{"id": "cu-fleet", "cross_repo": True}]})
    decision = model.can_act(matrix, "ao-soldier-1", AO, "files")
    assert decision.cannot_assess
    assert decision.reason == "matrix-invalid"


def test_mutating_the_scoping_rule_flips_the_verdict(monkeypatch, matrix: model.Matrix) -> None:
    """Negative control: the cross-repo DENIAL comes from the scoping rule, not from noise."""
    assert model.can_act(matrix, "ao-soldier-1", CU, "files").denied
    monkeypatch.setattr(model, "_principal_covers_repo", lambda principal, repo: True)
    assert model.can_act(matrix, "ao-soldier-1", CU, "files").ok
    monkeypatch.undo()
    assert model.can_act(matrix, "ao-soldier-1", CU, "files").denied


def test_mutating_the_merge_rule_flips_the_verdict(monkeypatch, matrix: model.Matrix) -> None:
    assert model.can_act(matrix, "ao-lieutenant", AO, "merge").denied
    original = model.Principal.actor

    def permissive_actor(self, actor_id):  # type: ignore[no-untyped-def]
        found = original(self, actor_id)
        return None if found is None else model.Actor(
            id=found.id,
            role=found.role,
            posture=found.posture,
            model_tier=found.model_tier,
            merge_authority=True,
            principal=found.principal,
        )

    monkeypatch.setattr(model.Principal, "actor", permissive_actor)
    assert model.can_act(matrix, "ao-lieutenant", AO, "merge").ok
    monkeypatch.undo()
    assert model.can_act(matrix, "ao-lieutenant", AO, "merge").denied


def test_effective_rights_never_exceed_what_the_repo_delegates(document) -> None:
    matrix = model.matrix_from_overlay(document, {"principals": [{"id": "cu-fleet", "admin_rights": ["files", "state"]}]})
    assert matrix.valid
    fleet = matrix.principal("cu-fleet")
    repo = matrix.repo(CU)
    assert fleet is not None and repo is not None
    assert set(model.effective_rights(fleet, repo)) == {"files", "state"}
    assert model.can_act(matrix, "cu-soldier-1", CU, "issues").reason == "admin-right-not-granted"
    assert model.can_act(matrix, "cu-soldier-1", CU, "state").ok


def test_no_allow_escapes_a_principals_scope_sweep(matrix: model.Matrix) -> None:
    """Sweep every actor x repo x action: an ALLOW outside the principal's scope is a leak."""
    for principal in matrix.principals:
        for actor in principal.actors:
            for repo in matrix.repos:
                for action in model.ACTIONS:
                    decision = model.can_act(matrix, actor.id, repo.id, action)
                    if decision.ok:
                        assert repo.id in principal.scope, f"{actor.id} leaked onto {repo.id}"
