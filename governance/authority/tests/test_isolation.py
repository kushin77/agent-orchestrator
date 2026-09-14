"""Two repos' fleets are fully isolated — demonstrated, not asserted (issue #150)."""

from __future__ import annotations

import isolation
import model

AO = "kushin77/agent-orchestrator"
CU = "kushin77/capital-underwriting"


def test_the_store_keeps_one_independent_state_per_repo(matrix: model.Matrix) -> None:
    store = isolation.RepoStateStore(matrix)
    assert store.repos == (AO, CU)
    assert store.state_sharing_ok()
    assert store.snapshot(AO) == {}
    assert store.snapshot(CU) == {}


def test_a_fleet_writes_and_reads_its_own_repo_state(matrix: model.Matrix) -> None:
    store = isolation.RepoStateStore(matrix)
    assert store.write("ao-soldier-1", AO, "claim:1", {"owner": "ao-soldier-1"}).ok
    decision, value = store.read("ao-soldier-1", AO, "claim:1")
    assert decision.ok
    assert value == {"owner": "ao-soldier-1"}
    keys_decision, keys = store.keys("ao-soldier-1", AO)
    assert keys_decision.ok
    assert keys == ["claim:1"]


def test_a_cross_repo_write_is_denied_and_leaves_the_target_untouched(matrix: model.Matrix) -> None:
    store = isolation.RepoStateStore(matrix)
    store.write("ao-soldier-1", AO, "claim:1", {"owner": "ao-soldier-1"})
    before = store.snapshot(AO)
    decision = store.write("cu-soldier-1", AO, "claim:2", {"owner": "cu-soldier-1"})
    assert decision.denied
    assert decision.reason == "out-of-scope"
    assert store.snapshot(AO) == before
    assert "claim:2" not in store.snapshot(AO)


def test_a_cross_repo_read_is_denied_and_yields_nothing(matrix: model.Matrix) -> None:
    store = isolation.RepoStateStore(matrix)
    store.write("ao-soldier-1", AO, "claim:1", {"owner": "ao-soldier-1"})
    decision, value = store.read("cu-lieutenant", AO, "claim:1")
    assert decision.denied
    assert value is None


def test_a_cross_repo_listing_is_denied(matrix: model.Matrix) -> None:
    store = isolation.RepoStateStore(matrix)
    store.write("ao-soldier-1", AO, "claim:1", {"owner": "ao-soldier-1"})
    decision, keys = store.keys("cu-commander", AO)
    assert decision.denied
    assert keys == []


def test_one_repos_state_never_appears_in_the_other(matrix: model.Matrix) -> None:
    store = isolation.RepoStateStore(matrix)
    store.write("ao-soldier-1", AO, "ao-only", 1)
    store.write("cu-soldier-1", CU, "cu-only", 2)
    assert store.snapshot(AO) == {"ao-only": 1}
    assert store.snapshot(CU) == {"cu-only": 2}
    assert store.state_sharing_ok()


def test_the_enterprise_controller_may_reach_both_repos(matrix: model.Matrix) -> None:
    store = isolation.RepoStateStore(matrix)
    store.write("ao-soldier-1", AO, "claim:1", {"owner": "ao-soldier-1"})
    store.write("cu-soldier-1", CU, "claim:1", {"owner": "cu-soldier-1"})
    for repo in (AO, CU):
        decision, value = store.read("enterprise-controller", repo, "claim:1")
        assert decision.ok, decision.render()
        assert value is not None
        assert model.can_act(matrix, "enterprise-controller", repo, model.ROLLUP).ok


def test_the_demonstrated_scenario_reports_isolation(matrix: model.Matrix) -> None:
    report = isolation.verify_isolation(matrix)
    assert report.isolated, report.findings
    assert report.cross_repo_principals == ("enterprise-controller",)
    assert len(report.repos) == 2
    labels = {one.label for one in report.steps}
    assert {"own-repo state write", "cross-repo state write", "cross-repo state read"} <= labels
    payload = report.to_dict()
    assert payload["isolated"] is True
    assert payload["findings"] == []


def test_the_only_actor_spanning_both_repos_is_the_enterprise_controller(matrix: model.Matrix) -> None:
    report = isolation.verify_isolation(matrix)
    assert report.allowed_cross_repo_actions == (
        "enterprise-commander -> ['kushin77/agent-orchestrator', 'kushin77/capital-underwriting']",
    )
    spanning = matrix.actor("enterprise-commander")
    assert spanning is not None and spanning.principal == "enterprise-controller"


def test_a_fleet_may_not_roll_up_across_repos(matrix: model.Matrix) -> None:
    decision = model.can_act(matrix, "ao-commander", AO, model.ROLLUP)
    assert decision.denied
    assert decision.reason == "cross-repo-denied"


def test_mutating_the_scoping_rule_is_detected_by_the_scenario(monkeypatch, matrix: model.Matrix) -> None:
    """Negative control: the scenario *detects* a weakened scoping rule instead of trusting it."""
    assert isolation.verify_isolation(matrix).isolated
    monkeypatch.setattr(model, "_principal_covers_repo", lambda principal, repo: True)
    report = isolation.verify_isolation(matrix)
    assert not report.isolated
    joined = " | ".join(report.findings)
    assert "cross-repo write allowed" in joined
    monkeypatch.undo()
    assert isolation.verify_isolation(matrix).isolated


def test_mutating_the_cross_repo_flag_is_detected(monkeypatch, matrix: model.Matrix) -> None:
    """A principal that spans repos without the cross_repo flag is a finding, not a pass."""
    original = model.can_act

    def leaky(candidate, principal, repo, action):  # type: ignore[no-untyped-def]
        if principal == "ao-commander" and action != model.ROLLUP:
            principal = "enterprise-controller"
        return original(candidate, principal, repo, action)

    monkeypatch.setattr(model, "can_act", leaky)
    report = isolation.verify_isolation(matrix)
    assert not report.isolated
    assert any("authority leak" in one for one in report.findings)
    monkeypatch.undo()
