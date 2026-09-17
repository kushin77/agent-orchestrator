"""Tests for the content-equivalence stranding predicate (#740).

Ancestry alone (`git merge-base --is-ancestor`) reports NOT-merged for a
squash-landed lane, because the lane's original commit is never reachable from
the target after a squash/replay. These tests build a real scratch repository
(no network) and prove the predicate answers the actual question: is the
lane's content genuinely absent from the target?
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from governance.reconcile.equivalence import content_equivalent  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


@pytest.fixture()
def scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "scratch"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "gate@example.com")
    _git(repo, "config", "user.name", "Gate")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    (repo / "Makefile").write_text("all:\n\techo base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def test_ancestor_lane_is_not_stranded(scratch_repo: Path):
    """The trivial case: ancestry already holds."""
    _git(scratch_repo, "checkout", "-q", "-b", "issue-1")
    (scratch_repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(scratch_repo, "add", "feature.txt")
    _git(scratch_repo, "commit", "-q", "-m", "feature")
    _git(scratch_repo, "checkout", "-q", "master")
    _git(scratch_repo, "merge", "-q", "issue-1")

    report = content_equivalent(scratch_repo, "issue-1", "master")
    assert report.stranded is False
    assert "ancestry holds" in report.reason


def test_squash_landed_lane_is_not_stranded(scratch_repo: Path):
    """The #740 counter-example: ancestry fails, content is identical."""
    _git(scratch_repo, "checkout", "-q", "-b", "issue-650-erp-crm")
    (scratch_repo / "integrations").mkdir()
    (scratch_repo / "integrations" / "crm.py").write_text("crm = 1\n", encoding="utf-8")
    _git(scratch_repo, "add", "integrations/crm.py")
    _git(scratch_repo, "commit", "-q", "-m", "add erp crm")
    lane_head = _git(scratch_repo, "rev-parse", "issue-650-erp-crm").strip()

    # master advances independently (a later PR touches unrelated files), then
    # squash-lands the SAME content the lane produced, under a NEW commit —
    # the lane's original commit is never reachable from master afterwards.
    _git(scratch_repo, "checkout", "-q", "master")
    (scratch_repo / "Makefile").write_text("all:\n\techo advanced\n", encoding="utf-8")
    _git(scratch_repo, "commit", "-q", "-am", "advance Makefile")
    (scratch_repo / "integrations").mkdir()
    (scratch_repo / "integrations" / "crm.py").write_text("crm = 1\n", encoding="utf-8")
    _git(scratch_repo, "add", "integrations/crm.py")
    _git(scratch_repo, "commit", "-q", "-m", "squash-land erp crm (#650)")

    assert (
        subprocess.run(
            ["git", "-C", str(scratch_repo), "merge-base", "--is-ancestor", lane_head, "master"],
            capture_output=True,
        ).returncode
        != 0
    ), "fixture invariant: ancestry must actually fail here"

    report = content_equivalent(scratch_repo, "issue-650-erp-crm", "master")
    assert report.stranded is False
    assert "landed-equivalent" in report.reason
    assert report.differing_paths == ()


def test_lane_with_unlanded_commit_is_stranded(scratch_repo: Path):
    """A lane whose content genuinely never reached the target IS stranded."""
    _git(scratch_repo, "checkout", "-q", "-b", "issue-999-never-landed")
    (scratch_repo / "only_here.txt").write_text("still stranded\n", encoding="utf-8")
    _git(scratch_repo, "add", "only_here.txt")
    _git(scratch_repo, "commit", "-q", "-m", "work nobody landed")

    _git(scratch_repo, "checkout", "-q", "master")
    (scratch_repo / "Makefile").write_text("all:\n\techo advanced\n", encoding="utf-8")
    _git(scratch_repo, "commit", "-q", "-am", "advance Makefile, unrelated to the lane")

    report = content_equivalent(scratch_repo, "issue-999-never-landed", "master")
    assert report.stranded is True
    assert "only_here.txt" in report.differing_paths
    assert "genuinely unlanded" in report.reason


def test_partial_overlap_still_counts_as_stranded(scratch_repo: Path):
    """One landed file and one unlanded file: still stranded, named by path."""
    _git(scratch_repo, "checkout", "-q", "-b", "issue-777-partial")
    (scratch_repo / "landed.txt").write_text("shared\n", encoding="utf-8")
    (scratch_repo / "not_landed.txt").write_text("only on the lane\n", encoding="utf-8")
    _git(scratch_repo, "add", "landed.txt", "not_landed.txt")
    _git(scratch_repo, "commit", "-q", "-m", "two files, one will land")

    _git(scratch_repo, "checkout", "-q", "master")
    (scratch_repo / "landed.txt").write_text("shared\n", encoding="utf-8")
    _git(scratch_repo, "add", "landed.txt")
    _git(scratch_repo, "commit", "-q", "-m", "land only landed.txt")

    report = content_equivalent(scratch_repo, "issue-777-partial", "master")
    assert report.stranded is True
    assert report.differing_paths == ("not_landed.txt",)
    assert "landed.txt" in report.touched_paths
