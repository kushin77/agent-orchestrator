"""Board reporting for reconciliation findings (#321).

A shelved / failed / suspect finding must reach the board as an issue — not only
a log line. These tests drive the sweep with an injected filer, so no network is
touched, and they are the mutation seam: if the board-reporting is reverted, the
filed-issue assertion fails.
"""

from __future__ import annotations

from pathlib import Path

from governance.lifecycle.report import BoardReporter
from governance.reconcile.heartbeat import SHELVED, stamp
from governance.reconcile.sweep import SHELVED_OUTCOME, sweep

from conftest import AGENT, BRANCH, SESSION, WORKTREE, FakeOps  # noqa: E402

OLD = 1_000_000.0
NOW = OLD + 20 * 60
FRESH = OLD + 10


class FakeFiler:
    """A board filer that records writes instead of performing them."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.comments: list[dict] = []

    def create(self, title: str, body: str, labels) -> int:
        self.created.append({"title": title, "body": body, "labels": list(labels)})
        return 1000 + len(self.created)

    def comment(self, number: int, body: str) -> None:
        self.comments.append({"number": number, "body": body})


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


def reporter(filer: FakeFiler, root: Path) -> BoardReporter:
    return BoardReporter(filer, ledger=root / "ledger" / "reports.json")


def test_a_shelved_lane_files_a_board_finding(root: Path):
    beat(root, at=OLD)
    filer = FakeFiler()
    report = sweep(root, at=NOW, apply=True, ops=FakeOps(), reporter=reporter(filer, root))
    assert report.actions[0].outcome == SHELVED_OUTCOME
    assert filer.created, "a shelved lane must reach the board (mutation seam)"
    created = filer.created[0]
    assert "shelved" in created["title"].lower()
    assert WORKTREE in created["body"]
    assert BRANCH in created["body"]
    assert "#304" in created["body"]


def test_repeat_passes_do_not_spam_the_board(root: Path):
    beat(root, at=OLD)
    filer = FakeFiler()
    rep = reporter(filer, root)
    sweep(root, at=NOW, apply=True, ops=FakeOps(), reporter=rep)
    sweep(root, at=NOW, apply=True, ops=FakeOps(), reporter=rep)
    assert len(filer.created) == 1


def test_a_dry_run_reports_without_writing(root: Path):
    beat(root, at=OLD)
    filer = FakeFiler()
    report = sweep(root, at=NOW, apply=False, ops=FakeOps(), reporter=reporter(filer, root))
    assert report.actions[0].outcome == SHELVED_OUTCOME  # the decision is still computed
    assert filer.created == []
    assert report.board_reports and report.board_reports[0].action == "dry-run"


def test_a_failed_reconciliation_files_a_board_finding(root: Path):
    beat(root, at=OLD)
    filer = FakeFiler()
    sweep(
        root, at=NOW, apply=True,
        ops=FakeOps(on_main=True, fail=("remove-worktree",)),
        reporter=reporter(filer, root),
    )
    assert filer.created
    assert "failed" in filer.created[0]["title"].lower()


def test_a_suspect_session_files_a_board_finding(root: Path):
    beat(root, at=FRESH)
    filer = FakeFiler()
    report = sweep(
        root, at=FRESH + 1, alive={SESSION: False}, ops=FakeOps(),
        apply=True, reporter=reporter(filer, root),
    )
    assert report.actions[0].status == "suspect"
    assert filer.created
    assert "suspect" in filer.created[0]["title"].lower()


def test_a_resolved_shelved_lane_files_again_if_shelved_anew(root: Path):
    beat(root, at=OLD, state=SHELVED, note="unmerged work")
    filer = FakeFiler()
    rep = reporter(filer, root)
    # Shelved: files once.
    sweep(root, at=NOW, apply=True, ops=FakeOps(on_main=False, remotely=False), reporter=rep)
    assert len(filer.created) == 1
    # The work lands: reclaimed -> the finding resolves (ledger entry removed).
    sweep(root, at=NOW, apply=True, ops=FakeOps(on_main=True), reporter=rep)
    assert len(filer.created) == 1
    # A genuinely new shelve of the same issue files again, not swallowed.
    beat(root, at=OLD, state=SHELVED, note="unmerged work again")
    sweep(root, at=NOW, apply=True, ops=FakeOps(on_main=False, remotely=False), reporter=rep)
    assert len(filer.created) == 2
