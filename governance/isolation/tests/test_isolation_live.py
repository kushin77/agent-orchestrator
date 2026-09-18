"""The live projection reflects real worktrees/git, not just the recorded lane JSON (issue #885)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from governance.isolation import live, speculative  # noqa: E402
from governance.isolation.identity import mint  # noqa: E402
from governance.isolation.worktree import provision, write_record  # noqa: E402


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    assert result.returncode == 0, result.stderr
    return result


@pytest.fixture
def main_repo(tmp_path: Path) -> Path:
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "master")
    _git(main, "config", "user.email", "owner@example.com")
    _git(main, "config", "user.name", "Owner")
    (main / "README.md").write_text("root\n", encoding="utf-8")
    _git(main, "add", "README.md")
    _git(main, "commit", "-q", "-m", "root")
    return main


def test_project_with_no_records_is_empty(main_repo, tmp_path):
    assert live.project(main_repo) == []
    assert live.render(main_repo) == ""


def test_project_reports_clean_lane(main_repo, tmp_path):
    identity = mint(11, "live-agent", worktree_root=tmp_path / "worktrees")
    result = provision(identity, main_repo, base="master")
    assert result.ok, result.problems
    write_record(identity, main_repo)

    projected = live.project(main_repo)
    assert len(projected) == 1
    lane = projected[0]
    assert lane.worktree_exists is True
    assert lane.branch_drift is False
    assert lane.speculative_drift == ""  # no claim made


def test_project_detects_branch_drift_when_worktree_is_switched(main_repo, tmp_path):
    identity = mint(12, "live-agent", worktree_root=tmp_path / "worktrees")
    provision(identity, main_repo, base="master")
    write_record(identity, main_repo)

    _git(main_repo, "branch", "some-other-branch")
    _git(identity.worktree, "checkout", "-q", "some-other-branch")

    projected = live.project(main_repo)
    lane = projected[0]
    assert lane.branch_drift is True
    assert "DRIFT" in live.render(main_repo)


def test_project_detects_stale_speculative_attestation(main_repo, tmp_path):
    identity = mint(13, "live-agent", worktree_root=tmp_path / "worktrees")
    provision(identity, main_repo, base="master")
    write_record(identity, main_repo)

    speculative.claim(main_repo, identity, upstream_branch="master", base="master")

    # Move the lane branch forward without re-verifying: the attestation's
    # recorded git_sha is now stale against the real worktree's HEAD.
    (identity.worktree / "extra.txt").write_text("more\n", encoding="utf-8")
    _git(identity.worktree, "add", "extra.txt")
    _git(identity.worktree, "commit", "-q", "-m", "extra commit")

    projected = live.project(main_repo)
    lane = projected[0]
    assert lane.speculative_drift == "attestation-git-sha-stale"
    assert "speculative attestation git_sha is stale" in live.render(main_repo)
