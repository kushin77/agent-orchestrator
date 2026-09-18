"""Ledger -> board linkage tests for governance/lessons/linkage.py (issue #1178).

Each test is a negative control over the census: it plants one record shape and
asserts the named verdict. The last block is the repository's own ledger and
board, which must be reachable and labelled rather than merely green.
"""

from __future__ import annotations

import importlib.util as _importlib_util
from pathlib import Path as _ConftestPath

import pytest

# Load this directory's own conftest by absolute path: the governance/* suites
# all ship a ``tests/conftest.py`` under the same bare module name, so a plain
# ``from conftest import ...`` would let whichever is collected last win
# (issues #699, #702, #1042).
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_lessons_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
INCIDENT_LABEL = _conftest.INCIDENT_LABEL
REPO_ROOT = _conftest.REPO_ROOT
action = _conftest.action
incident = _conftest.incident
lesson = _conftest.lesson
rca = _conftest.rca
suggestion = _conftest.suggestion

import linkage
from model import (
    CODE_BOARD_LINK_DANGLING,
    CODE_BOARD_LINK_GOAL_UNRESOLVED,
    CODE_BOARD_LINK_MISSING,
    CODE_BOARD_LINK_ORPHAN,
    CODE_BOARD_LINK_UNLABELLED,
    CODE_RECORD_INCOMPLETE,
    errors,
)


def issue(number: int, *, labels=(), parent=None, milestone="M24 - Knowledge") -> dict:
    return {
        "number": number,
        "title": "an issue",
        "state": "CLOSED",
        "milestone": milestone,
        "labels": list(labels),
        "parent": parent,
        "blocked_by": [],
    }


def board_of(*issues) -> dict:
    return {item["number"]: item for item in issues}


def codes(findings):
    return sorted({f.code for f in findings})


def only(findings, code):
    return [f for f in findings if f.code == code]


# --- reachability -----------------------------------------------------------


def test_a_record_named_by_an_issue_origin_reaches_that_issue():
    board = board_of(issue(100, labels=[INCIDENT_LABEL]))
    rows = linkage.linkage_map([incident(1)], board)
    assert rows[0].issue == 100
    assert rows[0].reachable
    assert rows[0].path == ("INC-0001", "origin:#100")


def test_a_pull_request_origin_is_not_a_board_node():
    """A PR declares provenance, but it does not make the record reachable."""
    board = board_of(issue(100, labels=[INCIDENT_LABEL]))
    record = incident(1, origin={"kind": "pr", "ref": "#100"})
    rows = linkage.linkage_map([record], board)
    assert not rows[0].reachable
    assert rows[0].origin_kind == "pr"


def test_a_lesson_reaches_the_board_through_its_rca():
    board = board_of(issue(100, labels=[INCIDENT_LABEL]))
    rows = linkage.linkage_map([incident(1), rca(1), action(1), lesson(1)], board)
    by_id = {row.id: row for row in rows}
    assert by_id["LESSON-0001"].issue == 100
    assert by_id["LESSON-0001"].path[-1] == "origin:#100"


def test_an_open_action_reaches_the_board_through_its_remediation_issue():
    board = board_of(issue(100), issue(170))
    record = action(
        1,
        rca="RCA-0001",
        status="open",
        remediation_issue="#170",
        evidence=[],
    )
    rows = linkage.linkage_map([record], board)
    assert rows[0].issue == 170
    assert rows[0].path[-1] == "remediation-issue:#170"


# --- the orphan rules -------------------------------------------------------


def test_a_lesson_that_says_nothing_about_reaching_the_board_is_refused():
    board = board_of(issue(100, labels=[INCIDENT_LABEL]))
    pull_request = {"kind": "pr", "ref": "#100"}
    records = [
        incident(1, origin=pull_request),
        rca(1, origin=pull_request),
        lesson(1),
    ]
    findings = linkage.findings(records, board, label=INCIDENT_LABEL)
    found = only(findings, CODE_BOARD_LINK_MISSING)
    assert len(found) == 1
    assert found[0].subject == "LESSON-0001"


def test_a_lesson_that_declares_itself_orphaned_is_reported_not_refused():
    board = board_of(issue(100, labels=[INCIDENT_LABEL]))
    pull_request = {"kind": "pr", "ref": "#100"}
    records = [
        incident(1, origin=pull_request),
        rca(1, origin=pull_request),
        lesson(1, orphan={"reason": "the RCA traces to a pull request"}),
    ]
    findings = linkage.findings(records, board, label=INCIDENT_LABEL)
    assert only(findings, CODE_BOARD_LINK_MISSING) == []
    found = only(findings, CODE_BOARD_LINK_ORPHAN)
    assert any(f.subject == "LESSON-0001" for f in found)
    assert all(not f.is_error for f in found)


def test_an_orphan_declaration_without_a_reason_is_refused(tmp_path):
    from checker import check_ledger, parse_ledger_text

    entry = linkage.linkage_map([lesson(1, orphan={"ticket": "#1"})], {})
    assert entry  # the map still reads the record
    text = (
        '{"id": "LESSON-0001", "kind": "lesson", "title": "t", "rca": "RCA-0001", '
        '"date": "2026-09-04", "class": "enterprise", "status": "closed", '
        '"evidence": [{"kind": "commit", "ref": "abc1234"}], "orphan": {"ticket": "#1"}}\n'
    )
    ledger = parse_ledger_text(text, tmp_path / "ledger.jsonl")
    assert CODE_RECORD_INCOMPLETE in codes(ledger.findings)


