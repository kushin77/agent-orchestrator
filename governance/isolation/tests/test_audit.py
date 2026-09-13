"""The audit: every isolation property is re-derived and each one can fail.

A gate that cannot fail is a formality, so each property here is broken on
purpose and the audit is required to name it. The trailer rule is additionally
shown to be historical: a later good commit does not repair an earlier commit
that never referenced the ticket, because the point of the rule is the history.
"""

from __future__ import annotations

from pathlib import Path

from governance.isolation.audit import audit_all, audit_lane
from governance.isolation.identity import SessionIdentity, mint
from governance.isolation.worktree import (
    git,
    provision,
    read_record,
    shared_identity,
    stamp_identity,
    write_record,
)

from conftest import commit  # noqa: E402  (suite-local helper; conftest bootstraps sys.path)


def codes(problems) -> set[str]:
    return {problem.code for problem in problems}


def test_a_clean_lane_is_isolated(lane, repo: Path):
    assert audit_lane(lane, repo) == []


def test_a_commit_without_the_ticket_trailer_is_a_violation(lane, repo: Path):
    commit(lane.worktree, "work.txt", "implement something")
    assert "commit-missing-ticket-trailer" in codes(audit_lane(lane, repo))


def test_a_commit_with_the_ticket_trailer_is_accepted(lane, repo: Path):
    commit(lane.worktree, "work.txt", "implement something", trailer=lane.trailer)
    assert audit_lane(lane, repo) == []


def test_a_later_commit_does_not_repair_an_earlier_untraceable_one(lane, repo: Path):
    commit(lane.worktree, "first.txt", "first, no ticket ref")
    commit(lane.worktree, "second.txt", "second, properly referenced", trailer=lane.trailer)
    problems = audit_lane(lane, repo)
    assert "commit-missing-ticket-trailer" in codes(problems)
    assert "1 commit(s)" in str(problems[0])


def test_only_this_sessions_commits_are_required_to_carry_the_trailer(lane, repo: Path):
    """A base commit authored by the human must not be blamed on the lane."""
    commit(lane.worktree, "work.txt", "do the work", trailer=lane.trailer)
    assert audit_lane(lane, repo) == []


def test_a_lane_on_the_wrong_branch_is_a_violation(lane, repo: Path):
    git(lane.worktree, "checkout", "-q", "-b", "somewhere-else")
    assert "branch-mismatch" in codes(audit_lane(lane, repo))


def test_a_branch_that_names_no_issue_is_a_violation(lane, repo: Path):
    broken = SessionIdentity(
        session_id=lane.session_id,
        issue=lane.issue,
        agent_id=lane.agent_id,
        lane=lane.lane,
        branch="work-not-a-ticket",
        worktree=lane.worktree,
    )
    assert "branch-does-not-name-issue" in codes(audit_lane(broken, repo))


def test_another_sessions_signature_in_the_lane_is_a_violation(lane, repo: Path):
    other = mint(263, "qa-sme", lane.lane, worktree_root=lane.worktree.parent)
    stamp_identity(lane.worktree, other)
    assert "identity-mismatch" in codes(audit_lane(lane, repo))


def test_identity_leaked_into_the_shared_config_is_a_violation(lane, repo: Path):
    """If the lane's signature reaches the shared config, every lane inherits it."""
    git(repo, "config", "--local", "user.email", lane.author_email)
    git(repo, "config", "--local", "user.name", lane.author_name)
    assert shared_identity(repo) == (lane.author_name, lane.author_email)
    assert "identity-leaked-to-shared-config" in codes(audit_lane(lane, repo))


def test_a_missing_worktree_is_a_violation(lane, repo: Path):
    git(repo, "worktree", "remove", "--force", str(lane.worktree))
    assert "worktree-missing" in codes(audit_lane(lane, repo))


def test_audit_all_covers_every_recorded_lane(repo: Path, tmp_path: Path):
    first = mint(263, "copilot-brain", "governance-isolation", worktree_root=tmp_path / "lanes")
    second = mint(264, "qa-sme", "verification", worktree_root=tmp_path / "lanes")
    for identity in (first, second):
        provision(identity, repo, base="HEAD")
        write_record(identity, repo)
    commit(second.worktree, "work.txt", "no ticket reference here")

    results = audit_all(repo)
    assert set(results) == {first.session_id, second.session_id}
    assert results[first.session_id] == []
    assert "commit-missing-ticket-trailer" in codes(results[second.session_id])
    assert read_record(second.session_id, repo) == second
