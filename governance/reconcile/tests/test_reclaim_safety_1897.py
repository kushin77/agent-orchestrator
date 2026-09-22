"""The reclaim must read before it destroys — three defects, provoked (#1897).

Every control here runs against a **real** git repository with a real (local)
bare origin, real linked worktrees and a real ``.fleet`` session plane, because a
fake port cannot show any of the three defects: two of them are facts about git
(an uncommitted tree, a worktree path that is not a repository) and the third is
a fact about a *held lock file*.

The three defects, and the arm each one gets:

* **Defect 1 — the reclaim deleted uncommitted work.** ``_disposition`` asked
  only "is HEAD an ancestor of ``origin/master``?" and on ``True`` ran
  ``git worktree remove --force``. A lane whose HEAD is still at ``origin/master``
  — the normal state between ``worktree add`` and its first commit — was therefore
  force-removed even while it held edits that exist nowhere else, and because
  nothing was ever committed there was no object left to recover them from.
  Measured on the lane for #1887, deleted while its agent was editing in it.
  Now: dirty ⇒ **shelved**, in the fixture and on disk. The negative control
  (a *clean* lane at ``origin/master``) is still reclaimed.
* **Defect 2 — an unreadable worktree wedged every pass.** ``git rev-parse HEAD``
  in a path that exists but is not a git repository raised git's own bare
  ``RuntimeError`` out of the decision, so ``cli.cmd_watch`` logged ``pass
  failed`` every 60 s, reconciled nothing, and never cleared the beat. Now: a
  named ``WorkLocationUnreadable``, caught by the decision, **the pass completes**
  and the other lane is reconciled in the same pass.
* **Defect 3 — the heartbeat did not cover the lane's gate.** ``judge`` put the
  TTL arm ahead of everything, so a lane inside its own ~13-minute ``make verify``
  looked orphaned against a 15-minute TTL. Now: a HELD permit outranks a stale
  beat (reported, not reclaimed), an unreadable permit store is CANNOT-ASSESS and
  refuses rather than reclaims, and a gone worktree whose branch survives on the
  remote *unmerged* is **parked**, never reclaimed.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from governance.reconcile.heartbeat import read, stamp
from governance.reconcile.sweep import (
    CODE_ROOT,
    PARKED,
    RECLAIMED,
    REPORTED,
    SHELVED_OUTCOME,
    RepoOps,
    gate_in_flight,
    sweep,
)

OLD = 1_000_000.0
NOW = OLD + 20 * 60  # past a 15-minute TTL


def git(cwd, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()[-300:]}")
    return result.stdout.strip()


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """A real repository, a real bare origin, and the repository's own tooling.

    ``governance/`` and ``fleet/`` are copied in for the same reason
    ``test_teardown_forget_lane`` copies them: the applied arms call the repo's
    *own* CLIs (``forget-lane``, ``release-claim``) to reach their terminal state,
    so a scratch root without them reports every teardown as FAILED and can never
    demonstrate a RECLAIMED outcome. ``governance/dispatch/claims.py`` resolves its
    ``runtime`` sibling from ``fleet/``, so both trees are needed. The code under
    test is still this checkout's — the copy is the *root* the worker is pointed at.
    """
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
    git(repo, "config", "user.name", "Lane Human")
    git(repo, "config", "user.email", "lane@example.com")
    for tree in ("governance", "fleet"):
        shutil.copytree(
            CODE_ROOT / tree, repo / tree, ignore=shutil.ignore_patterns("__pycache__")
        )
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "-u", "origin", "master")
    git(repo, "fetch", "-q", "origin")
    return repo


def lane(root: Path, tmp_path: Path, name: str) -> Path:
    """A real linked worktree on its own branch, cut from ``origin/master``."""
    path = tmp_path / f"lane-{name}"
    subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "-q", "-b", name, str(path), "origin/master"],
        check=True,
    )
    return path


def beat(root: Path, session_id: str, *, worktree: str, branch: str, issue: int) -> None:
    """A stale session heartbeat for a lane (20 minutes past a 15-minute TTL)."""
    stamp(
        session_id,
        issue=issue,
        agent="lane-agent",
        root=root,
        lane=session_id,
        worktree=worktree,
        branch=branch,
        pid=None,
        at=OLD,
    )


def by_session(report):
    return {action.session_id: action for action in report.actions}


# ── Defect 1: uncommitted work is never reclaimed ───────────────────────────


def test_a_lane_at_origin_master_with_uncommitted_work_is_shelved(scratch: Path, tmp_path: Path):
    """The measured destruction, provoked: HEAD on master, an edit, a stale beat.

    Before the fix this lane was force-removed, its branch deleted and its claim
    released — with the edit, which existed nowhere else, gone for good.
    """
    path = lane(scratch, tmp_path, "dirty-lane")
    (path / "work-in-progress.txt").write_text("unmerged and uncommitted\n", encoding="utf-8")
    beat(scratch, "dirty-1", worktree=str(path), branch="dirty-lane", issue=1897)

    report = sweep(scratch, ttl_minutes=15, at=NOW, apply=True, ops=RepoOps(scratch))

    action = report.actions[0]
    assert action.outcome == SHELVED_OUTCOME, action.steps
    assert path.exists(), "the worktree holding the only copy of the work was destroyed"
    assert (path / "work-in-progress.txt").read_text(encoding="utf-8") == "unmerged and uncommitted\n"
    assert "dirty-lane" in git(scratch, "worktree", "list")
    assert "dirty-lane" in git(scratch, "branch", "--list", "dirty-lane")
    assert read("dirty-1", scratch) is not None, "a shelved lane keeps its beat"
    assert "work-in-progress.txt" in action.steps[0].detail


def test_a_clean_lane_at_origin_master_is_still_reclaimed(scratch: Path, tmp_path: Path):
    """The negative control: the fix must not make reclaim impossible.

    An identical lane — at ``origin/master``, stale beat — with a *clean* worktree
    is still reclaimed, worktree and all.
    """
    path = lane(scratch, tmp_path, "clean-lane")
    beat(scratch, "clean-1", worktree=str(path), branch="clean-lane", issue=1)

    report = sweep(scratch, ttl_minutes=15, at=NOW, apply=True, ops=RepoOps(scratch))

    action = report.actions[0]
    assert action.outcome == RECLAIMED, action.steps
    assert not path.exists()
    assert "clean-lane" not in git(scratch, "worktree", "list")
    assert read("clean-1", scratch) is None


def test_uncommitted_untracked_work_alone_still_shelves_the_lane(scratch: Path, tmp_path: Path):
    """The unrecoverable case, isolated: *untracked* files are work too.

    A tracked-file edit at least leaves a blob in the object store. An untracked
    file leaves nothing at all once the directory is removed, so it is the arm
    the read has to catch on its own.
    """
    path = lane(scratch, tmp_path, "untracked-lane")
    (path / "brand-new-module.py").write_text("# never committed anywhere\n", encoding="utf-8")
    beat(scratch, "untracked-1", worktree=str(path), branch="untracked-lane", issue=1897)

    report = sweep(scratch, ttl_minutes=15, at=NOW, apply=True, ops=RepoOps(scratch))

    assert report.actions[0].outcome == SHELVED_OUTCOME, report.actions[0].steps
    assert (path / "brand-new-module.py").exists()


# ── Defect 2: an unreadable worktree is named, and the pass continues ───────


def test_an_unreadable_worktree_is_named_and_others_are_reconciled_in_the_same_pass(
    scratch: Path, tmp_path: Path
):
    """The wedge, provoked: a lane path that exists but is not a repository.

    Before the fix this raised out of the decision and out of the pass, so every
    other session went unreconciled and the beat was never cleared — the worker
    wedged on one bad path until a human cleared it by hand.
    """
    broken = tmp_path / "lane-broken"
    (broken / "registry").mkdir(parents=True)
    (broken / "registry" / "extracted.txt").write_text("a scratch extraction\n", encoding="utf-8")
    assert not (broken / ".git").exists()
    assert not (broken / "governance").exists()  # it exists, and is not a git repo

    good = lane(scratch, tmp_path, "good-lane")
    beat(scratch, "broken-1", worktree=str(broken), branch="broken-lane", issue=2)
    beat(scratch, "good-1", worktree=str(good), branch="good-lane", issue=3)

    report = sweep(scratch, ttl_minutes=15, at=NOW, apply=True, ops=RepoOps(scratch))

    actions = by_session(report)
    assert set(actions) == {"broken-1", "good-1"}, "the pass stopped at the unreadable lane"

    unreadable = actions["broken-1"]
    assert unreadable.outcome == REPORTED, unreadable.steps
    assert "cannot-assess" in unreadable.reason
    assert str(broken) in unreadable.reason, "the venue must be named"
    assert any(step.action == "locate-work" for step in unreadable.steps)
    assert broken.exists(), "nothing about an unreadable lane may be touched"
    assert read("broken-1", scratch) is not None, "the beat is kept so the next pass re-measures it"

    # …and the pass went on to reconcile the lane it *could* read.
    assert actions["good-1"].outcome == RECLAIMED, actions["good-1"].steps
    assert not good.exists()


def test_an_unreadable_worktree_is_named_on_a_dry_run_too(scratch: Path, tmp_path: Path):
    """A dry run must not raise where an applied pass refuses."""
    broken = tmp_path / "lane-broken-dry"
    broken.mkdir()
    (broken / "scripts").mkdir()
    beat(scratch, "broken-dry-1", worktree=str(broken), branch="broken-dry-lane", issue=4)

    report = sweep(scratch, ttl_minutes=15, at=NOW, apply=False, ops=RepoOps(scratch))

    assert report.actions[0].outcome == REPORTED
    assert "cannot-assess" in report.actions[0].reason


# ── Defect 3: a held gate outranks a stale beat ─────────────────────────────


def _hold(worktree: str, store: Path) -> int:
    """Take the worktree's gate permit the way a running gate does: a live flock.

    Held through a *different* file description from the one ``probe`` opens, which
    is exactly what makes ``fleet/gatelock.probe`` report HELD (flock is
    per-open-file-description, so the conflict is real even in one process).
    """
    from fleet import gatelock

    path = gatelock.worktree_lock_path(worktree, root=store)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def test_a_held_gate_reports_a_stale_lane_and_releasing_it_reclaims(
    scratch: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The arm the gate lock already recorded and nothing read.

    A lane inside its own ``make verify`` looks orphaned against a 15-minute TTL.
    While the permit is HELD the lane is reported and left alone — worktree, branch,
    claim and beat intact. Released, the very next pass reclaims it.
    """
    store = tmp_path / "gates"
    monkeypatch.setenv("AO_GATE_LOCK_ROOT", str(store))
    path = lane(scratch, tmp_path, "gated-lane")
    beat(scratch, "gated-1", worktree=str(path), branch="gated-lane", issue=5)

    fd = _hold(str(path), store)
    try:
        assert gate_in_flight(str(path)) is True
        held = sweep(
            scratch, ttl_minutes=15, at=NOW, apply=True,
            ops=RepoOps(scratch), gate_in_flight=gate_in_flight,
        )
        action = held.actions[0]
        assert action.outcome == REPORTED, action.steps
        assert path.exists(), "a lane whose gate is running must not be reclaimed"
        assert "gated-lane" in git(scratch, "worktree", "list")
        assert read("gated-1", scratch) is not None
    finally:
        os.close(fd)

    assert gate_in_flight(str(path)) is False
    released = sweep(
        scratch, ttl_minutes=15, at=NOW, apply=True,
        ops=RepoOps(scratch), gate_in_flight=gate_in_flight,
    )
    assert released.actions[0].outcome == RECLAIMED, released.actions[0].steps
    assert not path.exists()


