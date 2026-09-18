"""The landing proof (#1291): the work, not the name.

The defect these tests are written against is a *false finding*: at
``origin/master 99f6b37`` (2026-09-18) 25 of 35 real-tree findings were lane
branches and worktrees whose work **was already on the default branch**, reported
as orphans only because the audit's one landing source was the gitignored
close-out journal. Each false finding red the composite gate, and on this box that
gate serializes the whole fleet (16 open pull requests at the time).

`governance/reconcile/landing.py` answers the same question from the default
branch's **tracked** history, three ways — ancestry, tree containment, patch
identity — and every one of them is an *excuse*, so the tests that matter most are
the negative halves: a branch whose work is not on the default branch must stay
unexplained whatever it is called, and a worktree whose HEAD landed but which
holds uncommitted work must stay unexplained too, because the proof speaks only
about committed work.

The scratch repo here is real git, because the mechanism *is* git.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from governance.reconcile.landing import (  # noqa: E402
    BRANCH,
    WORKTREE,
    LandingUnavailable,
    RepoLanding,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


def _push(repo: Path) -> None:
    """Land the local default branch on the remote the prover reads."""
    _git(repo, "push", "-q", "origin", "master")


def _commit(repo: Path, message: str, files: dict[str, str], *, push: bool = True) -> str:
    for name, body in files.items():
        (repo / name).write_text(body, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)
    if push:
        # The prover reads the REMOTE default branch (rule 22: never the local
        # checkout, which may itself be the stale side), so a landed commit has
        # to actually be pushed for a squash landing to be observable.
        _git(repo, "push", "-q", "origin", "master")
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A clone whose `origin/master` is a real default branch."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "-q", "--bare", "-b", "master")
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "master")
    _git(work, "config", "user.email", "gate@example.com")
    _git(work, "config", "user.name", "Gate")
    _commit(work, "base", {"README.md": "base\n"}, push=False)
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "origin", "master")
    return work


def test_a_merged_tip_is_proven_by_ancestry(repo: Path):
    _git(repo, "branch", "issue-1-merged")
    proof = RepoLanding(repo).proof(BRANCH, "issue-1-merged", "issue-1-merged")
    assert proof.startswith("landed:ancestor:")


def test_a_squash_merged_branch_is_proven_by_tree_containment(repo: Path):
    """The shape a squash merge leaves: the tip is an ancestor of nothing."""
    _git(repo, "checkout", "-q", "-b", "issue-2")
    lane_tip = _commit(repo, "feat: lane work", {"lane.txt": "lane\n"})
    _git(repo, "checkout", "-q", "master")
    # The squash: the same tree content, landed as a NEW commit with the fleet's
    # own subject convention — the branch tip is NOT an ancestor of it.
    (repo / "lane.txt").write_text("lane\n", encoding="utf-8")
    _git(repo, "add", "lane.txt")
    _git(repo, "commit", "-q", "-m", "feat: lane work (#2) (#99)")
    _push(repo)
    # The precondition the whole case rests on: the tip is an ancestor of nothing.
    result = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", lane_tip, "origin/master"]
    )
    assert result.returncode != 0, "a squash-landed tip must NOT be an ancestor"
    proof = RepoLanding(repo).proof(BRANCH, "issue-2", "issue-2")
    assert proof.startswith("landed:tree-contained:")


def test_a_squash_merge_whose_files_later_changed_is_proven_by_patch_identity(repo: Path):
    """Later sibling edits must not resurrect a landed lane as a finding."""
    _git(repo, "checkout", "-q", "-b", "issue-3")
    _commit(repo, "fix: lane", {"shared.txt": "one\n"})
    _git(repo, "checkout", "-q", "master")
    (repo / "shared.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-q", "-m", "fix: lane (#3) (#98)")
    _push(repo)
    # A later sibling edits the same file: containment now fails, the patch does not.
    (repo / "shared.txt").write_text("one\ntwo\n", encoding="utf-8")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-q", "-m", "chore: sibling edit")
    _push(repo)
    proof = RepoLanding(repo).proof(BRANCH, "issue-3", "issue-3")
    assert proof.startswith("landed:patch-identity:"), proof


def test_a_branch_whose_work_is_not_on_the_default_branch_is_never_proven(repo: Path):
    """The negative half the whole change rests on: no wildcard, whatever the name."""
    _git(repo, "checkout", "-q", "-b", "issue-4-only-here")
    _commit(repo, "feat: work that exists nowhere else", {"orphan.txt": "orphan\n"})
    _git(repo, "checkout", "-q", "master")
    # It is even named in the default branch's history — and still unexplained.
    (repo / "note.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "note.txt")
    _git(repo, "commit", "-q", "-m", "docs: mention #4 (#4) (#97)")
    assert RepoLanding(repo).proof(BRANCH, "issue-4-only-here", "issue-4-only-here") == ""


def test_a_dirty_worktree_on_a_landed_commit_is_not_explained(repo: Path):
    """The proof speaks about committed work; uncommitted work is on no branch."""
    _git(repo, "branch", "issue-5-merged")
    worktree = repo.parent / "wt-5"
    _git(repo, "worktree", "add", "-q", str(worktree), "issue-5-merged")
    landing = RepoLanding(repo)
    assert landing.proof(WORKTREE, str(worktree), "issue-5-merged").startswith("landed:")
    (worktree / "uncommitted.txt").write_text("not anywhere\n", encoding="utf-8")
    assert landing.proof(WORKTREE, str(worktree), "issue-5-merged") == ""


def test_a_missing_default_ref_grants_no_excuse(repo: Path):
    """No `origin/master` (a fixture root) means no landing evidence exists here."""
    landing = RepoLanding(repo, ref="origin/does-not-exist")
    assert landing.ref is None
    assert landing.proof(BRANCH, "issue-5-merged", "issue-5-merged") == ""


def test_a_directory_that_is_not_a_repository_is_cannot_assess(tmp_path: Path):
    """An unreadable git is CANNOT-ASSESS, never 'not landed'."""
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    with pytest.raises(LandingUnavailable):
        RepoLanding(plain)


def test_every_proof_names_how_it_was_proven_and_at_which_commit(repo: Path):
    _git(repo, "branch", "issue-6-merged")
    proof = RepoLanding(repo).proof(BRANCH, "issue-6-merged", "issue-6-merged")
    kind, how, sha = proof.split(":")
    assert (kind, how) == ("landed", "ancestor")
    assert len(sha) == 12 and _git(repo, "rev-parse", "HEAD").strip().startswith(sha)


# --- the landing convention names TWO numbers (#1310) ------------------------
#
# A squash landing carries the pull request in its *subject* (``… (#1165)``) and
# the issue it closes in its *body* (``Closes #1158``). A candidate generator
# that read only subjects therefore looked in an empty bucket whenever a lane's
# pull request number differed from its issue number — the normal case here —
# and reported landed work as work that exists nowhere. Measured at
# ``origin/master bbe3c83`` (2026-09-18): 5 of the 23 artifacts the real-tree
# quarantine was still excusing were exactly this false finding, and one of them
# crossed the age grace while the gate ran and red the whole fleet.


def test_a_landing_naming_its_pull_request_and_closing_its_issue_is_proven(repo: Path):
    """The issue number appears in no subject — the proof must still find it."""
    _git(repo, "checkout", "-q", "-b", "issue-1158")
    _commit(repo, "docs(pmo): the lane's work", {"audit.md": "audit\n"})
    _git(repo, "checkout", "-q", "master")
    (repo / "audit.md").write_text("audit\n", encoding="utf-8")
    _git(repo, "add", "audit.md")
    _git(repo, "commit", "-q", "-m", "docs(pmo): the lane's work (#1165)", "-m", "Closes #1158")
    _push(repo)
    # A later sibling edits the same file, so tree containment can no longer
    # prove it and patch identity is the only proof left — which is the half
    # this case is about.
    (repo / "audit.md").write_text("audit\nmore\n", encoding="utf-8")
    _git(repo, "add", "audit.md")
    _git(repo, "commit", "-q", "-m", "chore: sibling edit")
    _push(repo)
    # The precondition the case rests on: the issue number is in NO subject.
    assert "#1158" not in _git(repo, "log", "--format=%s", "origin/master")
    proof = RepoLanding(repo).proof(BRANCH, "issue-1158", "issue-1158")
    assert proof.startswith("landed:patch-identity:"), proof


def test_a_body_reference_is_never_an_excuse_on_its_own(repo: Path):
    """Widening the candidate search must not become a wildcard.

    A commit on the default branch *closes* this artifact's issue, so the issue
    is a real candidate — and the artifact is still unexplained, because the
    patch is what proves a landing, never the name.
    """
    _git(repo, "checkout", "-q", "-b", "issue-7-only-here")
    _commit(repo, "feat: work that exists nowhere else", {"orphan.txt": "orphan\n"})
    _git(repo, "checkout", "-q", "master")
    (repo / "note.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "note.txt")
    _git(repo, "commit", "-q", "-m", "docs: unrelated work (#97)", "-m", "Closes #7")
    _push(repo)
    # The candidate exists — and does not excuse the artifact.
    assert "#7" in _git(repo, "log", "--format=%b", "origin/master")
    assert RepoLanding(repo).proof(BRANCH, "issue-7-only-here", "issue-7-only-here") == ""


def test_a_closer_that_is_not_on_the_body_s_first_line_is_still_found(repo: Path):
    """A body carries embedded newlines; a line-oriented scan drops the closer.

    Candidate commits are read as RECORDS. Read as lines instead, a `Closes #n`
    sitting on any later body line is parsed as if it were a commit hash of its
    own and the candidate is silently lost — which is a false finding of exactly
    the kind this module exists to remove.
    """
    _git(repo, "checkout", "-q", "-b", "issue-1158")
    _commit(repo, "docs(pmo): the lane's work", {"audit.md": "audit\n"})
    _git(repo, "checkout", "-q", "master")
    (repo / "audit.md").write_text("audit\n", encoding="utf-8")
    _git(repo, "add", "audit.md")
    _git(
        repo,
        "commit",
        "-q",
        "-m",
        "docs(pmo): the lane's work (#1165)",
        "-m",
        "Reviewed-by: someone else",
        "-m",
        "Closes #1158",
    )
    _push(repo)
    (repo / "audit.md").write_text("audit\nmore\n", encoding="utf-8")
    _git(repo, "add", "audit.md")
    _git(repo, "commit", "-q", "-m", "chore: sibling edit")
    _push(repo)
    landing = _git(repo, "log", "--format=%H", "-1", "--grep=(#1165)", "origin/master").strip()
    body = _git(repo, "log", "-1", "--format=%b", landing)
    # The precondition: the closer is NOT on the body's first line.
    assert body.splitlines()[0] == "Reviewed-by: someone else"
    assert "Closes #1158" in body
    proof = RepoLanding(repo).proof(BRANCH, "issue-1158", "issue-1158")
    assert proof.startswith("landed:patch-identity:"), proof
