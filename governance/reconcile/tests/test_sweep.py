"""The sweep: what it reclaims, what it parks, and what it refuses to destroy.

The centre of gravity is the third case. A lane whose work exists nowhere but its
own worktree must survive the sweep — that is the whole reason the teardown is
three-way instead of a single ``git worktree remove --force``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.reconcile.heartbeat import ORPHAN, SHELVED, SUSPECT, stamp
from governance.reconcile.sweep import (
    FAILED_OUTCOME,
    PARKED,
    RECLAIMED,
    REPORTED,
    SHELVED_OUTCOME,
    RepoOps,
    describe,
    sweep,
)

import importlib.util as _importlib_util  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_reconcile_tests_conftest", Path(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
AGENT = _conftest.AGENT
BRANCH = _conftest.BRANCH
SESSION = _conftest.SESSION
WORKTREE = _conftest.WORKTREE
FakeOps = _conftest.FakeOps

OLD = 1_000_000.0
NOW = OLD + 20 * 60  # past a 15-minute TTL
FRESH = OLD + 10  # inside it


def beat(root: Path, *, at: float = OLD, **overrides) -> None:
    stamp(
        overrides.pop("session_id", SESSION),
        issue=overrides.pop("issue", 304),
        agent=overrides.pop("agent", AGENT),
        root=root,
        lane=overrides.pop("lane", "governance-reconcile"),
        worktree=overrides.pop("worktree", WORKTREE),
        branch=overrides.pop("branch", BRANCH),
        at=at,
        **overrides,
    )


def test_an_empty_board_sweeps_clean(root: Path):
    report = sweep(root, at=NOW, ops=FakeOps())
    assert report.actions == []
    assert report.to_json()["counts"][RECLAIMED] == 0


def test_a_live_session_is_left_alone(root: Path):
    beat(root, at=NOW)
    ops = FakeOps()
    report = sweep(root, at=NOW, alive={SESSION: True}, ops=ops)
    assert [action.outcome for action in report.actions] == [REPORTED]
    assert ops.calls == []


def test_a_suspect_session_is_reported_and_not_touched(root: Path):
    """A fresh beat behind a dead pid: flagged, never reclaimed on that alone."""
    beat(root, at=FRESH)
    ops = FakeOps()
    report = sweep(root, at=NOW - 19 * 60, alive={SESSION: False}, ops=ops)
    assert report.actions[0].status == SUSPECT
    assert report.actions[0].outcome == REPORTED
    assert ops.calls == []


def test_a_stale_session_whose_worktree_is_gone_is_reconciled(root: Path):
    """The common crash: the worktree is gone, but the lane record and claim stay."""
    beat(root, at=OLD)
    ops = FakeOps(present=False)
    report = sweep(root, at=NOW, apply=True, ops=ops)
    assert report.actions[0].outcome == RECLAIMED
    assert ops.calls == ["forget-lane", "release-claim", "clear-heartbeat"]


def test_a_landed_lane_is_fully_reclaimed(root: Path):
    """Requirement 3, verification 3: the orphaned directory is pruned."""
    beat(root, at=OLD)
    ops = FakeOps(on_main=True)
    report = sweep(root, at=NOW, apply=True, ops=ops)
    assert report.actions[0].outcome == RECLAIMED
    assert ops.calls == [
        "remove-worktree",
        "delete-local-branch",
        "delete-remote-branch",
        "forget-lane",
        "release-claim",
        "clear-heartbeat",
    ]


def test_a_lane_preserved_only_remotely_is_parked_not_deleted(root: Path):
    """The remote branch is the only copy of the work: reclaim the disk, keep the branch."""
    beat(root, at=OLD)
    ops = FakeOps(remotely=True)
    report = sweep(root, at=NOW, apply=True, ops=ops)
    action = report.actions[0]
    assert action.outcome == PARKED
    assert "delete-remote-branch" not in ops.calls
    assert "remove-worktree" in ops.calls
    assert any(step.action == "keep-remote-branch" for step in action.steps)
    assert "release-claim" in ops.calls  # the issue is unlocked; the branch is the work


def test_unmerged_work_is_shelved_and_the_issue_stays_locked(root: Path):
    """The asymmetry, asserted: work is never traded for an unlocked issue."""
    beat(root, at=OLD)
    ops = FakeOps(present=True, on_main=False, remotely=False)
    report = sweep(root, at=NOW, apply=True, ops=ops)
    action = report.actions[0]
    assert action.outcome == SHELVED_OUTCOME
    assert ops.calls == ["mark-shelved"]
    assert "release-claim" not in ops.calls
    assert "remove-worktree" not in ops.calls
    assert "unmerged" in ops.shelved_reason or "TTL" in ops.shelved_reason


def test_a_dry_run_decides_without_acting(root: Path):
    beat(root, at=OLD)
    ops = FakeOps(on_main=True)
    report = sweep(root, at=NOW, apply=False, ops=ops)
    assert report.applied is False
    assert report.actions[0].outcome == RECLAIMED  # the decision is still reported
    assert ops.calls == []
    assert any(step.action == "plan" for step in report.actions[0].steps)


def test_a_dry_run_reports_which_case_a_lane_is_in(root: Path):
    beat(root, at=OLD)
    assert sweep(root, at=NOW, ops=FakeOps(present=False)).actions[0].outcome == RECLAIMED
    assert sweep(root, at=NOW, ops=FakeOps(remotely=True)).actions[0].outcome == PARKED
    assert sweep(root, at=NOW, ops=FakeOps()).actions[0].outcome == SHELVED_OUTCOME


def test_a_failing_step_is_reported_and_the_rest_still_run(root: Path):
    beat(root, at=OLD)
    ops = FakeOps(on_main=True, fail=("remove-worktree",))
    report = sweep(root, at=NOW, apply=True, ops=ops)
    action = report.actions[0]
    assert action.outcome == FAILED_OUTCOME
    assert any(step.outcome == "failed" for step in action.steps)
    assert "release-claim" in ops.calls  # the remaining work was not abandoned


def test_a_shelved_lane_self_heals_once_its_work_lands(root: Path):
    """A shelved session is re-evaluated every pass, not written off."""
    beat(root, at=OLD, state=SHELVED, note="unmerged work")
    ops = FakeOps(on_main=True)
    report = sweep(root, at=NOW, apply=True, ops=ops)
    assert report.actions[0].outcome == RECLAIMED
    assert "remove-worktree" in ops.calls


def test_a_shelved_lane_with_work_still_at_risk_stays_shelved(root: Path):
    beat(root, at=OLD, state=SHELVED, note="unmerged work")
    ops = FakeOps(present=True, on_main=False, remotely=False)
    report = sweep(root, at=NOW, apply=True, ops=ops)
    assert report.actions[0].outcome == SHELVED_OUTCOME
    assert ops.calls == ["mark-shelved"]


def test_killing_a_process_makes_the_sweep_flag_that_session(root: Path):
    """Requirement 2's verification, without killing anything: the pid is the seam."""
    beat(root, at=FRESH)
    report = sweep(root, at=FRESH + 1, alive={SESSION: False}, ops=FakeOps())
    assert report.actions[0].status == SUSPECT
    assert SESSION in str(report.actions[0])