def test_a_shelved_lane_is_left_alone_while_its_gate_is_held(
    scratch: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A stale *shelved* record must not authorise a teardown of a live gate.

    The sweep re-evaluates a shelved lane every pass; without the gate arm that
    re-evaluation would reclaim the lane whose gate is running right now.
    """
    from governance.reconcile.heartbeat import SHELVED

    store = tmp_path / "gates"
    monkeypatch.setenv("AO_GATE_LOCK_ROOT", str(store))
    path = lane(scratch, tmp_path, "shelved-gated-lane")
    stamp(
        "shelved-gated-1", issue=6, agent="lane-agent", root=scratch, lane="shelved-gated-1",
        worktree=str(path), branch="shelved-gated-lane", pid=None, at=OLD, state=SHELVED,
        note="unmerged work",
    )

    fd = _hold(str(path), store)
    try:
        report = sweep(
            scratch, ttl_minutes=15, at=NOW, apply=True,
            ops=RepoOps(scratch), gate_in_flight=gate_in_flight,
        )
        assert report.actions[0].outcome == REPORTED, report.actions[0].steps
        assert path.exists()
    finally:
        os.close(fd)


def test_an_unreadable_gate_store_refuses_rather_than_reclaims(scratch: Path, tmp_path: Path):
    """``None`` from the seam is CANNOT-ASSESS: never a reclaim on unread state."""
    path = lane(scratch, tmp_path, "unreadable-gate-lane")
    beat(scratch, "unreadable-gate-1", worktree=str(path), branch="unreadable-gate-lane", issue=7)

    report = sweep(
        scratch, ttl_minutes=15, at=NOW, apply=True,
        ops=RepoOps(scratch), gate_in_flight=lambda worktree: None,
    )

    action = report.actions[0]
    assert action.outcome == REPORTED, action.steps
    assert "cannot-assess" in action.reason
    assert path.exists()
    assert read("unreadable-gate-1", scratch) is not None


def test_the_gate_seam_defaults_to_no_information(scratch: Path, tmp_path: Path):
    """A caller that gives no seam keeps the TTL behaviour it had before #1897."""
    path = lane(scratch, tmp_path, "no-seam-lane")
    beat(scratch, "no-seam-1", worktree=str(path), branch="no-seam-lane", issue=8)

    report = sweep(scratch, ttl_minutes=15, at=NOW, apply=True, ops=RepoOps(scratch))

    assert report.actions[0].outcome == RECLAIMED


def test_a_live_verify_process_protects_a_lane_even_when_the_lock_store_misses_it(
    scratch: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """#2001: measured twice — a live ``verify.sh`` was reaped anyway because the
    reconcile daemon's permit-store read came back free (a store the acquiring
    process and the reconciler do not provably share, e.g. differing
    ``AO_GATE_LOCK_ROOT``/``XDG_RUNTIME_DIR``). No gate permit is held here — the
    store read is genuinely free — but a real process is running inside the lane
    with ``verify.sh`` on its cmdline. The direct ``/proc`` scan must catch that
    and outrank the stale beat regardless of what the store says.
    """
    store = tmp_path / "gates"
    monkeypatch.setenv("AO_GATE_LOCK_ROOT", str(store))
    path = lane(scratch, tmp_path, "live-verify-lane")
    beat(scratch, "live-verify-1", worktree=str(path), branch="live-verify-lane", issue=11)

    script = path / "verify.sh"
    script.write_text("#!/usr/bin/env bash\nsleep 30\n", encoding="utf-8")
    script.chmod(0o755)
    proc = subprocess.Popen(["bash", "verify.sh"], cwd=str(path))
    try:
        assert gate_in_flight(str(path)) is True, "no lock is held; the live pid must still be found"
        report = sweep(
            scratch, ttl_minutes=15, at=NOW, apply=True,
            ops=RepoOps(scratch), gate_in_flight=gate_in_flight,
        )
        action = report.actions[0]
        assert action.outcome == REPORTED, action.steps
        assert path.exists(), "a lane running verify.sh must not be reclaimed"
        assert read("live-verify-1", scratch) is not None
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    script.unlink()  # else the reaper correctly shelves an uncommitted verify.sh

    # Negative control: process is gone, no lock is held — the reaper still works.
    assert gate_in_flight(str(path)) is False
    released = sweep(
        scratch, ttl_minutes=15, at=NOW, apply=True,
        ops=RepoOps(scratch), gate_in_flight=gate_in_flight,
    )
    assert released.actions[0].outcome == RECLAIMED, released.actions[0].steps
    assert not path.exists()
    assert not path.exists()


# ── Defect 3, gone-worktree arm: the branch's remote tip decides ────────────


def test_a_gone_worktree_whose_branch_is_unmerged_remotely_is_parked(scratch: Path, tmp_path: Path):
    """With no HEAD to read, the branch's remote tip is the only evidence left."""
    path = lane(scratch, tmp_path, "gone-lane")
    (path / "w.txt").write_text("work that has not landed\n", encoding="utf-8")
    git(path, "add", "w.txt")
    git(path, "commit", "-q", "-m", "unmerged work")
    git(path, "push", "-q", "-u", "origin", "gone-lane")
    # The worktree goes away (a crash); the branch survives on the server.
    subprocess.run(["git", "-C", str(scratch), "worktree", "remove", "--force", str(path)], check=True)
    beat(scratch, "gone-1", worktree=str(path), branch="gone-lane", issue=9)

    report = sweep(scratch, ttl_minutes=15, at=NOW, apply=True, ops=RepoOps(scratch))

    action = report.actions[0]
    assert action.outcome == PARKED, action.steps
    assert any(step.action == "keep-remote-branch" for step in action.steps), "the kept branch must be said out loud"
    assert git(scratch, "ls-remote", "--heads", "origin", "gone-lane"), "the branch is the work; it must survive"
    assert read("gone-1", scratch) is None  # the lane reached a terminal state


def test_a_gone_worktree_with_no_remote_branch_is_still_reclaimed(scratch: Path, tmp_path: Path):
    """The negative control for the gone arm: nothing preserved ⇒ reclaim as before."""
    path = lane(scratch, tmp_path, "gone-clean-lane")
    subprocess.run(["git", "-C", str(scratch), "worktree", "remove", "--force", str(path)], check=True)
    beat(scratch, "gone-clean-1", worktree=str(path), branch="gone-clean-lane", issue=10)

    report = sweep(scratch, ttl_minutes=15, at=NOW, apply=True, ops=RepoOps(scratch))

    assert report.actions[0].outcome == RECLAIMED, report.actions[0].steps
    assert not git(scratch, "ls-remote", "--heads", "origin", "gone-clean-lane")
