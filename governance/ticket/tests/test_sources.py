"""Read-only source reader tests (issue #401)."""

from __future__ import annotations

import pytest

from conftest import issue, write_attestation, write_board, write_claims, write_lessons
from model import CannotAssess, CODE_UNRESOLVED_REFERENCE, Contribution
from sources import (
    LessonIndex,
    PRODUCER_BUDGETS,
    PRODUCER_DISPATCH,
    PRODUCER_ISOLATION,
    PRODUCER_LESSONS,
    PRODUCER_SNAPSHOT,
    board_contributions,
    read_attestations,
    read_board,
    read_budgets,
    read_claims,
    read_derived,
    read_lessons,
)


def _fields(contributions):
    return {(item.ticket, item.field): item for item in contributions}


def test_board_contributes_kind_goal_and_blocked_by(root):
    write_board(
        root,
        [
            issue(10, parent=4, blocked_by=[8, 9]),
            issue(11, state="CLOSED"),
        ],
    )
    board = read_board(root)
    fields = _fields(board_contributions(board))
    assert fields[("kushin77/agent-orchestrator#10", "kind")].value == "task"
    assert fields[("kushin77/agent-orchestrator#10", "kind")].producer == PRODUCER_SNAPSHOT
    assert fields[("kushin77/agent-orchestrator#10", "goal")].value == "#4"
    assert fields[("kushin77/agent-orchestrator#10", "blocked_by")].value == ["#8", "#9"]
    assert ("kushin77/agent-orchestrator#11", "goal") not in fields


def test_a_missing_board_is_cannot_assess(root):
    with pytest.raises(CannotAssess):
        read_board(root)


def test_a_held_claim_splits_status_and_owner_across_producers(root):
    write_board(root, [issue(10)])
    write_claims(root, [{"event": "claim", "issue": 10, "agent": "session-abc", "at": "t"}])
    contributions, violations = read_claims(root, read_board(root))
    assert violations == []
    fields = _fields(contributions)
    assert fields[("kushin77/agent-orchestrator#10", "status")].producer == PRODUCER_DISPATCH
    assert fields[("kushin77/agent-orchestrator#10", "status")].value == "in-progress"
    assert fields[("kushin77/agent-orchestrator#10", "owner")].producer == PRODUCER_ISOLATION
    assert fields[("kushin77/agent-orchestrator#10", "owner")].value == "session-abc"


def test_a_released_claim_reports_in_review_and_a_closed_issue_reports_done(root):
    write_board(root, [issue(10), issue(11, state="CLOSED")])
    write_claims(
        root,
        [
            {"event": "claim", "issue": 10, "agent": "a", "at": "t"},
            {"event": "release", "issue": 10, "agent": "a", "at": "t"},
            {"event": "claim", "issue": 11, "agent": "b", "at": "t"},
        ],
    )
    contributions, _ = read_claims(root, read_board(root))
    fields = _fields(contributions)
    assert fields[("kushin77/agent-orchestrator#10", "status")].value == "in-review"
    assert fields[("kushin77/agent-orchestrator#11", "status")].value == "done"


def test_a_claim_for_an_unknown_issue_fails_naming_the_file(root):
    write_board(root, [issue(10)])
    write_claims(root, [{"event": "claim", "issue": 999999, "agent": "a", "at": "t"}])
    _, violations = read_claims(root, read_board(root))
    assert [item.code for item in violations] == [CODE_UNRESOLVED_REFERENCE]
    assert "#999999" in violations[0].subject
    assert ".board/claims/" in violations[0].where


def test_an_unresolvable_lesson_issue_origin_fails_naming_the_ledger_line(root):
    write_board(root, [issue(10)])
    write_lessons(
        root,
        [{"id": "INC-0001", "kind": "incident", "origin": {"kind": "issue", "ref": "#999999"}}],
    )
    _, violations, _, _ = read_lessons(root, read_board(root))
    assert [item.code for item in violations] == [CODE_UNRESOLVED_REFERENCE]
    assert "INC-0001" in violations[0].subject
    assert violations[0].where == "governance/lessons/ledger.jsonl:1"


def test_a_pr_origin_is_not_a_ticket_edge(root):
    write_board(root, [issue(10)])
    write_lessons(
        root,
        [
            {"id": "INC-0001", "kind": "incident", "origin": {"kind": "pr", "ref": "#153"}},
        ],
    )
    contributions, violations, warnings, _ = read_lessons(root, read_board(root))
    assert violations == []
    assert warnings == []
    tickets = {item.ticket for item in contributions}
    assert tickets == {"INC-0001"}


def test_ledger_nodes_become_typed_tickets_and_join_the_issue(root):
    write_board(root, [issue(10)])
    write_lessons(
        root,
        [
            {"id": "INC-0001", "kind": "incident", "origin": {"kind": "issue", "ref": "#10"}},
            {"id": "RCA-0001", "kind": "rca", "incident": "INC-0001"},
            {"id": "CA-0001", "kind": "corrective-action", "rca": "RCA-0001", "status": "open"},
            {"id": "SUGGEST-0001", "kind": "lesson", "rca": "RCA-0001", "class": "elite"},
        ],
    )
    contributions, violations, _, index = read_lessons(root, read_board(root))
    assert violations == []
    fields = _fields(contributions)
    assert fields[("SUGGEST-0001", "kind")].value == "suggestion"
    assert fields[("RCA-0001", "kind")].value == "rca"
    issue_facet = fields[("kushin77/agent-orchestrator#10", "facets.lessons")]
    assert issue_facet.producer == PRODUCER_LESSONS
    assert issue_facet.value == {
        "incident": "INC-0001",
        "rca": "RCA-0001",
        "corrective_actions": ["CA-0001"],
        "class": "elite",
    }
    assert fields[("CA-0001", "facets.lessons")].value["corrective_actions"] == ["CA-0001"]
    assert index.open_cas_by_issue == {10: ["CA-0001"]}


