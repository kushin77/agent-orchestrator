"""Board reporting for reconciliation findings (#321).

A shelved / failed / suspect finding must reach the board as an issue — not only
a log line. These tests drive the sweep with an injected filer, so no network is
touched, and they are the mutation seam: if the board-reporting is reverted, the
filed-issue assertion fails.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from governance.lifecycle.report import BoardReporter
from governance.reconcile.heartbeat import SHELVED, stamp
from governance.reconcile.sweep import SHELVED_OUTCOME, sweep

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


# --- the dedupe ledger is the board's, not a checkout's (#1966) ----------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


def _scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "main"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "gate@example.com")
    _git(repo, "config", "user.name", "Gate")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def test_the_board_ledger_is_shared_across_checkouts(tmp_path: Path):
    """#1966: the dedupe ledger is the board's, not a checkout's.

    ``_reporter`` derived the ledger from ``Path(root)``, so the same fingerprint
    filed from a linked worktree (which has no ledger of its own) re-filed beside
    the filing from the main checkout — one fingerprint, two issues (#1894/#1896).
    The ledger must resolve to the main checkout for every checkout of the repo.
    """
    from governance.reconcile.cli import _reporter

    repo = _scratch_repo(tmp_path)
    lane = tmp_path / "lane"
    _git(repo, "worktree", "add", "-q", "-b", "issue-1966", str(lane))

    main_rep = _reporter(str(repo))
    lane_rep = _reporter(str(lane))
    assert main_rep.ledger == lane_rep.ledger, "the dedupe ledger is one, not one per checkout"
    assert main_rep.ledger == repo / ".fleet" / "board-reports.json"

    filer = FakeFiler()
    main_rep.filer = filer
    lane_rep.filer = filer
    key = "reconcile:suspect:641eef6a4837"
    first = main_rep.report(key, title="[reconcile] suspect session #304", body="b", apply=True)
    second = lane_rep.report(key, title="[reconcile] suspect session #304", body="b", apply=True)
    assert first.action == "filed"
    assert second.action == "deduped"
    assert len(filer.created) == 1
