"""Lane worktrees accumulate; the pruner must reclaim only what is provably safe.

Issue: stale worktrees from finished lanes piled up (11 in the 16 GiB /tmp tmpfs
plus 8 disk-backed ones on 2026-09-13), and cleaning them was a manual operator
step. `scripts/prune-worktrees.sh` makes it code. These tests pin the safety
rules — it must never remove a worktree whose work is not preserved elsewhere.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "prune-worktrees.sh"


def run_script(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def make_repo(tmp_path: Path) -> Path:
    """A throwaway repo with an origin-like remote so 'preserved' can be tested."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-q", str(origin))
    repo = tmp_path / "repo"
    git(tmp_path, "init", "-q", str(repo))
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "seed")
    git(repo, "branch", "-M", "master")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-qu", "origin", "master")
    git(repo, "fetch", "-q", "origin")
    return repo


def test_a_stale_worktree_is_reported_and_check_fails(tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "worktree", "add", "-q", "-b", "issue-1-lane", str(tmp_path / "lane"))
    result = run_script(repo, "--check")
    assert result.returncode == 1
    assert "STALE" in result.stdout
    assert (tmp_path / "lane").exists(), "a dry run must not remove anything"


def test_apply_removes_a_preserved_clean_worktree(tmp_path):
    repo = make_repo(tmp_path)
    lane = tmp_path / "lane"
    git(repo, "worktree", "add", "-q", "-b", "issue-1-lane", str(lane))
    git(repo, "push", "-qu", "origin", "issue-1-lane")
    assert run_script(repo, "--apply").returncode == 0
    assert not lane.exists()
    result = run_script(repo, "--check")
    assert result.returncode == 0, "no stale worktrees should remain"


def test_a_worktree_with_uncommitted_work_is_kept(tmp_path):
    """Unmerged work belongs to its lane — never delete it."""
    repo = make_repo(tmp_path)
    lane = tmp_path / "lane"
    git(repo, "worktree", "add", "-q", "-b", "issue-2-lane", str(lane))
    (lane / "wip.txt").write_text("in progress\n", encoding="utf-8")
    result = run_script(repo, "--apply")
    assert lane.exists()
    assert "KEEP" in result.stdout and "uncommitted" in result.stdout


def test_a_worktree_whose_commit_is_unpreserved_is_kept(tmp_path):
    repo = make_repo(tmp_path)
    lane = tmp_path / "lane"
    git(repo, "worktree", "add", "-q", "-b", "issue-3-lane", str(lane))
    (lane / "work.txt").write_text("committed only here\n", encoding="utf-8")
    git(lane, "add", "-A")
    git(lane, "commit", "-qm", "unpushed lane work")
    result = run_script(repo, "--apply")
    assert lane.exists()
    assert "not preserved on origin" in result.stdout


def test_a_worktree_in_use_by_a_live_process_is_kept(tmp_path):
    """The loop runs subagents inside their worktree — that tree is off limits."""
    repo = make_repo(tmp_path)
    lane = tmp_path / "lane"
    git(repo, "worktree", "add", "-q", "-b", "issue-4-lane", str(lane))
    git(repo, "push", "-qu", "origin", "issue-4-lane")
    sleeper = subprocess.Popen(["bash", "-c", f"cd {lane} && sleep 5"])
    try:
        result = run_script(repo, "--apply")
        assert lane.exists()
        assert "in use by a live process" in result.stdout
    finally:
        sleeper.kill()
        sleeper.wait()
