"""A filed finding reaches a terminal state, or it is named (#973).

The defect these tests pin down was measured on issue #973: the board carried
`[lifecycle] LANE_NOT_RECLAIMED — #241` while the item it named was fully
reclaimed, and nothing in the fleet could ever retire it. Filing was idempotent;
unfiling did not exist.

Both halves are asserted here, because a fix that clears the first by clearing the
second is worse than the bug:

* a finding whose invariant is **no longer charged** is retired, and the board
  artifact is closed with the re-measurement as its evidence;
* a finding whose invariant **is still charged** — and one whose subject cannot be
  measured at all — keeps its fingerprint and is named. Absence of evidence is
  never read as absence of the violation.

Every test is offline: the board is an injected record and the closer is a fake,
so no network and no real issue is touched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from governance.lifecycle.report import BoardReporter, board_report_findings
from governance.reconcile.findings import (
    FAILED,
    RESOLVED,
    STILL_OWED,
    UNMEASURED,
    WOULD_RESOLVE,
    _measure,
    counts,
    lifecycle_entries,
    recheck_findings,
)
from governance.reconcile.sweep import SHELVED_OUTCOME, sweep

from conftest import FakeOps  # noqa: E402

#: The exact fingerprint the board carried for #241, from `.fleet/board-reports.json`.
KEY = "lifecycle:LANE_NOT_RECLAIMED:#241"
NUMBER = 973
COLLECTED_AT = "deadbeefcafe0000000000000000000000000000"


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


class FakeCloser:
    """The board effect that makes a finding terminal, recorded not performed."""

    def __init__(self, *, fail: bool = False) -> None:
        self.closed: list[dict] = []
        self.fail = fail

    def close(self, number: int, comment: str) -> None:
        if self.fail:
            raise RuntimeError("gh issue close refused by the fixture")
        self.closed.append({"number": number, "comment": comment})


@dataclass(frozen=True)
class FakeFinding:
    """A ``Finding``-shaped object, for driving `board_report_findings`."""

    code: str
    subject: str
    detail: str = "still charged"
    remediation: str = "close the lane"


def reporter(filer: FakeFiler, root: Path, key: str = KEY) -> BoardReporter:
    """A reporter whose ledger already holds the #241-style filed finding."""
    ledger = root / ".fleet" / "board-reports.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps({key: {"number": NUMBER, "title": "[lifecycle] LANE_NOT_RECLAIMED — #241"}}) + "\n",
        encoding="utf-8",
    )
    return BoardReporter(filer, ledger=ledger)


def item(*, lane: dict | None = None) -> dict:
    """#241 as the collector reports it — reclaimed unless a lane is supplied.

    The shape is the real one, from the live record read off the board on
    2026-09-17: closed, merged, branch deleted, no live claim, journal recorded.
    """
    return {
        "issue": 241,
        "title": "Generated-cron reconciler (leaderboard pattern, later)",
        "state": "closed",
        "milestone": None,
        "labels": ["epic:session-fleet"],
        "pr": {
            "number": 962,
            "state": "merged",
            "branch": "issue-241",
            "head_commit": "aa1a7a1d7987fcc799b50e3913445529ee6f773f",
            "merge_commit": "3091fbdb244f45d69368598a6aa77a4ebfb3c74f",
        },
        "branch_deleted": True,
        "claim": {"agent": None, "live": False},
        "directive": {},
        "lane": lane or {},
        "verify": {"commit": "aa1a7a1d7987fcc799b50e3913445529ee6f773f", "ok": True, "source": "lane"},
        "closing_evidence": True,
    }


def record(*items: dict) -> dict:
    return {"scope": "fixture", "collected_at": COLLECTED_AT, "items": list(items), "tracking": {}}


def leftover(reporter_: BoardReporter) -> dict:
    return json.loads(Path(reporter_.ledger).read_text(encoding="utf-8"))


# --- the fingerprint is read, not guessed -----------------------------------


