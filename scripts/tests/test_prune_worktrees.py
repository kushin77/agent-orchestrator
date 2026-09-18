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


# --- issue #830, item 3: generated runtime state is not a lane's work --------


def declare_runtime_state(repo: Path, *paths: str) -> None:
    """Write the ONE declaration the reaper reads, where its owner keeps it.

    `governance/isolation/worktree.py` owns `MACHINE_MANAGED_PATHS`; the reaper
    reads it from there rather than keeping a second copy that could disagree
    with the path the lane-reclaim code uses.
    """
    source = repo / "governance" / "isolation"
    source.mkdir(parents=True, exist_ok=True)
    declared = ", ".join(f'"{path}"' for path in paths)
    (source / "worktree.py").write_text(
        "# written by the test suite\n"
        f"MACHINE_MANAGED_PATHS: tuple[str, ...] = ({declared},)\n",
        encoding="utf-8",
    )


def dirty_lane(repo: Path, tmp_path: Path, *paths: str, name: str = "lane") -> Path:
    """A clean, preserved worktree made dirty in exactly `paths`."""
    lane = tmp_path / name
    git(repo, "worktree", "add", "-q", "--detach", str(lane), "origin/master")
    for path in paths:
        target = lane / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fleet-written\n", encoding="utf-8")
    return lane


def test_item3_declared_runtime_state_does_not_keep_a_worktree(tmp_path):
    """#830 item 3: a lane whose ONLY dirt is machine-written is reclaimable.

    Measured on the real tree: 11 of 66 keeps were held by a single uncommitted
    file, `.board/focus.json`, which the fleet rewrites and the lane never
    authored — so a lane that had finished could never be reclaimed.
    """
    repo = make_repo(tmp_path)
    declare_runtime_state(repo, ".board/focus.json")
    lane = dirty_lane(repo, tmp_path, ".board/focus.json")

    result = run_script(repo, "--apply")

    assert not lane.exists(), "declared runtime state must not pin a worktree for ever"
    assert "REMOVED" in result.stdout
    assert "declared runtime state" in result.stdout


def test_item3_a_real_uncommitted_file_still_keeps_the_worktree(tmp_path):
    """The exemption is narrow: only paths the declaration names are excused."""
    repo = make_repo(tmp_path)
    declare_runtime_state(repo, ".board/focus.json")
    lane = dirty_lane(repo, tmp_path, ".board/focus.json", "lane-work.txt")

    result = run_script(repo, "--apply")

    assert lane.exists(), "uncommitted lane work still keeps its worktree"
    assert "KEEP" in result.stdout


def test_item3_an_unreadable_declaration_excuses_nothing(tmp_path):
    """Fail closed: if the declaration cannot be read, NOTHING is excused.

    Widening what may be discarded is never the safe direction for a failure.
    """
    repo = make_repo(tmp_path)
    assert not (repo / "governance" / "isolation" / "worktree.py").exists()
    lane = dirty_lane(repo, tmp_path, ".board/focus.json")

    result = run_script(repo, "--apply")

    assert lane.exists(), "an unreadable declaration must not widen what may be discarded"
    assert "KEEP" in result.stdout


def test_item3_the_declaration_is_read_from_its_owner_not_copied(tmp_path):
    """Point the declaration at a different path and the reaper follows it.

    A second, hard-coded copy of the list inside the reaper would keep excusing
    `.board/focus.json` here, and so would disagree with the declaration's owner.
    """
    repo = make_repo(tmp_path)
    declare_runtime_state(repo, ".board/other.json")
    focus_only = dirty_lane(repo, tmp_path, ".board/focus.json", name="lane-focus")
    other_only = dirty_lane(repo, tmp_path, ".board/other.json", name="lane-other")

    run_script(repo, "--apply")

    assert focus_only.exists(), "a path the declaration no longer names is not excused"
    assert not other_only.exists(), "a path the declaration names is excused"


# --- issue #830, item 2: lane branches have a reaper, by CONTENT -------------


def squash_land(repo: Path, branch: str, filename: str, content: str) -> None:
    """Create `branch` carrying `content`, then land the SAME content on master as
    a different commit — exactly what this repo's squash-merge landing does."""
    git(repo, "checkout", "-q", "-b", branch)
    (repo / filename).write_text(content, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"{branch} work")
    git(repo, "push", "-qu", "origin", branch)
    git(repo, "checkout", "-q", "master")
    (repo / filename).write_text(content, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"{branch} landed (squashed)")
    git(repo, "push", "-qu", "origin", "master")
    git(repo, "fetch", "-q", "origin")


def test_item2_a_squash_landed_branch_is_reaped_by_content_not_ancestry(tmp_path):
    """The whole point: ancestry says NOT merged, content says merged.

    The landing path squash-merges, so a fully-landed lane branch is never an
    ancestor of master. An ancestry test would call every landed lane "unmerged"
    and keep its branch for ever — which is how 302 of them accumulated (#830).
    """
    repo = make_repo(tmp_path)
    squash_land(repo, "issue-42-lane", "feature.txt", "landed\n")
    ancestry = git(repo, "merge-base", "--is-ancestor", "issue-42-lane", "origin/master")
    assert ancestry.returncode != 0, "the premise: a squash-landed branch is NOT an ancestor"

    report = run_script(repo, "--check", "--branches")
    assert "STALE  branch issue-42-lane" in report.stdout
    assert report.returncode == 1, "a landed branch that still exists is a finding"

    applied = run_script(repo, "--branches", "--apply")
    assert applied.returncode == 0
    assert "REMOVED branch issue-42-lane" in applied.stdout
    assert git(repo, "rev-parse", "--verify", "--quiet", "issue-42-lane").returncode != 0


def test_item2_a_branch_whose_change_is_not_on_master_is_kept(tmp_path):
    """Fail closed: what content equivalence cannot prove is never deleted."""
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "issue-43-lane")
    (repo / "unmerged.txt").write_text("nobody else has this\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "unlanded work")
    git(repo, "push", "-qu", "origin", "issue-43-lane")
    git(repo, "checkout", "-q", "master")

    result = run_script(repo, "--branches", "--apply")

    assert git(repo, "rev-parse", "--verify", "--quiet", "issue-43-lane").returncode == 0, (
        "a branch whose change master does not have must never be deleted"
    )
    assert "KEEP   branch issue-43-lane" in result.stdout


def test_item2_a_branch_checked_out_in_a_worktree_is_never_reaped(tmp_path):
    """A branch some worktree is standing on is not a candidate at all — even
    though the worktree it holds is itself removable in the same run."""
    repo = make_repo(tmp_path)
    live = tmp_path / "live"
    git(repo, "worktree", "add", "-q", "-b", "issue-44-lane", str(live))
    git(repo, "push", "-qu", "origin", "issue-44-lane")

    result = run_script(repo, "--branches", "--apply")

    assert git(repo, "rev-parse", "--verify", "--quiet", "issue-44-lane").returncode == 0
    assert "issue-44-lane" not in result.stdout, "a checked-out branch is not even a candidate"
