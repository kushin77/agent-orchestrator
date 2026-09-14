"""Projection builder tests — determinism, authority, coverage (issue #401)."""

from __future__ import annotations

from conftest import issue, write_attestation, write_board, write_claims, write_lessons
from builder import build
from model import (
    CODE_BUDGET_RECEIPT_UNBACKED,
    CODE_NON_DETERMINISTIC,
    CODE_PRODUCER_MISMATCH,
    CODE_SCHEMA_ENUM,
    CODE_TICKET_UNBACKED,
    CODE_TWO_WRITERS,
    CODE_UNKNOWN_FIELD,
    CODE_VALUE_CONFLICT,
    Contribution,
    canonical,
)

TICKET_10 = "kushin77/agent-orchestrator#10"


def _small_root(root):
    write_board(root, [issue(10, parent=4, blocked_by=[9], labels=["priority:P2"])])
    return root


def test_two_builds_over_one_revision_are_byte_identical(root):
    _small_root(root)
    first = build(root)
    second = build(root)
    assert first.ok and second.ok
    assert first.text == second.text
    assert first.sha256 == second.sha256


def test_a_stamp_makes_two_builds_differ(root):
    _small_root(root)
    first = build(root, stamp="1")
    second = build(root, stamp="2")
    assert first.text != second.text
    assert first.document["generated_at"] == "1"
    assert "generated_at" not in build(root).document


def test_authority_carries_exactly_the_populated_tracked_fields(root):
    _small_root(root)
    projection = build(root)
    ticket = projection.tickets[TICKET_10]
    assert ticket["goal"] == "#4"
    assert ticket["blocked_by"] == ["#9"]
    assert ticket["authority"] == {
        "goal": ".board/snapshot.json",
        "blocked_by": ".board/snapshot.json",
        "facets.raid": "derived",
    }
    assert "id" not in ticket["authority"]
    assert "kind" not in ticket["authority"]


def test_ownership_joins_the_claim_ledger_onto_the_ticket(root):
    _small_root(root)
    write_claims(root, [{"event": "claim", "issue": 10, "agent": "session-xyz", "at": "t"}])
    ticket = build(root).tickets[TICKET_10]
    assert ticket["owner"] == "session-xyz"
    assert ticket["status"] == "in-progress"
    assert ticket["authority"]["owner"] == "governance/isolation"
    assert ticket["authority"]["status"] == "governance/dispatch"


def test_two_writers_for_one_field_fail_and_name_both(root):
    _small_root(root)
    extra = [
        Contribution(
            TICKET_10, "goal", "rogue/lane", "#999", "negative-control"
        )
    ]
    projection = build(root, extra=extra)
    codes = [item.code for item in projection.violations]
    assert CODE_TWO_WRITERS in codes
    finding = next(item for item in projection.violations if item.code == CODE_TWO_WRITERS)
    assert finding.subject == f"{TICKET_10}.goal"
    assert "rogue/lane" in finding.detail
    assert ".board/snapshot.json" in finding.detail


def test_a_writer_that_is_not_the_authority_producer_fails(root):
    _small_root(root)
    extra = [Contribution(TICKET_10, "owner", "governance/dispatch", "a", "negative-control")]
    projection = build(root, extra=extra)
    finding = next(item for item in projection.violations if item.code == CODE_PRODUCER_MISMATCH)
    assert finding.subject == f"{TICKET_10}.owner"
    assert "governance/isolation" in finding.detail


def test_a_field_outside_the_frozen_contract_fails(root):
    _small_root(root)
    extra = [Contribution(TICKET_10, "facets.approvals", "rogue/lane", {"x": 1}, "nc")]
    projection = build(root, extra=extra)
    finding = next(item for item in projection.violations if item.code == CODE_UNKNOWN_FIELD)
    assert finding.subject == f"{TICKET_10}.facets.approvals"


def test_an_empty_value_is_not_a_writer(root):
    _small_root(root)
    extra = [Contribution(TICKET_10, "goal", "rogue/lane", "", "negative-control")]
    projection = build(root, extra=extra)
    assert projection.ok
    assert projection.tickets[TICKET_10]["goal"] == "#4"
    assert projection.tickets[TICKET_10]["authority"]["goal"] == ".board/snapshot.json"


def test_a_ticket_whose_only_contribution_is_empty_is_unbacked(root):
    _small_root(root)
    extra = [Contribution("#999999", "goal", "rogue/lane", "", "negative-control")]
    projection = build(root, extra=extra)
    finding = next(item for item in projection.violations if item.code == CODE_TICKET_UNBACKED)
    assert finding.subject == "#999999"


def test_a_ticket_no_ledger_backs_fails_and_names_it(root):
    _small_root(root)
    extra = [Contribution("#999999", None, "rogue/lane", None, "negative-control")]
    projection = build(root, extra=extra)
    finding = next(item for item in projection.violations if item.code == CODE_TICKET_UNBACKED)
    assert finding.subject == "#999999"


def test_one_producer_supplying_conflicting_values_fails(root):
    _small_root(root)
    extra = [
        Contribution(TICKET_10, "status", "governance/dispatch", "in-progress", "nc"),
        Contribution(TICKET_10, "status", "governance/dispatch", "done", "nc"),
    ]
    projection = build(root, extra=extra)
    finding = next(item for item in projection.violations if item.code == CODE_VALUE_CONFLICT)
    assert finding.subject == f"{TICKET_10}.status"


def test_a_value_outside_the_closed_enum_fails(root):
    _small_root(root)
    extra = [Contribution(TICKET_10, "status", "governance/dispatch", "nonsense", "nc")]
    projection = build(root, extra=extra)
    finding = next(item for item in projection.violations if item.code == CODE_SCHEMA_ENUM)
    assert finding.subject == f"{TICKET_10}.status"


def test_a_budget_receipt_must_equal_an_evidence_receipt_on_the_ticket(root):
    _small_root(root)
    ledger = root / "telemetry" / "budgets" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        '{"ticket": "#10", "scope": {"level": "agent", "id": "p"}, '
        '"spent": 1, "cap": 2, "receipt": "ran-backing-sha"}\n',
        encoding="utf-8",
    )
    unbacked = build(root)
    finding = next(
        item for item in unbacked.violations if item.code == CODE_BUDGET_RECEIPT_UNBACKED
    )
    assert finding.subject == TICKET_10

    write_attestation(
        root,
        {
            "branch": "issue-401-ticket",
            "git_sha": "ran-backing-sha",
            "result": 0,
            "check_count": 1,
        },
    )
    # the attestation names #401, not #10, so the charge is still unbacked
    still = build(root)
    assert any(item.code == CODE_BUDGET_RECEIPT_UNBACKED for item in still.violations)


def test_the_lessons_register_joins_the_issue_and_its_nodes(root):
    _small_root(root)
    write_lessons(
        root,
        [
            {"id": "INC-0001", "kind": "incident", "origin": {"kind": "issue", "ref": "#10"}},
            {"id": "RCA-0001", "kind": "rca", "incident": "INC-0001"},
        ],
    )
    projection = build(root)
    assert projection.ok
    ticket = projection.tickets[TICKET_10]
    assert ticket["facets"]["lessons"] == {
        "incident": "INC-0001",
        "rca": "RCA-0001",
    }
    assert ticket["authority"]["facets.lessons"] == "governance/lessons"
    assert projection.tickets["RCA-0001"]["kind"] == "rca"


def test_an_ok_build_reports_no_determinism_finding(root):
    _small_root(root)
    projection = build(root)
    assert canonical(projection.document) == projection.text
    assert not any(item.code == CODE_NON_DETERMINISTIC for item in projection.violations)
