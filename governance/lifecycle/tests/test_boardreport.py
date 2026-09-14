"""Board reporting for lifecycle findings (#321).

A non-terminal artifact the close-out (or audit) finds must reach the board as an
issue — not only a log line. These tests inject a fake filer, so no network is
touched, and they are the mutation seam: if the board-reporting is reverted, the
filed-issue assertion fails.
"""

from __future__ import annotations

from governance.lifecycle.audit import Finding
from governance.lifecycle.closeout import closeout
from governance.lifecycle.report import BoardReporter, board_report_findings

from conftest import FakeOps, clean_item  # noqa: E402


class FakeFiler:
    """A board filer that records writes instead of performing them."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.comments: list[dict] = []

    def create(self, title: str, body: str, labels) -> int:
        self.created.append({"title": title, "body": body, "labels": list(labels)})
        return 2000 + len(self.created)

    def comment(self, number: int, body: str) -> None:
        self.comments.append({"number": number, "body": body})


def reporter(filer: FakeFiler, tmp_path) -> BoardReporter:
    return BoardReporter(filer, ledger=tmp_path / "reports.json")


def test_a_non_terminal_artifact_files_a_board_finding(tmp_path):
    item = clean_item(branch_deleted=False, lane={"session_id": "s-269", "present": True})
    ops = FakeOps(item, fail=("delete-branch", "reclaim-lane"))
    filer = FakeFiler()
    result = closeout(item, ops, reporter=reporter(filer, tmp_path), apply=True)
    assert not result.ok
    assert filer.created, "a non-terminal artifact must reach the board (mutation seam)"
    titles = " ".join(created["title"] for created in filer.created)
    assert "BRANCH_NOT_DELETED" in titles
    assert "LANE_NOT_RECLAIMED" in titles


def test_repeat_close_outs_do_not_spam_the_board(tmp_path):
    item = clean_item(branch_deleted=False, lane={"session_id": "s-269", "present": True})
    filer = FakeFiler()
    rep = reporter(filer, tmp_path)
    closeout(item, FakeOps(item, fail=("delete-branch", "reclaim-lane")), reporter=rep, apply=True)
    closeout(item, FakeOps(item, fail=("delete-branch", "reclaim-lane")), reporter=rep, apply=True)
    assert len(filer.created) == 2  # one issue per (code, subject), no duplicates


def test_a_dry_run_reports_without_writing(tmp_path):
    item = clean_item(branch_deleted=False, lane={"session_id": "s-269", "present": True})
    filer = FakeFiler()
    result = closeout(
        item,
        FakeOps(item, fail=("delete-branch", "reclaim-lane")),
        reporter=reporter(filer, tmp_path),
        apply=False,
    )
    assert not result.ok
    assert filer.created == []
    assert result.board_reports and all(r.action == "dry-run" for r in result.board_reports)


def test_board_report_findings_is_idempotent_by_fingerprint(tmp_path):
    finding = Finding(
        code="BRANCH_NOT_DELETED",
        subject="#269",
        detail="source branch issue-269 still exists",
    )
    filer = FakeFiler()
    rep = reporter(filer, tmp_path)
    first = board_report_findings([finding], rep, apply=True)
    second = board_report_findings([finding], rep, apply=True)
    assert first and first[0].action == "filed"
    assert second and second[0].action == "deduped"
    assert len(filer.created) == 1