# --- dangling references ----------------------------------------------------


def test_an_incident_origin_off_the_board_is_dangling():
    """Before #1178 only an RCA's origin was resolved against the board."""
    board = board_of(issue(100, labels=[INCIDENT_LABEL]))
    findings = linkage.findings([incident(1, origin={"kind": "issue", "ref": "#999"})],
                                board, label=INCIDENT_LABEL)
    found = only(findings, CODE_BOARD_LINK_DANGLING)
    assert len(found) == 1
    assert found[0].subject == "INC-0001"
    assert found[0].is_error


def test_a_dangling_remediation_issue_is_reported():
    board = board_of(issue(100))
    record = action(1, status="open", remediation_issue="#777", evidence=[])
    findings = linkage.findings([record], board, label=INCIDENT_LABEL)
    assert only(findings, CODE_BOARD_LINK_DANGLING)


def test_an_orphan_tracking_ticket_off_the_board_is_dangling():
    board = board_of(issue(100))
    record = lesson(1, orphan={"reason": "no issue yet", "ticket": "#404"})
    findings = linkage.findings([record], board, label=INCIDENT_LABEL)
    found = only(findings, CODE_BOARD_LINK_DANGLING)
    assert found and found[0].subject == "LESSON-0001"


# --- the goal ---------------------------------------------------------------


def test_the_goal_is_the_parent_epic_when_the_issue_declares_one():
    board = board_of(issue(100, parent=42))
    rows = linkage.linkage_map([incident(1)], board)
    assert rows[0].goal == "#42"


def test_the_goal_falls_back_to_the_milestone():
    board = board_of(issue(100, milestone="M24 - Knowledge"))
    rows = linkage.linkage_map([incident(1)], board)
    assert rows[0].goal == "M24 - Knowledge"


def test_an_issue_with_no_epic_and_no_milestone_leaves_the_goal_unresolved():
    board = board_of(issue(100, milestone=""))
    findings = linkage.findings([incident(1)], board, label=INCIDENT_LABEL)
    found = only(findings, CODE_BOARD_LINK_GOAL_UNRESOLVED)
    assert len(found) == 1
    assert not found[0].is_error


# --- the label scope --------------------------------------------------------


def test_an_issue_the_ledger_names_without_the_label_is_refused():
    board = board_of(issue(100, labels=["area:board"]))
    findings = linkage.findings([incident(1)], board, label=INCIDENT_LABEL)
    found = only(findings, CODE_BOARD_LINK_UNLABELLED)
    assert len(found) == 1
    assert found[0].subject == "#100"
    assert found[0].is_error


def test_an_issue_the_ledger_names_with_the_label_is_accepted():
    board = board_of(issue(100, labels=[INCIDENT_LABEL]))
    findings = linkage.findings([incident(1)], board, label=INCIDENT_LABEL)
    assert only(findings, CODE_BOARD_LINK_UNLABELLED) == []
    assert findings == []


def test_the_label_scope_is_derived_from_the_ledger_not_from_the_label():
    """The vacuity this closes: 0 holders while the ledger names issues."""
    board = board_of(issue(100, labels=["area:board"]), issue(101, labels=[INCIDENT_LABEL]))
    assert linkage.ledger_named_issues([incident(1)], board) == [100]


# --- counts and abstention --------------------------------------------------


def test_counts_summarize_the_census():
    board = board_of(issue(100, labels=[INCIDENT_LABEL]), issue(101))
    records = [
        incident(1),
        rca(1),
        action(1),
        lesson(1),
        incident(2, origin={"kind": "event", "ref": "an-event"}),
    ]
    census = linkage.counts(records, board, label=INCIDENT_LABEL)
    assert census["records"] == 5
    assert census["with_issue"] == 4
    assert census["orphans"] == 1
    assert census["ledger_named_issues"] == 1
    assert census["ledger_named_labelled"] == 1


def test_without_a_board_there_are_no_linkage_findings():
    """A rule that guessed would be worse than one that abstains."""
    assert linkage.findings([incident(1)], None, label=INCIDENT_LABEL) == []


# --- the repository's own ledger and board ----------------------------------


def test_the_real_ledger_has_no_silent_orphan_and_no_dangling_reference():
    from checker import load_ledger, load_policy, load_snapshot

    records = list(load_ledger(REPO_ROOT / "governance/lessons/ledger.jsonl").records.values())
    board = load_snapshot(REPO_ROOT / ".board/snapshot.json")
    policy = load_policy(REPO_ROOT / "governance/lessons/policy.yaml")
    findings = linkage.findings(records, board, label=policy.incident_label)
    assert only(findings, CODE_BOARD_LINK_MISSING) == []
    assert only(findings, CODE_BOARD_LINK_DANGLING) == []


def test_the_real_ledger_names_issues_and_the_board_shows_the_label():
    from checker import load_ledger, load_policy, load_snapshot

    records = list(load_ledger(REPO_ROOT / "governance/lessons/ledger.jsonl").records.values())
    board = load_snapshot(REPO_ROOT / ".board/snapshot.json")
    policy = load_policy(REPO_ROOT / "governance/lessons/policy.yaml")
    named = linkage.ledger_named_issues(records, board)
    assert named, "the real ledger must name at least one incident-origin issue"
    for number in named:
        assert policy.incident_label in (board[number].get("labels") or [])
