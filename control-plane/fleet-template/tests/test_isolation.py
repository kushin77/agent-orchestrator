"""Per-repo isolation: two repos' fleets share no identity and no state.

Definition of done, second half. "Share no state" is measured, not argued: the
namespaces are compared, the member identities are compared, the render of one
pilot is hashed before and after rendering the other, and -- to prove the check
can actually fail -- one pilot is rendered while claiming the other's
namespace and the finding is required to appear.
"""

from __future__ import annotations

import hashlib
import os

import pytest

import render

ISO_KEYS = ("fleet_id", "state_prefix", "worktree_prefix", "lane_namespace")


def _digest(instance) -> str:
    return hashlib.sha256(render.dump_yaml(instance).encode("utf-8")).hexdigest()


def test_two_pilots_share_no_identity_or_state(renderer, pilots):
    instances = [renderer(name) for name in pilots]
    left, right = instances

    for key in ISO_KEYS:
        assert left["isolation"][key] != right["isolation"][key], f"shared isolation.{key}"
    assert not left["isolation"]["state_prefix"].startswith(right["isolation"]["state_prefix"])
    assert not right["isolation"]["state_prefix"].startswith(left["isolation"]["state_prefix"])

    left_members = set(render.member_ids(left))
    right_members = set(render.member_ids(right))
    assert left_members and right_members
    assert left_members.isdisjoint(right_members)

    assert all(member.startswith(left["definition"]["repo"] + ":") for member in left_members)
    assert all(member.startswith(right["definition"]["repo"] + ":") for member in right_members)
    assert render.check_isolation(instances) == []


def test_rendering_one_pilot_does_not_perturb_the_other(renderer, pilots):
    first, second = pilots
    before = _digest(renderer(first))
    renderer(second)
    after = _digest(renderer(first))
    assert before == after


def test_render_is_deterministic_across_runs(pilot, renderer):
    first = renderer(pilot)
    second = renderer(pilot)
    assert _digest(first) == _digest(second)
    assert first == second


def test_the_committed_instance_file_is_byte_identical_to_a_fresh_render(pilot, lane_root, renderer):
    """The committed artifact is generated, not hand-forked: same bytes."""
    path = os.path.join(lane_root, render.PILOTS_DIR, f"{pilot}.fleet.yaml")
    with open(path, "r", encoding="utf-8") as handle:
        on_disk = handle.read()
    assert on_disk == render.dump_yaml(renderer(pilot))


def test_a_repo_claiming_another_fleet_namespace_is_a_finding(renderer, lane_root, pilots):
    first, second = pilots
    borrowed_doc = render.load_yaml(os.path.join(lane_root, render.PILOTS_DIR, f"{second}.params.yaml"))
    borrowed_doc["repo"] = first
    borrowed_doc["values"]["repo"] = first
    borrowed = renderer(params_doc=borrowed_doc, observations=None)
    assert borrowed["isolation"]["fleet_id"] == f"fleet-{first}"

    left = renderer(first)
    codes = {finding.code for finding in render.check_isolation([left, borrowed])}
    assert "ISOLATION_SHARED_FLEET_ID" in codes
    assert "ISOLATION_SHARED_STATE_PREFIX" in codes
    assert "ISOLATION_SHARED_MEMBER" in codes


def test_a_shared_worktree_prefix_is_a_finding(renderer, pilots):
    first, second = pilots
    left = renderer(first)
    right = renderer(second)
    right["isolation"] = dict(right["isolation"], worktree_prefix=left["isolation"]["worktree_prefix"])
    codes = {finding.code for finding in render.check_isolation([left, right])}
    assert "ISOLATION_SHARED_WORKTREE_PREFIX" in codes


def test_a_nested_state_prefix_is_a_finding(renderer, pilots):
    first, second = pilots
    left = renderer(first)
    right = renderer(second)
    right["isolation"] = dict(
        right["isolation"],
        state_prefix=left["isolation"]["state_prefix"] + "/sub",
    )
    codes = {finding.code for finding in render.check_isolation([left, right])}
    assert "ISOLATION_NESTED_STATE" in codes


def test_a_member_id_outside_the_repo_namespace_is_a_finding(renderer):
    instance = renderer("agent-orchestrator")
    instance["definition"]["roles"][0]["id"] = "some-other-repo:commander"
    codes = {finding.code for finding in render.check_invariants(instance)}
    assert "MEMBER_ID_NOT_NAMESPACED" in codes


def test_an_observation_naming_another_fleets_member_is_a_finding(renderer):
    foreign = {
        "apiVersion": "ao.fleet-template/v1",
        "kind": "FleetRunStateObservation",
        "repo": "agent-orchestrator",
        "source": "test fixture naming a foreign member",
        "observed_present": ["agent-orchestrator:commander", "shared-frontend:commander"],
    }
    instance = renderer("agent-orchestrator", observations=foreign)
    codes = {finding.code for finding in render.check_invariants(instance)}
    assert "OBSERVATION_FOREIGN_MEMBER" in codes


def test_an_observation_attached_to_the_wrong_repo_is_a_hard_failure(renderer):
    wrong_repo = {
        "apiVersion": "ao.fleet-template/v1",
        "kind": "FleetRunStateObservation",
        "repo": "shared-frontend",
        "source": "test fixture with a mismatched repo",
        "observed_present": [],
    }
    with pytest.raises(render.InputError):
        renderer("agent-orchestrator", observations=wrong_repo)