def test_several_sessions_are_each_dispositioned_on_their_own_facts(root: Path):
    beat(root, at=NOW, session_id="live-one", issue=1)
    beat(root, at=OLD, session_id="dead-one", issue=2, worktree="/lanes/gone")
    ops = FakeOps(on_main=True)
    report = sweep(root, at=NOW, apply=True, alive={"live-one": True, "dead-one": True}, ops=ops)
    outcomes = {action.session_id: action.outcome for action in report.actions}
    assert outcomes == {"live-one": REPORTED, "dead-one": RECLAIMED}
    assert len(report.shelved) == 0


def test_the_report_is_machine_readable(root: Path):
    beat(root, at=OLD)
    payload = sweep(root, at=NOW, apply=True, ops=FakeOps(on_main=True)).to_json()
    assert payload["counts"][RECLAIMED] == 1
    assert payload["actions"][0]["issue"] == 304
    assert payload["actions"][0]["steps"]


def test_the_description_shows_the_case_and_the_steps(root: Path):
    beat(root, at=OLD)
    text = describe(sweep(root, at=NOW, apply=True, ops=FakeOps(on_main=True)))
    assert "reconcile (apply)" in text
    assert RECLAIMED in text
    assert "remove-worktree" in text


def test_sweep_refuses_to_run_without_an_operations_port(root: Path):
    with pytest.raises(ValueError):
        sweep(root, at=NOW)