def test_only_lifecycle_fingerprints_are_re_measured():
    entries, unreadable = lifecycle_entries(
        {
            "lifecycle:LANE_NOT_RECLAIMED:#241": {"number": 973},
            "reconcile:shelved:#304": {"number": 12},
            "reconcile:failed:deadbeef": {"number": 13},
        }
    )
    assert [entry.key for entry in entries] == ["lifecycle:LANE_NOT_RECLAIMED:#241"]
    assert entries[0].code == "LANE_NOT_RECLAIMED"
    assert entries[0].subject == "#241"
    assert entries[0].number == 973
    assert unreadable == [], "a shelved/failed key is another vocabulary, not an unreadable one"


def test_a_fingerprint_that_cannot_be_split_is_named_not_dropped():
    entries, unreadable = lifecycle_entries({"lifecycle:LANE_NOT_RECLAIMED": {"number": 1}})
    assert entries == []
    assert unreadable == ["lifecycle:LANE_NOT_RECLAIMED"]


# --- (a) the false positive is retired --------------------------------------


def test_a_finding_whose_invariant_is_gone_is_closed_and_retired(root: Path):
    filer = FakeFiler()
    closer = FakeCloser()
    rep = reporter(filer, root)

    states = recheck_findings(
        rep, root=root, apply=True, record=record(item()), closer=closer
    )

    assert [state.outcome for state in states] == [RESOLVED]
    assert closer.closed and closer.closed[0]["number"] == NUMBER
    comment = closer.closed[0]["comment"]
    assert "LANE_NOT_RECLAIMED" in comment
    assert "#241" in comment
    assert COLLECTED_AT in comment, "the resolution must carry the re-measurement it was decided on"
    assert leftover(rep) == {}, "the fingerprint is retired, so a recurrence files afresh"


def test_a_dry_run_decides_without_touching_the_board_or_the_ledger(root: Path):
    filer = FakeFiler()
    closer = FakeCloser()
    rep = reporter(filer, root)

    states = recheck_findings(rep, root=root, apply=False, record=record(item()), closer=closer)

    assert [state.outcome for state in states] == [WOULD_RESOLVE]
    assert closer.closed == []
    assert filer.comments == []
    assert KEY in leftover(rep), (
        "a dry run must not retire the fingerprint: BoardReporter.resolve saves the ledger "
        "whether or not apply is set, so a dry run that called it would suppress the finding"
    )


def test_apply_without_a_closer_is_refused_rather_than_retiring_the_fingerprint(root: Path):
    rep = reporter(FakeFiler(), root)
    with pytest.raises(ValueError):
        recheck_findings(rep, root=root, apply=True, record=record(item()))


# --- (b) the true positive still stands -------------------------------------


def test_a_finding_whose_invariant_is_still_charged_keeps_its_fingerprint(root: Path):
    filer = FakeFiler()
    closer = FakeCloser()
    rep = reporter(filer, root)
    live = {"session_id": "6d2a4bf7d4be", "worktree": "/lanes/ao-241-6d2a4bf7", "present": True}

    states = recheck_findings(
        rep, root=root, apply=True, record=record(item(lane=live)), closer=closer
    )

    assert [state.outcome for state in states] == [STILL_OWED]
    assert "still provisioned" in states[0].detail
    assert closer.closed == [], "a live violation must never be closed"
    assert KEY in leftover(rep), "a live violation keeps its fingerprint, so it stays reported"


def test_a_subject_the_audit_cannot_speak_about_is_not_read_as_clear(root: Path):
    closer = FakeCloser()
    rep = reporter(FakeFiler(), root)

    states = recheck_findings(rep, root=root, apply=True, record=record(), closer=closer)

    assert [state.outcome for state in states] == [UNMEASURED]
    assert "cannot speak about #241" in states[0].detail
    assert closer.closed == []
    assert KEY in leftover(rep)


def test_an_unreadable_board_resolves_nothing(root: Path):
    closer = FakeCloser()
    rep = reporter(FakeFiler(), root)

    states = recheck_findings(rep, root=root, apply=True, measure=lambda _root: None, closer=closer)

    assert [state.outcome for state in states] == [UNMEASURED]
    assert "could not be read" in states[0].detail
    assert closer.closed == []
    assert KEY in leftover(rep), "an unreachable board is not a clean board"


