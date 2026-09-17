"""Tests for the real-tree baseline (#740, item 1).

`scripts/check-reconcile.sh` proves the audit only against a synthetic fixture
repo; it never once points `audit()` at the real repository it governs. These
tests exercise the baseline mechanism itself (new violation / stale entry /
clean match) against a small real scratch repo, independent of the shell gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from governance.reconcile.real_tree_baseline import (  # noqa: E402
    BaselineUnavailable,
    check_real_tree,
    load_baseline,
)
from governance.reconcile.sweep import RepoOps  # noqa: E402


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
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "branch", "issue-orphan-1")
    _git(repo, "branch", "issue-orphan-2")
    return repo


def _write_baseline(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps({"note": "test", "entries": entries}), encoding="utf-8")


def test_matching_baseline_is_ok(scratch_repo: Path, tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    _write_baseline(
        baseline,
        [
            {"kind": "branch", "name": "issue-orphan-1", "reason": "known, unresolved"},
            {"kind": "branch", "name": "issue-orphan-2", "reason": "known, unresolved"},
        ],
    )
    verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo))
    assert verdict.assessable
    assert verdict.ok
    assert verdict.new_violations == ()
    assert verdict.stale_entries == ()


def test_new_unbaselined_artifact_fails(scratch_repo: Path, tmp_path: Path):
    """A NEW unmatched artifact absent from the baseline is a violation, named."""
    baseline = tmp_path / "baseline.json"
    _write_baseline(
        baseline,
        [{"kind": "branch", "name": "issue-orphan-1", "reason": "known, unresolved"}],
    )
    verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo))
    assert verdict.assessable
    assert not verdict.ok
    names = {(e.kind, e.name) for e in verdict.new_violations}
    assert ("branch", "issue-orphan-2") in names
    assert verdict.stale_entries == ()


def test_stale_entry_fails(scratch_repo: Path, tmp_path: Path):
    """A baselined artifact that is no longer unmatched must be removed, not kept."""
    baseline = tmp_path / "baseline.json"
    _write_baseline(
        baseline,
        [
            {"kind": "branch", "name": "issue-orphan-1", "reason": "known, unresolved"},
            {"kind": "branch", "name": "issue-orphan-2", "reason": "known, unresolved"},
            {"kind": "branch", "name": "issue-does-not-exist", "reason": "stale by construction"},
        ],
    )
    verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo))
    assert verdict.assessable
    assert not verdict.ok
    assert verdict.new_violations == ()
    stale_names = {(e.kind, e.name) for e in verdict.stale_entries}
    assert ("branch", "issue-does-not-exist") in stale_names


def test_malformed_baseline_is_cannot_assess(scratch_repo: Path, tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{ not json", encoding="utf-8")
    verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo))
    assert not verdict.assessable
    assert not verdict.ok


def test_load_baseline_rejects_missing_fields(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"entries": [{"kind": "branch"}]}), encoding="utf-8")
    with pytest.raises(BaselineUnavailable):
        load_baseline(baseline)


def test_the_real_repo_baseline_file_matches_the_real_tree():
    """The committed baseline for THIS repository must stay in sync with
    reality: a stale entry or a new unbaselined artifact must fail here too,
    not only in the shell gate. This is the actual #740 acceptance check —
    the audit is pointed at the real repo_root, not a fixture."""
    baseline_path = REPO_ROOT / "governance" / "reconcile" / "real-tree-baseline.json"
    assert baseline_path.exists(), "governance/reconcile/real-tree-baseline.json must exist (#740)"
    verdict = check_real_tree(REPO_ROOT, baseline_path, ops=RepoOps(REPO_ROOT))
    assert verdict.assessable, verdict.reason
    if not verdict.ok:
        pytest.fail(
            "real-tree-baseline drifted from disk — new: "
            f"{[e.name for e in verdict.new_violations]}, stale: "
            f"{[e.name for e in verdict.stale_entries]}. Update "
            "governance/reconcile/real-tree-baseline.json with an explicit, "
            "reviewed edit."
        )
