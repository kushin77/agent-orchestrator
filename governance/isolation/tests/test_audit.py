"""The audit: every isolation property is re-derived and each one can fail.

A gate that cannot fail is a formality, so each property here is broken on
purpose and the audit is required to name it. The trailer rule is additionally
shown to be historical: a later good commit does not repair an earlier commit
that never referenced the ticket, because the point of the rule is the history.
It is also shown to be *positional*: a reference in the subject line, or in a
body paragraph, is a mention rather than a trailer (issue #287).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from governance.isolation import cli, trailer
from governance.isolation.audit import audit_all, audit_lane
from governance.isolation.identity import GIT_IDENTITY_VARS, SessionIdentity, mint
from governance.isolation.worktree import (
    git,
    provision,
    read_record,
    shared_identity,
    stamp_identity,
    write_record,
)

import importlib.util as _importlib_util  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_isolation_tests_conftest", Path(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
commit = _conftest.commit


def git_as(identity: SessionIdentity, cwd: Path | str, *args: str) -> subprocess.CompletedProcess:
    """Run git signing as *this lane*, never as the shell that started pytest.

    ``worktree.git`` deliberately inherits its caller's environment, which is
    right for provisioning and wrong for a commit: AGENTS.md rule 15 exports the
    four identity variables into every agent shell, and they outrank ``git
    config --worktree`` (measured for #934 — and they beat a per-commit ``-c
    user.email=`` too, so pinning the environment is the only form that wins).

    Inheriting them does not merely fail an assertion: ``audit_lane`` selects a
    lane's commits *by author address*, so a commit signed by the shell's
    session vanishes from the rule being tested and the assertion above it
    passes vacuously. Setting the four variables for this one process replaces
    the caller's pair rather than losing to it (issue #1404).
    """
    signature = {name: identity.env()[name] for name in GIT_IDENTITY_VARS}
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **signature, "GIT_CONFIG_GLOBAL": os.devnull},
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


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


def test_a_ref_in_the_subject_only_is_a_violation(lane, repo: Path):
    """The rule is position, not presence (issue #287, measured on `6d89618`).

    A substring test accepts this commit: the reference is in the message. The
    rule does not — a subject is not a trailer block.
    """
    commit(lane.worktree, "work.txt", f"{lane.trailer}: do the work")
    problems = audit_lane(lane, repo)
    assert "commit-missing-ticket-trailer" in codes(problems)
    # The violation quotes the shared predicate's own naming for the defect.
    assert "commit-ref-only-in-subject" in str(problems)


def test_a_ref_inside_a_body_paragraph_is_a_violation(lane, repo: Path):
    """A mention woven into prose is not a trailer (issue #287)."""
    commit(lane.worktree, "work.txt", f"do the work\n\nThe change is tracked as {lane.trailer} in prose.")
    problems = audit_lane(lane, repo)
    assert "commit-missing-ticket-trailer" in codes(problems)
    assert "commit-ref-outside-the-trailer-block" in str(problems)


def test_a_ref_paragraph_that_is_not_trailing_is_a_violation(lane, repo: Path):
    """The block must be *trailing*: prose after the ref ends it."""
    commit(lane.worktree, "work.txt", f"do the work\n\n{lane.trailer}\n\nNotes for the reviewer below.")
    problems = audit_lane(lane, repo)
    assert "commit-missing-ticket-trailer" in codes(problems)
    assert "commit-ref-outside-the-trailer-block" in str(problems)


def test_a_trailer_followed_by_another_trailer_is_accepted(lane, repo: Path):
    """The shape of the repository's own exemplary commit `8b97ab6`."""
    (lane.worktree / "work.txt").write_text("work\n", encoding="utf-8")
    git_as(lane, lane.worktree, "add", "work.txt")
    git_as(
        lane,
        lane.worktree,
        "commit",
        "-q",
        "-m",
        "do the work",
        "-m",
        lane.trailer,
        "-m",
        "Co-authored-by: gate <gate@example.invalid>",
    )
    assert audit_lane(lane, repo) == []


def test_a_merge_commit_is_exempt_from_the_trailer_rule(lane, repo: Path):
    """A merge message is generated by git, not authored by the session.

    The shared predicate audits `--no-merges` for the same reason, so this is
    consistency with the one implementation rather than a second, weaker rule: a
    lane that merges `origin/master` must not fail a rule its own history cannot
    satisfy.
    """
    git_as(lane, lane.worktree, "checkout", "-q", "-b", "side")
    commit(lane.worktree, "side.txt", "side work", trailer=lane.trailer)
    git_as(lane, lane.worktree, "checkout", "-q", lane.branch)
    git_as(lane, lane.worktree, "merge", "-q", "--no-ff", "-m", "Merge side into the lane", "side")
    merge_author = git_as(lane, lane.worktree, "log", "-1", "--format=%ae").stdout.strip()
    assert merge_author == lane.author_email, "the merge must be authored by the session for this to prove anything"
    assert audit_lane(lane, repo) == []


def test_the_lane_signs_its_own_commits_even_when_the_shell_carries_another_pair(lane, repo: Path, monkeypatch):
    """The environment is an input, not a property of whoever ran pytest (#1404).

    Rule 15 exports the four identity variables into every agent shell, so a
    commit made through the inheriting helper is signed by the *shell's* session
    — and the audit attributes commits by author address, so it is not the
    assertion that breaks but the measurement: the commit disappears from the
    rule and the lane audits squeaky clean.

    Provoking the pair IN the test is what makes this property falsifiable on a
    machine that exports nothing at all, which is why CI never saw the defect.
    """
    ambient_name = "agent-mechanical-sme"
    ambient_email = f"agent+{ambient_name}@agents.invalid"
    for name in GIT_IDENTITY_VARS:
        monkeypatch.setenv(name, ambient_email if name.endswith("_EMAIL") else ambient_name)

    (lane.worktree / "work.txt").write_text("work\n", encoding="utf-8")
    git_as(lane, lane.worktree, "add", "work.txt")
    git_as(lane, lane.worktree, "commit", "-q", "-m", "do the work", "-m", lane.trailer)
    signed = git_as(lane, lane.worktree, "log", "-1", "--format=%ae").stdout.strip()
    assert signed == lane.author_email, "the caller's shell signed the lane's commit"
    assert audit_lane(lane, repo) == []

    # The half a foreign signature hides: an untrailed commit the rule must still
    # be able to SEE. Signed by another session, it is invisible and audits clean.
    (lane.worktree / "untrailed.txt").write_text("untrailed\n", encoding="utf-8")
    git_as(lane, lane.worktree, "add", "untrailed.txt")
    git_as(lane, lane.worktree, "commit", "-q", "-m", "no ticket reference here")
    assert "commit-missing-ticket-trailer" in codes(audit_lane(lane, repo))


def test_an_unassessable_history_rule_is_a_violation_not_a_pass(lane, repo: Path, monkeypatch):
    """No false green: if the shared predicate cannot run, the lane is not isolated."""
    commit(lane.worktree, "work.txt", "do the work", trailer=lane.trailer)
    monkeypatch.setattr(trailer, "PREDICATE_SCRIPT", Path("/nonexistent/check-pr-contract.sh"))
    assert "commit-trailer-check-unavailable" in codes(audit_lane(lane, repo))


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


def test_audit_all_covers_every_recorded_lane(repo: Path, tmp_path: Path, mounts: Path):
    first = mint(263, "copilot-brain", "governance-isolation", worktree_root=tmp_path / "lanes")
    second = mint(264, "qa-sme", "verification", worktree_root=tmp_path / "lanes")
    for identity in (first, second):
        provision(identity, repo, base="HEAD", mounts=mounts)
        write_record(identity, repo)
    commit(second.worktree, "work.txt", "no ticket reference here")

    results = audit_all(repo)
    assert set(results) == {first.session_id, second.session_id}
    assert results[first.session_id] == []
    assert "commit-missing-ticket-trailer" in codes(results[second.session_id])
    assert read_record(second.session_id, repo) == second


def test_an_audit_that_assessed_no_lane_is_cannot_assess(tmp_path: Path, capsys):
    """No lane records is CANNOT-ASSESS, never OK (issue #287).

    An audit with nothing to re-derive used to print `OK (no lanes provisioned)`
    and exit 0 — the same answer as a machine where lanes were never provisioned,
    which is the failure the rule exists to catch. A green that was never measured
    is the false-green class the whole doctrine rejects, so the verdict is that the
    rule could not be assessed at all.
    """
    empty = tmp_path / "no-lanes"
    empty.mkdir()
    rc = cli.main(["audit", "--main", str(empty)])
    captured = capsys.readouterr()
    assert rc == 2, "an empty audit must not be a pass"
    assert "CANNOT-ASSESS" in captured.err
    assert "no lane records" in captured.err
    assert "OK" not in captured.out, "an empty audit printed OK"
