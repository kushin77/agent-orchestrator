"""The mint: a session identity is only useful if it cannot be forged or reused.

These tests pin the parts of the identity that other lanes and the gate rely
on — the branch that names the issue, the deterministic session id, and the
non-human signature — plus the refusals that keep an unsafe id out of a branch
name, a path or a git config value.
"""

from __future__ import annotations

import pytest

from governance.isolation.identity import (
    IDENTITY_DOMAIN,
    IdentityRefused,
    branch_for,
    branch_issue,
    commit_trailer,
    mint,
    session_id_for,
    worktree_name_for,
)

ISSUE = 263
AGENT = "copilot-brain"
LANE = "governance-isolation"


def test_branch_is_named_after_the_issue():
    assert branch_for(ISSUE) == "issue-263"
    assert branch_issue(branch_for(ISSUE)) == ISSUE


def test_branch_suffix_disambiguates_without_hiding_the_issue():
    branch = branch_for(ISSUE, "second")
    assert branch == "issue-263-second"
    assert branch_issue(branch) == ISSUE


def test_branch_that_names_no_issue_is_not_a_lane_branch():
    assert branch_issue("master") is None
    assert branch_issue("feature/whatever") is None
    assert branch_issue("issue-abc") is None


def test_mint_is_deterministic_so_a_redispatch_reattaches():
    first = mint(ISSUE, AGENT, LANE, worktree_root="/tmp/lanes")
    second = mint(ISSUE, AGENT, LANE, worktree_root="/tmp/lanes")
    assert first == second
    assert first.session_id == second.session_id


def test_distinct_lanes_get_distinct_identities():
    assert session_id_for(ISSUE, AGENT, LANE) != session_id_for(ISSUE, "qa-sme", LANE)
    assert session_id_for(ISSUE, AGENT, LANE) != session_id_for(264, AGENT, LANE)


def test_signature_is_non_human_and_bound_to_the_agent():
    identity = mint(ISSUE, AGENT, LANE, worktree_root="/tmp/lanes")
    assert identity.author_name == "agent-copilot-brain"
    assert identity.author_email == f"agent+{AGENT}@{IDENTITY_DOMAIN}"
    assert identity.author_email.endswith(".invalid")  # RFC 2606: never routable


def test_env_carries_both_the_session_id_and_the_signature():
    identity = mint(ISSUE, AGENT, LANE, worktree_root="/tmp/lanes")
    env = identity.env()
    assert env["AO_SESSION_ID"] == identity.session_id
    assert env["AO_ISSUE"] == str(ISSUE)
    assert env["AO_BRANCH"] == "issue-263"
    assert env["AO_AGENT_ID"] == AGENT
    assert env["GIT_AUTHOR_EMAIL"] == identity.author_email
    assert env["GIT_COMMITTER_EMAIL"] == identity.author_email
    assert "export AO_SESSION_ID=" in identity.shell_env()
    assert "export GIT_AUTHOR_EMAIL=" in identity.shell_env()


def test_trailer_points_at_the_ticket():
    assert commit_trailer(ISSUE) == "Refs kushin77/agent-orchestrator#263"
    assert mint(ISSUE, AGENT, LANE).trailer == commit_trailer(ISSUE)


def test_worktree_name_is_issue_scoped_and_unique_per_session():
    name = worktree_name_for(ISSUE, session_id_for(ISSUE, AGENT, LANE))
    assert name.startswith("ao-263-")
    other = worktree_name_for(ISSUE, session_id_for(ISSUE, "qa-sme", LANE))
    assert name != other


@pytest.mark.parametrize("agent", ["Human Person", "human@example.com", "../escape", "agent;rm -rf", "AGENT"])
def test_unsafe_or_human_agent_ids_are_refused(agent):
    with pytest.raises(IdentityRefused):
        mint(ISSUE, agent, LANE)


@pytest.mark.parametrize("issue", [0, -1, True, "263"])
def test_a_non_positive_issue_is_refused(issue):
    with pytest.raises(IdentityRefused):
        mint(issue, AGENT, LANE)  # type: ignore[arg-type]


@pytest.mark.parametrize("suffix", ["bad/slash", "UPPER", "-leading", "a b"])
def test_unsafe_branch_suffixes_are_refused(suffix):
    with pytest.raises(IdentityRefused):
        branch_for(ISSUE, suffix)


def test_identity_round_trips_through_json():
    identity = mint(ISSUE, AGENT, LANE, worktree_root="/tmp/lanes")
    from governance.isolation.identity import SessionIdentity

    assert SessionIdentity.from_json(identity.to_json()) == identity
