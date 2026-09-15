"""The trailer adapter, and the landed-history surface it gives the audit.

Two things are pinned here, and they are the two halves of issue #289's brief:

* the lane audit asks the **one** implementation of the trailing-trailer rule
  (the predicate in ``scripts/check-pr-contract.sh``, issue #288) rather than
  restating it — ``governance/isolation/trailer.py`` is an adapter, so the audit
  and the PR gate cannot drift apart;
* the rule can be pointed at **real landed history** — ``cli.py landed``
  classifies a commit that has already landed, which is the coverage that was
  missing (the gate only ever provisioned lanes in a scratch repository).

The four real commits the #287/#288 table names are re-checked here when this
clone has them; a shallow clone skips rather than passing vacuously.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from governance.isolation import cli, trailer
from governance.isolation.trailer import (
    PredicateUnavailable,
    classify_commit,
    named_findings,
    run_landed,
)

from conftest import commit, git  # noqa: E402  (suite-local helper; conftest bootstraps sys.path)


def head(path: Path) -> str:
    return git(path, "rev-parse", "HEAD").stdout.strip()


# --- the predicate is shared, not restated ----------------------------------


def test_the_predicate_lives_in_the_pr_contract_gate_and_nowhere_else():
    assert trailer.PREDICATE_SCRIPT == trailer.REPO_ROOT / "scripts" / "check-pr-contract.sh"
    assert trailer.PREDICATE_SCRIPT.is_file()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("do the work", "commit-missing-ticket-trailer"),
        ("Refs kushin77/agent-orchestrator#263: do the work", "commit-ref-only-in-subject"),
        ("do the work\n\nTracked as Refs kushin77/agent-orchestrator#263 in prose.", "commit-ref-outside-the-trailer-block"),
    ],
)
def test_the_shared_predicate_classifies_position(lane, repo: Path, message: str, expected: str):
    commit(lane.worktree, "work.txt", message)
    assert classify_commit(lane.worktree, head(lane.worktree)) == expected


def test_a_real_trailing_trailer_passes_the_shared_predicate(lane, repo: Path):
    commit(lane.worktree, "work.txt", "do the work", trailer=lane.trailer)
    assert classify_commit(lane.worktree, head(lane.worktree)) is None


def test_an_unrunnable_predicate_raises_rather_than_passing(lane, repo: Path, monkeypatch):
    commit(lane.worktree, "work.txt", "do the work", trailer=lane.trailer)
    monkeypatch.setattr(trailer, "PREDICATE_SCRIPT", Path("/nonexistent/check-pr-contract.sh"))
    with pytest.raises(PredicateUnavailable):
        classify_commit(lane.worktree, head(lane.worktree))


def test_a_predicate_that_fails_without_naming_a_finding_is_not_clean(lane, repo: Path, monkeypatch):
    """Unproven is not clean: a non-zero verdict this adapter cannot attribute
    must raise, never report the commit as compliant (measured by mutation while
    proving scripts/check-isolation-landed.sh, issue #287)."""
    commit(lane.worktree, "work.txt", "do the work", trailer=lane.trailer)
    monkeypatch.setattr(
        trailer, "_run", lambda *_a, **_k: subprocess.CompletedProcess([], 1, "", "the output shape changed\n")
    )
    with pytest.raises(PredicateUnavailable):
        classify_commit(lane.worktree, head(lane.worktree))


def test_the_boundary_finding_is_not_attributed_to_the_commit_under_test(lane, repo: Path):
    """The enforcement boundary is the commit's parent; its findings are its own."""
    commit(lane.worktree, "work.txt", "do the work", trailer=lane.trailer)
    sha = head(lane.worktree)
    result = run_landed(lane.worktree, f"{sha}^..{sha}", f"{sha}^")
    # The seed commit (the boundary here) has no trailer, so the shared gate
    # reports a boundary finding — and the commit under test is still clean.
    assert "enforcement-gate-missing-trailer" in named_findings(result.output)
    assert named_findings(result.output, sha) == []


# --- landed history, the coverage that was missing ---------------------------


def test_the_landed_mode_names_a_bad_landed_commit(lane, repo: Path, capsys):
    commit(lane.worktree, "work.txt", f"{lane.trailer}: landed with the ref in the subject")
    sha = head(lane.worktree)
    rc = cli.main(["landed", "--main", str(lane.worktree), "--commit", sha])
    captured = capsys.readouterr()
    assert rc == 1
    assert "commit-ref-only-in-subject" in captured.err
    assert sha[:12] in captured.err


def test_the_landed_mode_passes_a_well_trailed_landed_commit(lane, repo: Path, capsys):
    commit(lane.worktree, "work.txt", "do the work", trailer=lane.trailer)
    sha = head(lane.worktree)
    rc = cli.main(["landed", "--main", str(lane.worktree), "--commit", sha])
    assert rc == 0
    assert "OK" in capsys.readouterr().out


def test_the_landed_mode_delegates_a_range_to_the_shared_gate(lane, repo: Path, capsys):
    commit(lane.worktree, "work.txt", "no ticket reference at all")
    sha = head(lane.worktree)
    rc = cli.main(["landed", "--main", str(lane.worktree), "--range", f"{sha}^..{sha}", "--gate", f"{sha}^"])
    captured = capsys.readouterr()
    assert rc == 1
    # The shared gate's own verdict, verbatim — not this module's paraphrase.
    assert "check-pr-contract: LANDED" in captured.err
    assert f"commit-missing-ticket-trailer:{sha[:12]}" in captured.err


def test_an_unresolvable_commit_is_cannot_assess(repo: Path, capsys):
    assert cli.main(["landed", "--main", str(repo), "--commit", "0" * 40]) == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


# --- the real commits the issue names (skipped when history is unavailable) --


REAL_COMMITS = (
    ("8b97ab6", None),
    ("6d89618", "commit-ref-only-in-subject"),
    ("576edce", "commit-ref-only-in-subject"),
    ("061690c", "commit-missing-ticket-trailer"),
)


@pytest.mark.parametrize(("sha", "expected"), REAL_COMMITS)
def test_the_real_commits_the_issue_names(sha: str, expected: str | None):
    repo = trailer.REPO_ROOT
    if subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True).returncode != 0:
        pytest.skip(f"{sha} is not in this clone (shallow?), so it cannot be re-checked here")
    assert classify_commit(repo, sha) == expected