def test_the_real_port_refuses_an_unknown_session_id(tmp_path: Path):
    """The real port is constructed here; its git effects are exercised by the gate.

    The ``forget-lane`` step is the exception: a lane record is not git state, so
    it is driven for real by the two controls below rather than stubbed
    (``RepoOps.forget_lane``, issues #1444/#1446/#1452).
    """
    ops = RepoOps(tmp_path)
    assert ops.worktree_present("") is False
    assert ops.worktree_present(str(tmp_path)) is True


# --- the real port's `forget-lane` step (issues #1444/#1446/#1452) -----------
#
# Every sweep reported this step as `forget-lane: ImportError: attempted relative
# import with no known parent package`, and the step is what retires a lane's own
# record. The suite built `RepoOps` and never called the method, so the gate stayed
# green while the record — and with it the issue's claim — outlived the lane. Both
# controls below fail on the old form and pass on the fix.

import sys  # noqa: E402

from governance.isolation.identity import mint  # noqa: E402
from governance.isolation.worktree import (  # noqa: E402
    list_records,
    read_record,
    write_record,
)


def _write_scratch_lane(root: Path):
    """A lane record on disk, written by the isolation package's own writer."""
    identity = mint(
        issue=1444,
        agent_id="copilot-qa-sme",
        lane="governance/reconcile",
        worktree_root=root / "lanes",
    )
    write_record(identity, root)
    return identity


def test_the_real_port_forgets_a_lane_record_under_a_scratch_root(tmp_path: Path):
    """The step, driven for real: the record it is handed is gone afterwards."""
    identity = _write_scratch_lane(tmp_path)
    assert read_record(identity.session_id, tmp_path) is not None

    loaded_before = set(sys.modules)
    detail = RepoOps(tmp_path).forget_lane(identity.session_id)

    assert detail == f"forgot lane record {identity.session_id}"
    assert read_record(identity.session_id, tmp_path) is None
    assert list_records(tmp_path) == []
    # And it got there through the *package*: the old form put `governance/isolation`
    # on `sys.path` and imported `worktree` as a top-level module, which is exactly a
    # copy with no parent package for its own `from .identity import ...` to resolve.
    assert "worktree" not in (set(sys.modules) - loaded_before)


def test_a_root_carrying_governance_isolation_is_only_ever_the_data_venue(tmp_path: Path):
    """The reported symptom, provoked: a root that carries `governance/isolation`.

    Every real checkout does, and that is what made the old form import a bare
    `worktree` and raise `ImportError: attempted relative import with no known
    parent package` — `governance/isolation/worktree.py`'s own
    `from .identity import ...` (#1444/#1446/#1452). The reconciled root supplies
    the lane record; the collaborator must come from this checkout either way, so a
    root that happens to carry one is data and nothing more.
    """
    (tmp_path / "governance").mkdir()
    (tmp_path / "governance" / "isolation").symlink_to(
        _conftest.REPO_ROOT / "governance" / "isolation", target_is_directory=True
    )
    identity = _write_scratch_lane(tmp_path)

    detail = RepoOps(tmp_path).forget_lane(identity.session_id)
    assert detail == f"forgot lane record {identity.session_id}"
    assert read_record(identity.session_id, tmp_path) is None


# --- controls (#885): the batch-limit refusal + the append-only ledger ------

from governance.reconcile import ledger as reconcile_ledger  # noqa: E402
from governance.reconcile import policy as reconcile_policy  # noqa: E402
from governance.reconcile.sweep import REFUSED_OUTCOME  # noqa: E402


def _controls(limit: int) -> reconcile_policy.Controls:
    return reconcile_policy.Controls(
        max_actions_per_pass=limit,
        outcome_codes={
            "reclaimed": "reconcile.reclaimed",
            "parked": "reconcile.parked",
            "shelved": "reconcile.shelved",
            "reported": "reconcile.reported",
            "failed": "reconcile.failed",
            "refused": "reconcile.batch-limit-exceeded",
        },
    )


def test_batch_limit_refuses_beyond_the_control(root: Path):
    """The #885 brief's mutation test: change a control value -> behaviour
    changes / refused by name."""
    beat(root, session_id="a", issue=1, at=OLD)
    beat(root, session_id="b", issue=2, at=OLD)
    report = sweep(root, at=NOW, apply=True, ops=FakeOps(on_main=True), controls=_controls(1))
    outcomes = {a.session_id: a.outcome for a in report.actions}
    assert sorted(outcomes.values()) == sorted([RECLAIMED, REFUSED_OUTCOME])
    assert REFUSED_OUTCOME in outcomes.values()


