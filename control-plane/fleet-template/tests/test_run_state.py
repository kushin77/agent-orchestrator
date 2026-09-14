"""Definition vs run-state: `unverified` is never reported as present.

The mature distinction this lane harvests: the composition is DECLARED, the
run state is OBSERVED. A declared member with no observation is `unverified`
-- an honest "I do not know" -- and an unknown must never be counted as a
present member. Every test here tries to make the renderer claim more than it
knows.
"""

from __future__ import annotations

import render


def _observation(repo, observed, source="test fixture observation"):
    return {
        "apiVersion": "ao.fleet-template/v1",
        "kind": "FleetRunStateObservation",
        "repo": repo,
        "source": source,
        "observed_present": list(observed),
    }


def test_unverified_is_never_reported_as_present(pilot, renderer):
    run_state = renderer(pilot)["run_state"]
    tri = run_state["tri_state"]
    assert not set(tri["unverified"]) & set(run_state["present"])
    assert run_state["present"] == tri["required"]
    assert set(tri["required"]) & set(tri["unverified"]) == set()


def test_the_tri_state_partitions_the_declared_members(pilot, renderer):
    run_state = renderer(pilot)["run_state"]
    declared = set(run_state["declared"]["required"]) | set(run_state["declared"]["parked"])
    tri = run_state["tri_state"]
    covered = set(tri["required"]) | set(tri["unverified"]) | set(tri["parked"])
    assert covered == declared
    assert len(tri["required"]) + len(tri["unverified"]) == len(run_state["declared"]["required"])


def test_a_parked_member_is_parked_not_present(pilot, renderer):
    run_state = renderer(pilot)["run_state"]
    assert run_state["declared"]["parked"], "no pilot exercises the parked state"
    for member in run_state["declared"]["parked"]:
        assert member in run_state["tri_state"]["parked"]
        assert member not in run_state["present"]
        assert member not in run_state["tri_state"]["unverified"]


def test_no_observation_means_every_required_member_is_unverified(renderer):
    instance = renderer("agent-orchestrator", observations=None)
    run_state = instance["run_state"]
    assert run_state["present"] == []
    assert run_state["counts"]["present"] == 0
    assert set(run_state["tri_state"]["unverified"]) == set(run_state["declared"]["required"])
    assert run_state["tri_state"]["required"] == []
    assert "no run-state observation" in run_state["observations"]["source"]


def test_an_empty_observation_file_changes_nothing_about_presence(renderer):
    instance = renderer("agent-orchestrator", observations=_observation("agent-orchestrator", []))
    assert instance["run_state"]["present"] == []
    assert instance["run_state"]["counts"]["unverified"] > 0


def test_observing_a_subset_marks_exactly_that_subset_present(renderer):
    observed = ["agent-orchestrator:commander", "agent-orchestrator:auditor"]
    instance = renderer("agent-orchestrator", observations=_observation("agent-orchestrator", observed))
    run_state = instance["run_state"]
    assert run_state["present"] == observed
    assert set(run_state["tri_state"]["unverified"]) == set(run_state["declared"]["required"]) - set(observed)


def test_a_foreign_observed_member_never_becomes_present(renderer):
    observed = ["agent-orchestrator:commander", "other-repo:commander"]
    instance = renderer("agent-orchestrator", observations=_observation("agent-orchestrator", observed))
    assert "other-repo:commander" not in instance["run_state"]["present"]
    codes = {finding.code for finding in render.check_invariants(instance)}
    assert "OBSERVATION_FOREIGN_MEMBER" in codes


def test_a_parked_member_observed_running_is_a_finding(renderer):
    instance = renderer(
        "agent-orchestrator",
        observations=_observation("agent-orchestrator", ["agent-orchestrator:advisor"]),
    )
    codes = {finding.code for finding in render.check_invariants(instance)}
    assert "PARKED_MEMBER_OBSERVED" in codes
    assert "agent-orchestrator:advisor" not in instance["run_state"]["present"]


def test_hand_editing_present_to_include_an_unverified_member_is_caught(renderer):
    """The invariant is not vacuous: force the corruption it is meant to catch."""
    instance = renderer("agent-orchestrator", observations=None)
    unverified = instance["run_state"]["tri_state"]["unverified"]
    assert unverified, "fixture has nothing unverified to corrupt"
    instance["run_state"]["present"] = [unverified[0]]
    instance["run_state"]["counts"]["present"] = 1

    codes = {finding.code for finding in render.check_invariants(instance)}
    assert "UNVERIFIED_REPORTED_PRESENT" in codes
    assert "PRESENT_NOT_OBSERVED_SET" in codes


def test_counts_that_disagree_with_the_lists_are_caught(renderer):
    instance = renderer("agent-orchestrator")
    instance["run_state"]["counts"]["present"] = 999
    codes = {finding.code for finding in render.check_invariants(instance)}
    assert "RUN_STATE_COUNTS" in codes


def test_the_report_cli_shows_unverified_as_not_present(lane_root, capsys):
    params = f"{lane_root}/{render.PILOTS_DIR}/agent-orchestrator.params.yaml"
    assert render.main(["--root", lane_root, "report", "--params", params]) == render.EXIT_OK
    out = capsys.readouterr().out
    assert "unverified" in out
    assert "(NOT present)" in out


def test_the_pilots_between_them_exercise_all_three_states(renderer, pilots):
    states = set()
    for pilot in pilots:
        tri = renderer(pilot)["run_state"]["tri_state"]
        for name, members in tri.items():
            if members:
                states.add(name)
    assert states == {"required", "parked", "unverified"}
