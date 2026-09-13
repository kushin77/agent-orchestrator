"""Pytest bootstrap + fixtures for the governance/reconcile suite (issue #304).

``governance/`` is a PEP-420 namespace package, so the repository root goes on
``sys.path`` and the modules are imported qualified (``governance.reconcile.*``).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from governance.reconcile.heartbeat import (  # noqa: E402
    RUNNING,
    Session,
    clear,
    stamp,
)

WORKTREE = "/lanes/ao-304-deadbeef"
BRANCH = "issue-304"
AGENT = "subagent-dead"
SESSION = "deadbeef0001"


def make_session(**overrides) -> Session:
    """A session record; every test overrides only what it is about."""
    base = dict(
        session_id=SESSION,
        issue=304,
        agent=AGENT,
        lane="governance-reconcile",
        worktree=WORKTREE,
        branch=BRANCH,
        pid=4242,
        at=1_000_000.0,
        state=RUNNING,
        note="",
    )
    base.update(overrides)
    return Session(**base)


def dead_pid() -> int:
    """A pid that is definitely gone: spawn a process, reap it, return its pid."""
    child = subprocess.Popen(["true"])
    child.wait()
    return child.pid


class FakeOps:
    """A reconciliation port whose effects are recorded, not performed.

    ``on_main`` / ``remotely`` set where the lane's work lives, which is the only
    input that changes the *outcome* of a teardown.
    """

    def __init__(
        self,
        *,
        present: bool = True,
        on_main: bool = False,
        remotely: bool = False,
        fail: tuple[str, ...] = (),
    ) -> None:
        self.present = present
        self.on_main = on_main
        self.remotely = remotely
        self.fail = set(fail)
        self.calls: list[str] = []
        self.shelved_reason = ""

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(f"{name} refused by the fixture")

    def worktree_present(self, path: str) -> bool:
        return self.present

    def preserved_on_main(self, worktree: str) -> bool:
        return self.on_main

    def preserved_remotely(self, worktree: str, branch: str) -> bool:
        return self.remotely

    def remove_worktree(self, path: str) -> str:
        self._record("remove-worktree")
        self.present = False
        return f"removed {path}"

    def delete_branch(self, branch: str, *, remote: bool) -> str:
        self._record("delete-remote-branch" if remote else "delete-local-branch")
        return f"deleted {'origin/' if remote else ''}{branch}"

    def forget_lane(self, session_id: str) -> str:
        self._record("forget-lane")
        return f"forgot {session_id}"

    def release_claim(self, issue: int, agent: str) -> str:
        self._record("release-claim")
        return f"released #{issue}"

    def mark_shelved(self, session: Session, reason: str) -> str:
        self._record("mark-shelved")
        self.shelved_reason = reason
        return f"shelved: {reason}"

    def clear_session(self, session_id: str) -> str:
        self._record("clear-heartbeat")
        return "cleared"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def beaten(root: Path):
    """A session with a real heartbeat file on disk, and its root."""

    def beat(**kwargs):
        session = stamp(
            kwargs.pop("session_id", SESSION),
            issue=kwargs.pop("issue", 304),
            agent=kwargs.pop("agent", AGENT),
            root=root,
            lane=kwargs.pop("lane", "governance-reconcile"),
            worktree=kwargs.pop("worktree", WORKTREE),
            branch=kwargs.pop("branch", BRANCH),
            **kwargs,
        )
        return session

    yield beat
    clear(SESSION, root)