def test_batch_limit_zero_refuses_everything(root: Path):
    beat(root, session_id="a", issue=1, at=OLD)
    report = sweep(root, at=NOW, apply=True, ops=FakeOps(on_main=True), controls=_controls(0))
    assert all(a.outcome == REFUSED_OUTCOME for a in report.actions)


def test_dry_run_never_refuses_on_the_batch_limit(root: Path):
    """A dry run never touches disk, so the limit — a limit on ACTIONS taken —
    must not fire on a plan."""
    beat(root, session_id="a", issue=1, at=OLD)
    report = sweep(root, at=NOW, apply=False, ops=FakeOps(on_main=True), controls=_controls(0))
    assert all(a.outcome != REFUSED_OUTCOME for a in report.actions)


def test_every_sweep_decision_writes_exactly_one_ledger_record(root: Path):
    beat(root, session_id="a", issue=1, at=OLD)
    beat(root, session_id="b", issue=2, at=OLD)
    sweep(root, at=NOW, apply=True, ops=FakeOps(on_main=True))
    records = reconcile_ledger.read(root)
    assert len(records) == 2
    assert {r["session_id"] for r in records} == {"a", "b"}


def test_a_refused_decision_produces_exactly_one_named_ledger_record(root: Path):
    beat(root, session_id="a", issue=1, at=OLD)
    sweep(root, at=NOW, apply=True, ops=FakeOps(on_main=True), controls=_controls(0))
    records = reconcile_ledger.read(root)
    assert len(records) == 1
    assert records[0]["outcome"] == REFUSED_OUTCOME
    assert records[0]["code"] == "reconcile.batch-limit-exceeded"


# --- the real port's `preserved_remotely` must not trust a stale local cache
# (issue #1887) ---------------------------------------------------------------
#
# `git branch -r --contains <sha>` only sees remote-tracking refs this checkout
# has already fetched. The reconcile daemon's checkout is long-running and never
# fetches before a sweep, so a branch another lane pushed moments ago is
# invisible to that command even though `origin/<branch>` genuinely holds the
# work. The old form then found the work "nowhere" and let `_teardown` delete
# the branch it had just failed to see (measured 2026-09-22, #1887). The fix
# asks the remote directly (`git ls-remote origin <branch>`), which needs no
# fetch.

import subprocess  # noqa: E402


def _run(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)
    return result.stdout.strip()


def test_preserved_remotely_sees_a_branch_pushed_after_this_checkout_was_cloned(tmp_path: Path):
    """Provoke: push a branch to the remote, but never fetch it into the sweep's clone."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _run("init", "--bare", "-q", cwd=remote)

    origin = tmp_path / "origin"
    origin.mkdir()
    _run("init", "-q", "-b", "master", cwd=origin)
    _run("config", "user.email", "t@example.com", cwd=origin)
    _run("config", "user.name", "t", cwd=origin)
    (origin / "f.txt").write_text("one\n")
    _run("add", "f.txt", cwd=origin)
    _run("commit", "-q", "-m", "initial", cwd=origin)
    _run("remote", "add", "origin", str(remote), cwd=origin)
    _run("push", "-q", "origin", "master", cwd=origin)

    # The sweep's own checkout: cloned BEFORE the lane's branch exists upstream,
    # so it carries no `origin/issue-1887` remote-tracking ref at all.
    sweep_clone = tmp_path / "sweep-clone"
    _run("clone", "-q", str(remote), str(sweep_clone), cwd=tmp_path)

    # A lane pushes unmerged work to a NEW branch, from its own worktree.
    lane_worktree = tmp_path / "lane-worktree"
    _run("worktree", "add", "-q", "-b", "issue-1887", str(lane_worktree), "master", cwd=origin)
    (lane_worktree / "g.txt").write_text("two\n")
    _run("add", "g.txt", cwd=lane_worktree)
    _run("commit", "-q", "-m", "unmerged lane work", cwd=lane_worktree)
    _run("push", "-q", "origin", "issue-1887", cwd=lane_worktree)

    ops = RepoOps(sweep_clone)
    assert ops.preserved_on_main(str(lane_worktree)) is False
    assert ops.preserved_remotely(str(lane_worktree), "issue-1887") is True
