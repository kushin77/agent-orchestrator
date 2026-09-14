"""Lane worktrees accumulate; the pruner must reclaim only what is provably safe.

Issue: stale worktrees from finished lanes piled up (11 in the 16 GiB /tmp tmpfs
plus 8 disk-backed ones on 2026-09-13), and cleaning them was a manual operator
step. `scripts/prune-worktrees.sh` makes it code. These tests pin the safety
rules — it must never remove a worktree whose work is not preserved elsewhere.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "prune-worktrees.sh"


def run_script(repo: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=cwd or repo,
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


def test_an_open_lane_is_kept_even_when_its_work_is_preserved(tmp_path):
    """Issue #516: a lane the repository still believes is open is off limits.

    The worktree of a lane that has just been cut is clean and sits on a commit
    that is already on master, so every preservation test says "removable" —
    while its owner is working in it. The .fleet/lanes/ record is what says
    otherwise, and it is deleted when the lane closes.
    """
    repo = make_repo(tmp_path)
    lane = tmp_path / "lane"
    git(repo, "worktree", "add", "-q", "-b", "issue-5-lane", str(lane))
    records = repo / ".fleet" / "lanes"
    records.mkdir(parents=True)
    (records / "abcdef123456.json").write_text(
        json.dumps({"session_id": "abcdef123456", "issue": 5, "worktree": str(lane)}),
        encoding="utf-8",
    )

    result = run_script(repo, "--apply")

    assert lane.exists()
    assert "claimed by an open lane" in result.stdout


def test_strict_keeps_a_worktree_parked_on_a_local_branch(tmp_path):
    """--strict reclaims only work preserved outside the worktree (issue #516)."""
    repo = make_repo(tmp_path)
    lane = tmp_path / "lane"
    git(repo, "worktree", "add", "-q", "-b", "issue-6-lane", str(lane))
    (lane / "work.txt").write_text("lane work\n", encoding="utf-8")
    git(lane, "add", "-A")
    git(lane, "commit", "-qm", "work only this lane has")
    git(lane, "push", "-qu", "origin", "issue-6-lane")

    strict = run_script(repo, "--check", "--strict")

    assert strict.returncode == 0, "work parked on a local branch is not reclaimable under --strict"
    assert "PARKED" in strict.stdout
    assert lane.exists()
    # The default contract is unchanged: without --strict this is still reclaimable.
    assert run_script(repo, "--check").returncode == 1


def test_strict_still_reclaims_a_detached_scratch_tree(tmp_path):
    """A detached scratch tree whose commits are on a remote branch loses nothing."""
    repo = make_repo(tmp_path)
    lane = tmp_path / "lane"
    git(repo, "worktree", "add", "-q", "--detach", str(lane))
    (lane / "scratch.txt").write_text("scratch\n", encoding="utf-8")
    git(lane, "add", "-A")
    git(lane, "commit", "-qm", "scratch commit")
    sha = git(lane, "rev-parse", "HEAD").stdout.strip()
    git(repo, "push", "-q", "origin", f"{sha}:refs/heads/scratch-copy")

    result = run_script(repo, "--apply", "--strict")

    assert not lane.exists(), "a detached tree preserved on a remote branch is reclaimable"
    assert result.returncode == 0


def test_the_open_lane_guard_reads_the_main_checkouts_fleet_dir(tmp_path):
    """A lane's worktree has no .fleet of its own — it lives in the main checkout.

    Running the pruner from inside a linked worktree is the normal case: a lane
    reclaiming its neighbours. Reading .fleet relative to the worktree finds no
    records at all, which silently disables the guard and lets the sweep delete a
    lane whose record says it is still open. Resolving it from the git common dir
    is what makes the guard real rather than decorative.
    """
    repo = make_repo(tmp_path)
    here = tmp_path / "here"
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-q", "-b", "issue-7-here", str(here))
    git(repo, "worktree", "add", "-q", "-b", "issue-8-other", str(other))
    records = repo / ".fleet" / "lanes"
    records.mkdir(parents=True)
    (records / "abcdef123456.json").write_text(
        json.dumps({"session_id": "abcdef123456", "issue": 8, "worktree": str(other)}),
        encoding="utf-8",
    )
    assert not (here / ".fleet").exists(), "the premise: a lane worktree carries no .fleet"

    result = run_script(repo, "--apply", cwd=here)

    assert other.exists(), "an open lane must survive a sweep run from inside another lane"
    assert "claimed by an open lane" in result.stdout
    assert here.exists(), "the current worktree is never its own target"