def test_the_board_read_is_bounded(root: Path):
    """A pass that can wait for ever also stops reconciling orphans (#973).

    The collection shells out to `gh`, which has no timeout of its own, and this
    pass runs every two minutes as the fleet's only scheduled reconciler — so the
    read runs as a child process under an explicit timeout. `_measure` is imported
    directly because the bound IS the property under test; a stub could not show it.
    """
    assert _measure(root, timeout=0.001) is None, "a read that runs out of budget is unmeasured"


def test_a_timed_out_read_resolves_nothing(root: Path):
    closer = FakeCloser()
    rep = reporter(FakeFiler(), root)

    states = recheck_findings(
        rep, root=root, apply=True, closer=closer, measure=lambda r: _measure(r, timeout=0.001)
    )

    assert [state.outcome for state in states] == [UNMEASURED]
    assert closer.closed == []
    assert KEY in leftover(rep), "a board this pass could not read is not a board it cleared"


def test_a_lost_board_write_keeps_the_fingerprint_for_the_next_pass(root: Path):
    rep = reporter(FakeFiler(), root)

    states = recheck_findings(
        rep, root=root, apply=True, record=record(item()), closer=FakeCloser(fail=True)
    )

    assert [state.outcome for state in states] == [FAILED]
    assert "gh issue close refused" in states[0].detail
    assert KEY in leftover(rep), "the entry is what makes the resolution retryable"


# --- the fail-open the ledger kept open -------------------------------------


def test_a_recurrence_files_again_once_the_finding_is_retired(root: Path):
    """The harm that makes this a defect rather than board tidiness (#973).

    A fingerprint that never leaves the ledger is reported as `deduped` for ever,
    so a *genuine* recurrence on the same subject is swallowed by a stale issue.
    """
    filer = FakeFiler()
    rep = reporter(filer, root)
    finding = FakeFinding(code="LANE_NOT_RECLAIMED", subject="#241")

    first = board_report_findings([finding], rep, apply=True)
    assert [report.action for report in first] == ["deduped"], "the fixture is already filed"
    assert filer.created == []

    recheck_findings(rep, root=root, apply=True, record=record(item()), closer=FakeCloser())

    again = board_report_findings([finding], rep, apply=True)
    assert [report.action for report in again] == ["filed"], (
        "after the stale finding is retired, a real recurrence must file a new issue"
    )
    assert len(filer.created) == 1


# --- the seam is free when there is nothing filed ---------------------------


def test_a_ledger_with_no_lifecycle_finding_is_a_local_read_only(root: Path):
    rep = reporter(FakeFiler(), root, key="reconcile:shelved:#304")

    def explode(_root):
        raise AssertionError("the board must not be read when no lifecycle finding is filed")

    assert recheck_findings(rep, root=root, apply=True, measure=explode) == []


# --- reconcile-owned findings reach the same terminal state (#973) ----------


def beat_failed_session(root: Path) -> None:
    from governance.reconcile.heartbeat import stamp

    stamp(
        "deadbeef0001",
        issue=304,
        agent="subagent-dead",
        root=root,
        lane="governance-reconcile",
        worktree="/lanes/ao-304-deadbeef",
        branch="issue-304",
        at=1_000_000.0,
    )


def test_a_failed_finding_is_retired_when_a_later_pass_reclaims_the_lane(root: Path):
    from governance.reconcile.heartbeat import stamp

    filer = FakeFiler()
    rep = reporter(filer, root, key="lifecycle:LANE_NOT_RECLAIMED:#241")
    beat_failed_session(root)

    sweep(root, at=1_000_000.0 + 20 * 60, apply=True, ops=FakeOps(on_main=True, fail=("remove-worktree",)), reporter=rep)
    failed_key = "reconcile:failed:deadbeef0001"
    assert failed_key in leftover(rep), "a teardown that could not finish is a finding"

    # The lane is reclaimed cleanly on a later pass: the finding about the failed
    # teardown describes nothing true any more.
    stamp("deadbeef0001", issue=304, agent="subagent-dead", root=root, at=1_000_000.0)
    sweep(root, at=1_000_000.0 + 20 * 60, apply=True, ops=FakeOps(on_main=True), reporter=rep)

    assert failed_key not in leftover(rep), "a reclaimed lane's failure finding must reach terminal"
    assert filer.comments, "the retirement is commented on the board, not silent"


