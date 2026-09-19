"""A lane's teardown finishes — and a teardown that does not finish stays visible (#1446).

Measured 2026-09-19. ``RepoOps.forget_lane`` loaded ``governance/isolation/worktree.py``
as a **top-level** module, so it died on that module's own relative import
(``from .identity import ...``) with

    ImportError: attempted relative import with no known parent package

— *after* the git half of the teardown had already run. The sweep recorded the step
as FAILED and then cleared the session's heartbeat anyway, so the lane record
outlived the only worker that could forget it, and the ``failed:`` finding it filed
could never be retired (that key is resolved only for a session the sweep reclaims).
Five sessions, five reports: #1446, #1452, #1457, #1461, #1444.

Every control here is driven against a **real** repository, a real lane record and a
real heartbeat. A fake port cannot see a module-loading mistake, which is exactly why
the gate's scratch repository — which has no ``governance/`` of its own, so the load
fails as ``ModuleNotFoundError`` — stayed green while the live worker filed a report
every few minutes.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from governance.isolation.identity import mint
from governance.isolation.worktree import write_record
from governance.reconcile import heartbeat
from governance.reconcile.sweep import (
    CODE_ROOT,
    FAILED_OUTCOME,
    RECLAIMED,
    IsolationUnavailable,
    RepoOps,
    _isolation_worktree,
    sweep,
)

# Loaded by absolute path for the reason `test_sweep.py` documents: every
# governance suite's `tests/conftest.py` lands under the same bare module identity
# `conftest` in `sys.modules`, so whichever is imported last wins the name (#699,
# #702).
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_reconcile_teardown_conftest", Path(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
FakeOps = _conftest.FakeOps
SESSION = _conftest.SESSION

ISSUE = 1446
OLD = 1_000_000.0
NOW = OLD + 20 * 60  # past a 15-minute TTL


def real_repo(tmp_path: Path, *, code: tuple[str, ...] = ("governance",)) -> Path:
    """A real git repository carrying the repository's own code.

    The scratch root has to hold a real ``governance/`` tree: that is the shape the
    live worker runs against (its root *is* a checkout), and the shape the defect
    needs — the module loads and then dies on its own relative import.
    """
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "master", str(root)], check=True, capture_output=True, text=True
    )
    for name in code:
        shutil.copytree(CODE_ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    return root


def lane_record(root: Path, tmp_path: Path, suffix: str = "1446") -> str:
    """A real lane record in ``root``, naming a worktree that does not exist."""
    identity = mint(
        ISSUE, f"copilot-{suffix}", "governance/reconcile",
        suffix=suffix, worktree_root=tmp_path / "lanes",
    )
    write_record(identity, root)
    assert not identity.worktree.exists(), "the fixture lane must be the reported shape: no worktree"
    return identity.session_id


class _ClearingOps(FakeOps):
    """``FakeOps`` whose ``clear_session`` really removes the beat from ``root``.

    The terminal state under test is a fact about the disk, not about the port, so
    the port has to reach the disk for it.
    """

    def __init__(self, root: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root = root

    def clear_session(self, session_id: str) -> str:
        self._record("clear-heartbeat")
        return "cleared heartbeat" if heartbeat.clear(session_id, self._root) else "no heartbeat to clear"


def test_forget_lane_reaches_the_record_in_a_real_repo(tmp_path: Path):
    """The step the whole teardown ends on, against the module that owns the record.

    Before the fix this raises ``ImportError: attempted relative import with no
    known parent package`` from ``worktree.py``'s own ``from .identity import ...``.
    """
    root = real_repo(tmp_path)
    session_id = lane_record(root, tmp_path)
    record = root / ".fleet" / "lanes" / f"{session_id}.json"
    assert record.exists()

    assert RepoOps(root).forget_lane(session_id) == f"forgot lane record {session_id}"
    assert not record.exists(), "the record is the step's terminal state"


def test_a_record_already_forgotten_is_a_performed_no_op(tmp_path: Path):
    """Idempotent: a second pass over a lane whose record is gone says so."""
    root = real_repo(tmp_path)
    session_id = lane_record(root, tmp_path)
    RepoOps(root).forget_lane(session_id)

    assert RepoOps(root).forget_lane(session_id) == f"no lane record for {session_id} (already forgotten)"


def test_an_applied_pass_finishes_a_teardown_in_a_real_repo(tmp_path: Path):
    """The whole teardown, on a real repository: reclaimed, record gone, beat gone."""
    root = real_repo(tmp_path, code=("governance", "fleet"))
    session_id = lane_record(root, tmp_path)
    heartbeat.stamp(
        session_id, issue=ISSUE, agent="copilot-1446", root=root, lane="governance/reconcile",
        worktree=str(tmp_path / "lanes" / "gone"), branch="issue-1446", pid=4194303, at=OLD,
    )

    report = sweep(root, at=NOW, apply=True, ops=RepoOps(root))
    action = report.actions[0]
    steps = [(step.action, step.outcome, step.detail) for step in action.steps]
    assert action.outcome == RECLAIMED, steps
    assert [name for name, _, _ in steps] == ["forget-lane", "release-claim", "clear-heartbeat"]
    assert not (root / ".fleet" / "lanes" / f"{session_id}.json").exists(), "the record survived the pass"
    assert heartbeat.read(session_id, root) is None, "the beat survived the pass"


def test_a_failed_step_keeps_the_beat_and_the_next_pass_finishes_the_job(tmp_path: Path):
    """A teardown that did not finish must not hide the lane that owes it.

    This is the wedge #1446 left behind, as an assertion: with the step failing, the
    beat is *kept* (so the lane is re-measured, and the ``failed:`` finding it filed
    has a terminal state), the skip is named rather than silent, and the very next
    pass — with the failing step working — reaches the terminal state.
    """
    root = tmp_path
    heartbeat.stamp(
        SESSION, issue=ISSUE, agent="copilot-1446", root=root, lane="governance/reconcile",
        worktree="/lanes/gone", branch="issue-1446", pid=4194303, at=OLD,
    )
    failing = _ClearingOps(root, present=False, fail=("forget-lane",))

    first = sweep(root, at=NOW, apply=True, ops=failing)
    action = first.actions[0]
    assert action.outcome == FAILED_OUTCOME, action.outcome
    assert heartbeat.read(SESSION, root) is not None, "a failed teardown cleared the beat: the wedge"
    assert "clear-heartbeat" not in failing.calls, "the beat was cleared by a port call"
    kept = {step.action: step.detail for step in action.steps if step.action == "clear-heartbeat"}
    assert kept, "the skipped step is not named at all"
    assert "kept" in kept["clear-heartbeat"] and "visible" in kept["clear-heartbeat"], kept
    failed = [step.detail for step in action.steps if step.outcome == "failed"]
    assert failed and "forget-lane refused by the fixture" in failed[0], failed

    second = sweep(root, at=NOW, apply=True, ops=_ClearingOps(root, present=False))
    assert second.actions[0].outcome == RECLAIMED, "the lane never reached its terminal state"
    assert heartbeat.read(SESSION, root) is None


def deniable_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``from governance.isolation import worktree`` raise, as a broken checkout does.

    The import statement always goes through ``builtins.__import__``, so denying it
    here is a real provocation of the refusal path — and unlike clearing
    ``sys.modules`` it cannot be defeated by the package attribute a previous test
    already bound.
    """
    import builtins

    real_import = builtins.__import__

    def deny(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002 - the protocol
        if name == "governance.isolation" and fromlist and "worktree" in fromlist:
            raise ImportError("denied by the fixture: this checkout has no isolation package")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", deny)


def test_an_unimportable_isolation_package_refuses_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """No isolation package: say which module, from where, and why — not how it loaded."""
    deniable_import(monkeypatch)

    with pytest.raises(IsolationUnavailable) as excinfo:
        RepoOps(tmp_path).forget_lane(SESSION)

    message = str(excinfo.value)
    assert "governance.isolation.worktree cannot be imported from" in message, message
    assert str(CODE_ROOT) in message, "the refusal does not name the checkout it looked in"
    assert "ImportError" in message, "the underlying cause is dropped"
    assert "relative import with no known parent package" not in message, (
        "the refusal repeats the old message about this worker's own loader"
    )


def test_the_named_refusal_reaches_the_pass_and_keeps_the_lane_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The refusal above, through a whole applied pass: named on the step, beat kept."""
    deniable_import(monkeypatch)
    heartbeat.stamp(
        SESSION, issue=ISSUE, agent="copilot-1446", root=tmp_path, lane="governance/reconcile",
        worktree="/lanes/gone", branch="issue-1446", pid=4194303, at=OLD,
    )

    report = sweep(tmp_path, at=NOW, apply=True, ops=RepoOps(tmp_path))
    action = report.actions[0]
    assert action.outcome == FAILED_OUTCOME, action.outcome
    detail = next(step.detail for step in action.steps if step.action == "forget-lane")
    assert detail.startswith("IsolationUnavailable: governance.isolation.worktree cannot be imported from"), detail
    assert heartbeat.read(SESSION, tmp_path) is not None, "a named refusal must not hide the lane"


def test_a_record_that_survives_forget_record_is_named_not_reported_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The step never reports PERFORMED over a record still on disk."""
    root = real_repo(tmp_path)
    session_id = lane_record(root, tmp_path)
    module = _isolation_worktree()
    monkeypatch.setattr(module, "forget_record", lambda *args, **kwargs: None)

    with pytest.raises(IsolationUnavailable) as excinfo:
        RepoOps(root).forget_lane(session_id)

    assert "survived forget_record" in str(excinfo.value)
    assert (root / ".fleet" / "lanes" / f"{session_id}.json").exists()