def test_two_linked_incidents_warn_and_stay_deterministic(root):
    write_board(root, [issue(10)])
    write_lessons(
        root,
        [
            {"id": "INC-0002", "kind": "incident", "origin": {"kind": "issue", "ref": "#10"}},
            {"id": "INC-0001", "kind": "incident", "origin": {"kind": "issue", "ref": "#10"}},
        ],
    )
    contributions, _, warnings, _ = read_lessons(root, read_board(root))
    assert len(warnings) == 1
    assert warnings[0].code == "lesson-join-ambiguous"
    assert "INC-0001" in warnings[0].detail and "INC-0002" in warnings[0].detail
    facet = _fields(contributions)[("kushin77/agent-orchestrator#10", "facets.lessons")]
    assert facet.value["incident"] == "INC-0001"


def test_a_duplicate_ledger_id_is_reported_not_silently_resolved(root):
    write_board(root, [issue(10)])
    write_lessons(
        root,
        [
            {"id": "INC-0001", "kind": "incident", "origin": {"kind": "issue", "ref": "#10"}},
            {"id": "INC-0001", "kind": "incident", "origin": {"kind": "issue", "ref": "#10"}},
        ],
    )
    contributions, violations, warnings, _ = read_lessons(root, read_board(root))
    assert violations == []
    assert [item.code for item in warnings] == ["ledger-duplicate-id"]
    assert warnings[0].where == "governance/lessons/ledger.jsonl:2"
    # the duplicate is skipped, not merged: one kind + one facet for INC-0001
    assert len([item for item in contributions if item.ticket == "INC-0001"]) == 2


def test_derived_raid_reads_the_priority_label_and_the_open_action(root):
    write_board(
        root,
        [issue(10, labels=["priority:P1"]), issue(11, labels=["type:task"])],
    )
    index = LessonIndex(open_cas_by_issue={10: ["CA-0007"]})
    contributions = read_derived(read_board(root), index)
    fields = _fields(contributions)
    assert fields[("kushin77/agent-orchestrator#10", "facets.raid")].value == {
        "risk": "high",
        "remediation": "CA-0007",
    }
    assert ("kushin77/agent-orchestrator#11", "facets.raid") not in fields
    assert fields[("kushin77/agent-orchestrator#10", "facets.raid")].producer == "derived"


def test_a_budget_charge_names_its_ticket_and_a_foreign_one_fails(root):
    write_board(root, [issue(10)])
    ledger = root / "telemetry" / "budgets" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        '{"ticket": "#10", "scope": {"level": "agent", "id": "paperclip"}, '
        '"spent": 1.25, "cap": 5.0, "receipt": "abc"}\n'
        '{"ticket": "#999999", "scope": {"level": "agent", "id": "x"}, "spent": 1, "cap": 2}\n',
        encoding="utf-8",
    )
    contributions, violations = read_budgets(root, read_board(root))
    fields = _fields(contributions)
    assert fields[("kushin77/agent-orchestrator#10", "facets.budget")].producer == PRODUCER_BUDGETS
    assert fields[("kushin77/agent-orchestrator#10", "facets.budget")].value == {
        "scope": {"level": "agent", "id": "paperclip"},
        "spent": 1.25,
        "cap": 5.0,
        "receipt": "abc",
    }
    assert [item.code for item in violations] == [CODE_UNRESOLVED_REFERENCE]
    assert violations[0].where == "telemetry/budgets/ledger.jsonl:2"


def test_an_attestation_for_the_lane_branch_becomes_an_evidence_receipt(root):
    write_board(root, [issue(401)])
    write_attestation(
        root,
        {"branch": "issue-401-ticket", "git_sha": "deadbeef", "result": 0, "check_count": 40},
    )
    contributions, warnings = read_attestations(root, read_board(root))
    assert warnings == []
    receipt = _fields(contributions)[("kushin77/agent-orchestrator#401", "evidence")]
    assert receipt.value == {
        "kind": "gate-run",
        "ref": "deadbeef",
        "result": "PASS",
        "checks": 40,
    }


def test_an_attestation_on_a_detached_branch_contributes_nothing(root):
    write_board(root, [issue(401)])
    write_attestation(root, {"branch": "HEAD", "git_sha": "x", "result": 0, "check_count": 1})
    contributions, warnings = read_attestations(root, read_board(root))
    assert contributions == []
    assert warnings == []


def test_an_attestation_for_an_issue_the_board_lacks_is_reported_not_fatal(root):
    # A stale committed snapshot is normal (the board is refreshed mid-verify),
    # and evidence is appended rather than authority, so this reports.
    write_board(root, [issue(10)])
    write_attestation(
        root,
        {"branch": "issue-401-ticket", "git_sha": "x", "result": 0, "check_count": 1},
    )
    contributions, warnings = read_attestations(root, read_board(root))
    assert contributions == []
    assert [item.code for item in warnings] == ["evidence-unattached"]
    assert warnings[0].subject == "#401"
    assert warnings[0].where == ".verify/attestation.json"


def test_a_malformed_attestation_is_reported_not_fatal(root, tmp_path):
    write_board(root, [issue(10)])
    path = root / ".verify" / "attestation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json\n", encoding="utf-8")
    contributions, warnings = read_attestations(root, read_board(root))
    assert contributions == []
    assert [item.code for item in warnings] == ["evidence-unreadable"]


def test_contributions_are_plain_records():
    item = Contribution("t", "status", "p", "v", "w")
    assert (item.ticket, item.field, item.producer, item.value, item.where) == (
        "t",
        "status",
        "p",
        "v",
        "w",
    )