def test_a_shelved_lane_still_keeps_its_work_and_its_finding(root: Path):
    """The load-bearing negative, restated for this change (#973 (b)).

    Retiring a *false* finding must not weaken the true one: an orphan whose work
    exists nowhere else still keeps its worktree, and is still reported.
    """
    filer = FakeFiler()
    rep = reporter(filer, root, key="lifecycle:LANE_NOT_RECLAIMED:#241")
    beat_failed_session(root)
    ops = FakeOps(on_main=False, remotely=False)

    report = sweep(root, at=1_000_000.0 + 20 * 60, apply=True, ops=ops, reporter=rep)

    assert report.actions[0].outcome == SHELVED_OUTCOME
    assert ops.present is True, "unmerged work is never destroyed to close a finding"
    assert "remove-worktree" not in ops.calls
    assert "reconcile:shelved:#304" in leftover(rep), "the shelved finding stays on the board"


# --- reconcile-owned suspect findings resolve against the local heartbeat (#1966) -


def suspect_reporter(filer: FakeFiler, root: Path) -> BoardReporter:
    """A reporter with an EMPTY ledger — the sweep files into it below."""
    return BoardReporter(filer, ledger=root / ".fleet" / "board-reports.json")


def test_a_suspect_finding_resolves_once_the_session_stops_beating(root: Path):
    """#1966: a `reconcile:suspect:<session>` finding reaches `resolved` when the
    session it names no longer has a heartbeat — not only through a later reclaim.

    The recheck used to re-measure only `lifecycle:` keys, so a suspect finding
    whose session simply stopped (beat cleared, never reclaimed) stayed open for
    ever. The falsification: file the finding, remove the beat, assert it resolves.
    """
    from governance.reconcile.heartbeat import clear, stamp

    filer = FakeFiler()
    rep = suspect_reporter(filer, root)
    # provoke: a session with a fresh beat behind a dead pid files a suspect finding
    stamp("deadbeef0001", issue=304, agent="subagent-dead", root=root, pid=4242, at=1_000_010.0)
    sweep(root, at=1_000_011.0, alive={"deadbeef0001": False}, apply=True, ops=FakeOps(), reporter=rep)
    assert "suspect" in filer.created[0]["title"].lower()
    assert "reconcile:suspect:deadbeef0001" in leftover(rep)

    # the session ends cleanly: the beat is gone, but the finding outlives it
    clear("deadbeef0001", root)

    closer = FakeCloser()
    states = recheck_findings(rep, root=root, apply=True, closer=closer)

    assert [state.outcome for state in states] == [RESOLVED]
    assert closer.closed and "deadbeef0001" in closer.closed[0]["comment"]
    assert "reconcile:suspect:deadbeef0001" not in leftover(rep), (
        "a resolved suspect finding is retired, so a genuine recurrence files afresh"
    )


def test_a_suspect_finding_survives_while_the_session_still_beats(root: Path):
    """The negative control: a session that still beats keeps its suspect finding.

    The recheck must not read "beat present, pid gone" as resolved — the finding
    is exactly the signal that something is wrong with that session.
    """
    from governance.reconcile.heartbeat import stamp

    filer = FakeFiler()
    rep = suspect_reporter(filer, root)
    stamp("deadbeef0001", issue=304, agent="subagent-dead", root=root, pid=4242, at=1_000_010.0)
    sweep(root, at=1_000_011.0, alive={"deadbeef0001": False}, apply=True, ops=FakeOps(), reporter=rep)
    assert "reconcile:suspect:deadbeef0001" in leftover(rep)

    closer = FakeCloser()
    states = recheck_findings(rep, root=root, apply=True, closer=closer)

    assert [state.outcome for state in states] == [STILL_OWED]
    assert closer.closed == [], "a live suspect finding must never be closed"
    assert "reconcile:suspect:deadbeef0001" in leftover(rep)


def test_counts_names_every_outcome_even_at_zero():
    assert counts([]) == {
        RESOLVED: 0,
        WOULD_RESOLVE: 0,
        STILL_OWED: 0,
        UNMEASURED: 0,
        FAILED: 0,
    }
