"""A dangling worktree directory cannot wedge a lane's close-out (#1441/#1443).

Both issues report the same defect at the same code path, and this module pins it
against the machinery that produced it rather than against a stand-in: a **real**
repository, a **real** ``git worktree``, the **real** reclaim operation that leaves the
shape (the admin entry under ``.git/worktrees/`` pruned while the directory survives),
and the **real** ``RepoLaneOps``. The only fiction is the board half — a scratch
repository has no pull request — which is the same seam
``scripts/check-lifecycle-closeout.sh`` §2 already drives.

The measured statement (2026-09-19, ``origin/master`` ``d6b3eb8d``, ledger
``action=close-lane``)::

    blocked    worktree-removed: RuntimeError: git worktree remove <path>:
               fatal: '<path>' is not a working tree
    withheld   lane-archived: withheld: worktree-removed is blocked
    BLOCKED    closeout-blocked:worktree-removed

``worktree_present`` was ``Path(path).exists()`` — the **directory** — while
``git worktree remove`` needs the **admin entry**. The two halves of "is the worktree
there?" disagreed, so the dry run said ``would remove`` and the apply refused; because
``lane-archived`` is withheld behind it, ``forget_lane`` never ran and the record could
never be retired. 7 of 32 records were wedged.

What is pinned here:

* the fixture really is the defect shape — the directory exists, git refuses to remove
  it, and the pre-fix predicate is what would have differed;
* the record reaches its terminal state, naming the leftover path;
* the leftover **directory is not deleted** (AGENTS.md rule 17: the admin entry being
  gone means git can no longer say what is inside);
* a lane whose tip is not content-landed is still refused, leftover or not;
* a lane with a registered worktree still has its tree really removed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from governance.lifecycle import lane_closeout as lc
from governance.lifecycle.cli import RepoLaneOps

ISSUE = 1441

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "lifecycle-test",
    "GIT_AUTHOR_EMAIL": "lifecycle-test@agents.invalid",
    "GIT_COMMITTER_NAME": "lifecycle-test",
    "GIT_COMMITTER_EMAIL": "lifecycle-test@agents.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
}


def git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=GIT_ENV)
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}: {result.stderr.strip()}")
    return result.stdout.strip()


def commit(cwd: Path, name: str, message: str) -> str:
    (cwd / name).write_text(f"{name}\n", encoding="utf-8")
    git(cwd, "add", name)
    git(cwd, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
    return git(cwd, "rev-parse", "HEAD")


def _repo(root: Path) -> Path:
    """A real repository with a real bare origin, cloned so ``origin/master`` resolves."""
    origin = root / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True, env=GIT_ENV)
    main = root / "main"
    subprocess.run(["git", "clone", "-q", str(origin), str(main)], check=True, capture_output=True, env=GIT_ENV)
    commit(main, "seed.txt", "seed")
    git(main, "push", "-q", "origin", "master")
    return main


def _lane(main: Path, issue: int, *, landed: bool, dangling: bool):
    """A real lane: worktree, branch, a squash landing, and a lane record."""
    branch = f"issue-{issue}"
    worktree = main.parent / f"ao-{issue}"
    git(main, "worktree", "add", "-q", "-b", branch, str(worktree), "master")
    tip = commit(worktree, f"work-{issue}.txt",
                 f"work for #{issue}\n\nRefs kushin77/agent-orchestrator#{issue}")
    git(main, "push", "-q", "origin", branch)

    if landed:
        # The repo squash-merges, so a landed tip is never an ancestor of master: the
        # same CONTENT lands under a different commit, which is what makes the
        # content-landed test the load-bearing one (and what #1265 was about).
        git(main, "checkout", "-q", "master")
        (main / f"work-{issue}.txt").write_text(f"work-{issue}.txt\n", encoding="utf-8")
        git(main, "add", f"work-{issue}.txt")
        git(main, "-c", "commit.gpgsign=false", "commit", "-q", "-m",
            f"squash #{issue}\n\nRefs kushin77/agent-orchestrator#{issue}\nCloses #{issue}")
        git(main, "push", "-q", "origin", "master")
    git(main, "fetch", "-q", "origin")

    if dangling:
        # THE MEASURED SHAPE, produced by hand because no single git command makes it:
        # a reclaim prunes the admin entry (`git worktree remove` then deletes the
        # directory; `git worktree prune` deletes only the entry) and the directory
        # survives when a process holds it or a crash intervenes.
        shutil.rmtree(main / ".git" / "worktrees" / worktree.name)
        assert worktree.exists(), "the fixture must leave the dangling DIRECTORY"

    record = {"session_id": f"lane{issue}", "lane_id": f"lane{issue}", "issue": issue,
              "agent_id": "fixture", "lane": "fixture", "branch": branch,
              "worktree": str(worktree), "opened_at": "2026-09-19T00:00:00Z"}
    lanes = main / ".fleet" / "lanes"
    lanes.mkdir(parents=True, exist_ok=True)
    (lanes / f"lane{issue}.json").write_text(json.dumps(record), encoding="utf-8")
    sessions = main / ".fleet" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"lane{issue}.json").write_text(json.dumps({"session_id": f"lane{issue}", "at": 1.0}),
                                                encoding="utf-8")
    return record, worktree, tip


class Ops(RepoLaneOps):
    """The real git half; only the board half is fixed (a scratch repo has no PR)."""

    def pull_request_for(self, branch):
        return lc.PullRequest(9000, "merged", git(self.root, "rev-parse", "origin/master"))

    def trailer_finding(self, sha):
        return None

    def issue_state(self, issue):
        return "closed"


def outcomes(result) -> dict:
    return {step.name: step.outcome for step in result.steps}


def test_the_fixture_really_is_the_defect_shape(tmp_path):
    """Without this, the controls below would be testing nothing.

    The pre-fix predicate (a directory that exists) is TRUE here while git really does
    refuse to remove the path — the two answers the defect was made of.
    """
    main = _repo(tmp_path)
    _record, worktree, _tip = _lane(main, ISSUE, landed=True, dangling=True)

    assert Path(worktree).exists(), "the old predicate's question must answer YES"
    removal = subprocess.run(
        ["git", "-C", str(main), "worktree", "remove", "--force", str(worktree)],
        capture_output=True, text=True, env=GIT_ENV,
    )
    assert removal.returncode != 0, "git must really refuse this path"
    assert lc.ALREADY_TORN_DOWN in removal.stderr, removal.stderr
    assert str(worktree) not in git(main, "worktree", "list", "--porcelain")
    assert Ops(main).worktree_present(str(worktree)) is False
    assert Ops(main).worktree_leftover(str(worktree)) is True


def test_a_dangling_worktree_reaches_terminal_and_names_the_leftover(tmp_path):
    """The fix, on the real port: the record settles, and the step says why."""
    main = _repo(tmp_path)
    record, worktree, _tip = _lane(main, ISSUE, landed=True, dangling=True)

    result = lc.closeout_lane(record, Ops(main), apply=True, now="2026-09-19T12:00:00Z")

    assert result.ok, lc.describe(result)
    assert result.blocked == []
    assert outcomes(result)["worktree-removed"] == "skipped"
    assert outcomes(result)["lane-archived"] == "performed"
    step = next(step for step in result.steps if step.name == "worktree-removed")
    assert str(worktree) in step.detail and "already torn down" in step.detail

    # The record really reached its terminal state, and the branch really went.
    assert not (main / ".fleet" / "lanes" / f"lane{ISSUE}.json").exists()
    assert not (main / ".fleet" / "sessions" / f"lane{ISSUE}.json").exists()
    assert git(main, "rev-parse", "--verify", "--quiet", f"refs/heads/issue-{ISSUE}", check=False) == ""
    assert git(main, "ls-remote", "--heads", "origin", f"issue-{ISSUE}") == ""
    archive = main / ".fleet" / "lifecycle" / "lanes" / f"lane{ISSUE}.json"
    assert json.loads(archive.read_text(encoding="utf-8"))["evidence"]["worktree_leftover"] is True


def test_the_leftover_directory_is_not_deleted(tmp_path):
    """Rule 17: with the admin entry gone git cannot say what is inside, so the
    directory is left for an operator rather than deleted on an unmeasurable guess."""
    main = _repo(tmp_path)
    record, worktree, _tip = _lane(main, ISSUE, landed=True, dangling=True)
    (worktree / "maybe-the-lanes-only-copy.txt").write_text("unmeasurable\n", encoding="utf-8")

    result = lc.closeout_lane(record, Ops(main), apply=True)

    assert result.ok
    assert worktree.exists(), "the leftover directory must survive the close-out"
    assert (worktree / "maybe-the-lanes-only-copy.txt").read_text(encoding="utf-8") == "unmeasurable\n"


def test_an_unlanded_lane_is_refused_even_when_its_worktree_is_dangling(tmp_path):
    """Negative control: the leftover reading is about the DIRECTORY, never the work.

    A tip that is not content-landed still blocks by name, and nothing of the lane is
    touched — no branch deleted, no record archived, no directory removed.
    """
    main = _repo(tmp_path)
    record, worktree, tip = _lane(main, ISSUE, landed=False, dangling=True)

    result = lc.closeout_lane(record, Ops(main), apply=True)

    assert result.blocked == ["closeout-blocked:branch-reaped"], lc.describe(result)
    assert git(main, "rev-parse", "--verify", "--quiet", f"refs/heads/issue-{ISSUE}") == tip
    assert tip in git(main, "ls-remote", "--heads", "origin", f"issue-{ISSUE}")
    assert (main / ".fleet" / "lanes" / f"lane{ISSUE}.json").exists()
    assert worktree.exists()


def test_a_registered_worktree_is_still_really_removed(tmp_path):
    """The other half: the fix must not stop removing worktrees it CAN remove."""
    main = _repo(tmp_path)
    record, worktree, _tip = _lane(main, 1442, landed=True, dangling=False)
    assert Ops(main).worktree_present(str(worktree)) is True

    result = lc.closeout_lane(record, Ops(main), apply=True)

    assert result.ok, lc.describe(result)
    assert outcomes(result)["worktree-removed"] == "performed"
    assert not worktree.exists()


def test_a_registered_worktree_holding_the_lanes_work_is_kept(tmp_path):
    """And it must not remove one that holds the lane's own uncommitted work (#786)."""
    main = _repo(tmp_path)
    record, worktree, _tip = _lane(main, 1443, landed=True, dangling=False)
    (worktree / "uncommitted-lane-work.txt").write_text("not committed anywhere\n", encoding="utf-8")

    result = lc.closeout_lane(record, Ops(main), apply=True)

    assert result.blocked == ["closeout-blocked:worktree-removed"], lc.describe(result)
    assert worktree.exists() and (worktree / "uncommitted-lane-work.txt").exists()
    assert (main / ".fleet" / "lanes" / "lane1443.json").exists()
    assert not (main / ".fleet" / "lifecycle" / "lanes" / "lane1443.json").exists()


def test_an_unreadable_worktree_list_is_cannot_assess_not_a_verdict(tmp_path, monkeypatch):
    """A source that cannot be read is CANNOT-ASSESS — never "no worktree on disk",
    which would archive a record on the strength of a measurement that never happened."""
    main = _repo(tmp_path)
    record, _worktree, _tip = _lane(main, 1444, landed=True, dangling=False)
    ops = Ops(main)

    def broken(*args: str):
        return subprocess.CompletedProcess(args, 128, "", "fatal: not a git repository")

    monkeypatch.setattr(ops, "_git", broken)
    with pytest.raises(lc.LaneUnavailable):
        lc.closeout_lane(record, ops, apply=True)
