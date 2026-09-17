"""Detection tests for governance/lessons/checker.py (issue #141).

Each test is a negative control: it plants exactly one defect and asserts the
named finding code appears. The last tests are the self-control: the checker
fails when it should, and the repository's own ledger passes it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from checker import (
    GitProbe,
    LedgerUnavailable,
    Policy,
    PolicyUnavailable,
    check_ledger,
    load_ledger,
    load_policy,
    load_snapshot,
    parse_ledger_text,
    relpath,
)
from conftest import (
    AREA_LABEL,
    ARTIFACT,
    INCIDENT_LABEL,
    REPO_ROOT,
    StubProbe,
    action,
    board,
    board_issue,
    incident,
    lesson,
    rca,
    suggestion,
)
from model import (
    CODE_BOARD_INCIDENT_PENDING,
    CODE_BOARD_INCIDENT_WITHOUT_RCA,
    CODE_CORRECTIVE_ACTION_OPEN,
    CODE_CORRECTIVE_ACTION_REMEDIATION_LANDED,
    CODE_CORRECTIVE_ACTION_UNLINKED,
    CODE_CORRECTIVE_ACTION_UNRECORDED,
    CODE_CORRECTIVE_ACTION_WITHOUT_EVIDENCE,
    CODE_CORRECTIVE_ACTION_WITHOUT_OWNER,
    CODE_DUPLICATE_ID,
    CODE_EVIDENCE_UNRESOLVABLE,
    CODE_INCIDENT_CLOSED_WITHOUT_LESSON,
    CODE_INCIDENT_WITHOUT_RCA,
    CODE_LEDGER_EMPTY,
    CODE_LESSON_INVALID_STATUS,
    CODE_LESSON_WITHOUT_COMMIT_EVIDENCE,
    CODE_LESSON_WITHOUT_EVIDENCE,
    CODE_ORIGIN_UNRESOLVED,
    CODE_RCA_ARTIFACT_INCOMPLETE,
    CODE_RCA_ARTIFACT_MISSING,
    CODE_RCA_ARTIFACT_UNTRACKED,
    CODE_RCA_REVIEW_OVERDUE,
    CODE_RCA_WITHOUT_CORRECTIVE_ACTION,
    CODE_RCA_WITHOUT_ORIGIN,
    CODE_SUGGESTION_OPEN,
    CODE_SUGGESTION_WITHOUT_OWNER,
    CODE_SUGGESTION_WITHOUT_REMEDIATION,
    CODE_UNKNOWN_REFERENCE,
    SEVERITY_ERROR,
    errors,
    warnings,
)


def codes(report):
    return sorted({finding.code for finding in report.findings})


def only(report, code):
    return [f for f in report.findings if f.code == code]


def clean_ledger():
    """A complete one-incident ledger, as records."""
    return [incident(1), rca(1), action(1), lesson(1)]


def test_a_complete_ledger_passes(report_factory, clean_records):
    report = report_factory(clean_records)
    assert errors(report.findings) == []
    assert report.counts["incidents"] == 1
    assert report.counts["lessons"] == 1


def test_an_empty_ledger_is_an_error_not_a_pass(report_factory):
    report = report_factory([])
    assert codes(report) == [CODE_LEDGER_EMPTY]


def test_a_missing_ledger_raises_unavailable(tmp_path):
    with pytest.raises(LedgerUnavailable):
        load_ledger(tmp_path / "nope.jsonl")


def test_duplicate_ids_are_reported(report_factory):
    report = report_factory([incident(1), incident(1), rca(1), action(1), lesson(1)])
    assert CODE_DUPLICATE_ID in codes(report)


def test_the_summary_counts_open_and_closed_work(report_factory, clean_records):
    records = clean_records + [
        incident(2, status="open"),
        rca(2, incident_id="INC-0002", corrective_actions=["CA-0002"], status="open"),
        action(2, rca_id="RCA-0002", status="open", evidence=[], remediation_issue="#9"),
        suggestion(1, rca_id="RCA-0002"),
    ]
    report = report_factory(records)
    assert report.counts["incidents"] == 2
    assert report.counts["incidents_closed"] == 1
    assert report.counts["corrective_actions_open"] == 1
    assert report.counts["suggestions"] == 1


# --- traceability -----------------------------------------------------------


def test_an_incident_without_an_rca_is_reported(report_factory):
    report = report_factory([incident(1)])
    assert codes(report) == [CODE_INCIDENT_WITHOUT_RCA]


def test_an_rca_pointing_at_an_unknown_incident_is_reported(report_factory):
    report = report_factory([rca(1, incident_id="INC-0099")])
    assert CODE_UNKNOWN_REFERENCE in codes(report)


def test_an_rca_without_an_origin_ref_is_reported(report_factory):
    report = report_factory([incident(1), rca(1, origin={"kind": "issue", "ref": ""})])
    assert CODE_RCA_WITHOUT_ORIGIN in codes(report)


def test_an_rca_whose_origin_issue_is_not_on_the_board_is_reported(report_factory):
    records = [incident(1), rca(1, origin={"kind": "issue", "ref": "#424242"})]
    report = report_factory(records, snapshot=board(board_issue(100)))
    assert CODE_ORIGIN_UNRESOLVED in codes(report)


def test_a_pull_request_origin_is_validated_by_shape(report_factory):
    records = [incident(1), rca(1, origin={"kind": "pr", "ref": "155"})]
    assert CODE_ORIGIN_UNRESOLVED in codes(report_factory(records))


def test_a_commit_origin_must_be_a_sha(report_factory):
    records = [incident(1), rca(1, origin={"kind": "commit", "ref": "pr-156"})]
    assert CODE_ORIGIN_UNRESOLVED in codes(report_factory(records))


def test_a_commit_origin_must_exist_in_history(report_factory):
    records = [incident(1), rca(1, origin={"kind": "commit", "ref": "0ba7a2c"})]
    assert CODE_EVIDENCE_UNRESOLVABLE in codes(report_factory(records))


def test_a_known_commit_origin_is_accepted(report_factory):
    records = [
        incident(1),
        rca(1, origin={"kind": "commit", "ref": "abc1234"}),
        action(1),
        lesson(1),
    ]
    assert errors(report_factory(records).findings) == []


def test_an_event_origin_is_accepted_as_free_text(report_factory):
    records = [incident(1), rca(1, origin={"kind": "event", "ref": "audit 2026-09-13"})]
    report = report_factory(records + [action(1), lesson(1)])
    assert errors(report.findings) == []


def test_an_rca_without_a_corrective_action_is_reported(report_factory):
    records = [incident(1), rca(1, corrective_actions=[])]
    assert CODE_RCA_WITHOUT_CORRECTIVE_ACTION in codes(report_factory(records))


def test_a_corrective_action_that_is_not_recorded_is_reported(report_factory):
    records = [incident(1), rca(1, corrective_actions=["CA-0007"])]
    assert CODE_CORRECTIVE_ACTION_UNRECORDED in codes(report_factory(records))


def test_a_recorded_action_no_rca_claims_is_reported(report_factory):
    records = [incident(1), rca(1), action(1, rca_id="RCA-0001", id="CA-0002")]
    assert CODE_CORRECTIVE_ACTION_UNLINKED in codes(report_factory(records))


def test_an_action_naming_an_unknown_rca_is_reported(report_factory):
    records = [incident(1), rca(1), action(1, rca_id="RCA-0099")]
    assert CODE_UNKNOWN_REFERENCE in codes(report_factory(records))


def test_a_lesson_citing_an_unknown_rca_is_reported(report_factory):
    records = [incident(1), rca(1), action(1), lesson(1, rca_id="RCA-0099")]
    assert CODE_UNKNOWN_REFERENCE in codes(report_factory(records))


def test_a_closed_incident_without_a_lesson_is_reported(report_factory):
    records = [incident(1, status="closed"), rca(1), action(1)]
    assert CODE_INCIDENT_CLOSED_WITHOUT_LESSON in codes(report_factory(records))


def test_an_open_incident_without_a_lesson_is_allowed(report_factory):
    records = [
        incident(1, status="open"),
        rca(1, status="open"),
        action(1, status="open", evidence=[], remediation_issue="#9"),
    ]
    report = report_factory(records)
    assert CODE_INCIDENT_CLOSED_WITHOUT_LESSON not in codes(report)


# --- artifacts --------------------------------------------------------------


def test_a_missing_rca_artifact_is_reported(report_factory, root):
    (root / ARTIFACT).unlink()
    records = [incident(1), rca(1), action(1), lesson(1)]
    assert CODE_RCA_ARTIFACT_MISSING in codes(report_factory(records))


def test_an_untracked_rca_artifact_is_reported(report_factory, clean_records):
    probe = StubProbe(tracked={ARTIFACT: False}, commits={"abc1234"})
    report = report_factory(clean_records, probe=probe)
    assert CODE_RCA_ARTIFACT_UNTRACKED in codes(report)


def test_an_rca_artifact_missing_a_section_is_reported(report_factory, root):
    (root / ARTIFACT).write_text("# RCA-0001 — sample\n\n## Root cause\n\nx\n",
                                 encoding="utf-8")
    records = [incident(1), rca(1), action(1), lesson(1)]
    report = report_factory(records)
    assert CODE_RCA_ARTIFACT_INCOMPLETE in codes(report)
    assert "## Lessons" in only(report, CODE_RCA_ARTIFACT_INCOMPLETE)[0].message


def test_the_artifact_path_is_normalised(report_factory, root):
    records = [incident(1), rca(1, artifact="./" + ARTIFACT), action(1), lesson(1)]
    assert errors(report_factory(records).findings) == []
    assert relpath("./governance/x.md") == "governance/x.md"
    assert relpath(".board/snapshot.json") == ".board/snapshot.json"


# --- evidence ---------------------------------------------------------------


def test_a_lesson_without_evidence_is_reported(report_factory):
    records = [
        incident(1),
        rca(1),
        action(1),
        lesson(1, evidence=[]),
    ]
    assert CODE_LESSON_WITHOUT_EVIDENCE in codes(report_factory(records))


def test_a_lesson_proven_only_by_an_issue_is_reported(report_factory):
    records = [
        incident(1),
        rca(1),
        action(1),
        lesson(1, evidence=[{"kind": "issue", "ref": "#100"}]),
    ]
    assert CODE_LESSON_WITHOUT_COMMIT_EVIDENCE in codes(report_factory(records))


def test_a_lesson_that_is_not_closed_is_reported(report_factory):
    records = [incident(1), rca(1), action(1), lesson(1, status="open")]
    assert CODE_LESSON_INVALID_STATUS in codes(report_factory(records))


def test_a_closed_suggestion_is_reported(report_factory):
    records = [incident(1), rca(1), action(1), suggestion(1, status="closed")]
    assert CODE_LESSON_INVALID_STATUS in codes(report_factory(records))


def test_a_suggestion_without_a_remediation_is_reported(report_factory):
    records = [incident(1), rca(1), action(1), suggestion(1, remediation="")]
    assert CODE_SUGGESTION_WITHOUT_REMEDIATION in codes(report_factory(records))


def test_a_suggestion_without_an_owner_is_reported(report_factory):
    records = [incident(1), rca(1), action(1), suggestion(1, owner="")]
    assert CODE_SUGGESTION_WITHOUT_OWNER in codes(report_factory(records))


def test_an_open_suggestion_is_a_tracked_deviation(report_factory):
    records = [incident(1), rca(1), action(1), suggestion(1)]
    report = report_factory(records)
    assert CODE_SUGGESTION_OPEN in codes(report)
    assert only(report, CODE_SUGGESTION_OPEN)[0].severity != SEVERITY_ERROR


def test_a_closed_action_without_evidence_is_reported(report_factory):
    records = [incident(1), rca(1), action(1, evidence=[]), lesson(1)]
    assert CODE_CORRECTIVE_ACTION_WITHOUT_EVIDENCE in codes(report_factory(records))


def test_an_open_action_without_a_remediation_issue_is_reported(report_factory):
    records = [
        incident(1, status="open"),
        rca(1, status="open"),
        action(1, status="open", evidence=[]),
        lesson(1),
    ]
    assert CODE_CORRECTIVE_ACTION_WITHOUT_OWNER in codes(report_factory(records))


def test_an_open_action_names_its_remediation_issue(report_factory):
    records = [
        incident(1, status="open"),
        rca(1, status="open"),
        action(1, status="open", evidence=[], remediation_issue="#170"),
        lesson(1),
    ]
    report = report_factory(records)
    finding = only(report, CODE_CORRECTIVE_ACTION_OPEN)[0]
    assert "#170" in finding.message
    assert errors(report.findings) == []


def _open_action_with_remediation(remediation="#170"):
    """One complete ledger whose single action is still open (#1028)."""
    return [
        incident(1, status="open"),
        rca(1, status="open"),
        action(1, status="open", evidence=[], remediation_issue=remediation),
        lesson(1),
    ]


def _board_with(*issues):
    """The fixture board under test, always carrying the default origin #100.

    ``labels=[]`` on purpose: this rule is about an action's *remediation* issue,
    so the record-label rule must not be what fires.
    """
    return board(*issues)


def test_an_open_action_whose_remediation_landed_is_an_error(report_factory):
    """#1028: the record states its own closure condition; the board says it is met."""
    report = report_factory(
        _open_action_with_remediation(),
        snapshot=_board_with(
            board_issue(100, state="OPEN", labels=[]),
            board_issue(170, state="CLOSED", labels=[]),
        ),
    )
    finding = only(report, CODE_CORRECTIVE_ACTION_REMEDIATION_LANDED)[0]
    assert finding.subject == "CA-0001"
    assert "#170" in finding.message
    assert "CLOSED" in finding.message
    assert errors(report.findings) != []
    # one finding for the action: restating the contradiction, softly, is not a
    # second observation
    assert only(report, CODE_CORRECTIVE_ACTION_OPEN) == []


def test_an_open_action_whose_remediation_is_open_stays_a_deviation(report_factory):
    """The accepted half: in-flight work is a deviation, never an error (#1028)."""
    report = report_factory(
        _open_action_with_remediation(),
        snapshot=_board_with(
            board_issue(100, state="OPEN", labels=[]),
            board_issue(170, state="OPEN", labels=[]),
        ),
    )
    assert only(report, CODE_CORRECTIVE_ACTION_REMEDIATION_LANDED) == []
    assert len(only(report, CODE_CORRECTIVE_ACTION_OPEN)) == 1
    assert errors(report.findings) == []


def test_an_open_action_off_the_snapshot_is_not_evidence_it_landed(report_factory):
    """An issue the point-in-time snapshot does not carry proves nothing (#1028)."""
    report = report_factory(
        _open_action_with_remediation(),
        snapshot=_board_with(board_issue(100, state="OPEN", labels=[])),
    )
    assert only(report, CODE_CORRECTIVE_ACTION_REMEDIATION_LANDED) == []
    assert len(only(report, CODE_CORRECTIVE_ACTION_OPEN)) == 1
    assert errors(report.findings) == []


def test_a_commit_that_is_not_in_history_is_reported(report_factory):
    probe = StubProbe(commits={"abc1234"})
    records = [
        incident(1),
        rca(1),
        action(1, evidence=[{"kind": "commit", "ref": "deadbee"}]),
        lesson(1, evidence=[{"kind": "commit", "ref": "deadbee"}]),
    ]
    report = report_factory(records, probe=probe)
    assert len(only(report, CODE_EVIDENCE_UNRESOLVABLE)) == 2
    assert errors(report.findings) != []


def test_a_shallow_clone_downgrades_unresolvable_evidence(report_factory):
    probe = StubProbe(shallow=True, commits=set())
    records = [
        incident(1),
        rca(1),
        action(1, evidence=[{"kind": "commit", "ref": "deadbee"}]),
        lesson(1, evidence=[{"kind": "commit", "ref": "deadbee"}]),
    ]
    report = report_factory(records, probe=probe)
    assert only(report, CODE_EVIDENCE_UNRESOLVABLE)
    assert errors(report.findings) == []
    assert len(warnings(report.findings)) == 2


def test_a_commit_evidence_that_is_not_a_sha_is_reported(report_factory):
    records = [
        incident(1),
        rca(1),
        action(1, evidence=[{"kind": "commit", "ref": "version-2"}]),
        lesson(1),
    ]
    assert CODE_EVIDENCE_UNRESOLVABLE in codes(report_factory(records))


def test_missing_artifact_evidence_is_reported(report_factory, clean_records):
    records = clean_records + [
        suggestion(1, evidence=[{"kind": "artifact", "ref": "docs/absent.md"}]),
    ]
    report = report_factory(records)
    assert CODE_EVIDENCE_UNRESOLVABLE in codes(report)


def test_artifact_evidence_that_exists_is_accepted(report_factory, clean_records):
    records = clean_records + [
        suggestion(1, evidence=[{"kind": "artifact", "ref": ARTIFACT}]),
    ]
    report = report_factory(records)
    assert CODE_EVIDENCE_UNRESOLVABLE not in codes(report)


def test_evidence_with_a_malformed_issue_ref_is_reported(report_factory):
    records = [
        incident(1),
        rca(1),
        action(1, evidence=[{"kind": "issue", "ref": "issue one"}]),
        lesson(1),
    ]
    assert CODE_EVIDENCE_UNRESOLVABLE in codes(report_factory(records))


# --- review cadence ---------------------------------------------------------


def test_an_overdue_review_is_a_deviation(report_factory, clean_records):
    report = report_factory(clean_records, today=date(2027, 9, 15))
    finding = only(report, CODE_RCA_REVIEW_OVERDUE)[0]
    assert "re-review" in finding.remediation
    assert errors(report.findings) == []


def test_a_recent_review_is_not_reported(report_factory, clean_records):
    report = report_factory(clean_records, today=date(2026, 9, 15))
    assert CODE_RCA_REVIEW_OVERDUE not in codes(report)


# --- board coverage: a RECORD label, never an area label (issue #766) -------


def test_a_closed_record_labelled_issue_with_no_ledger_record_is_reported(
    report_factory,
):
    snapshot = board(board_issue(100, state="OPEN", labels=["area:board"]),
                     board_issue(900, state="CLOSED"))
    report = report_factory(clean_ledger(), snapshot=snapshot)
    finding = only(report, CODE_BOARD_INCIDENT_WITHOUT_RCA)[0]
    assert finding.subject == "#900"
    assert INCIDENT_LABEL in finding.message
    assert "no incident record in the ledger" in finding.message
    assert report.counts["board_incidents_scanned"] == 1


def test_an_area_labelled_issue_is_not_an_incident_record(report_factory):
    """The #766 regression: #141/#494/#495/#497 hold the AREA label, not a record.

    All four are work items with no incident to record, so none of them may be
    a finding — and none may need a hand-written exemption to say so.
    """
    snapshot = board(
        board_issue(100, state="OPEN", labels=["area:board"]),
        {**board_issue(141, state="CLOSED"), "labels": [AREA_LABEL, "area:lessons"]},
        {**board_issue(494, state="CLOSED"), "labels": [AREA_LABEL]},
        {**board_issue(495, state="CLOSED"), "labels": [AREA_LABEL]},
        {**board_issue(497, state="CLOSED"), "labels": [AREA_LABEL]},
    )
    report = report_factory(clean_ledger(), snapshot=snapshot)
    assert report.counts["board_incidents_scanned"] == 0
    assert [f for f in report.findings if f.code.startswith("board-incident")] == []
    assert errors(report.findings) == []


def test_an_open_record_labelled_issue_is_a_deviation(report_factory, clean_records):
    snapshot = board(
        board_issue(100, state="OPEN", labels=["area:board"]),
        board_issue(900, state="OPEN"),
    )
    report = report_factory(clean_records, snapshot=snapshot)
    finding = only(report, CODE_BOARD_INCIDENT_PENDING)[0]
    assert finding.subject == "#900"
    assert errors(report.findings) == []


def test_a_board_issue_traced_by_a_ledger_incident_is_not_reported(report_factory):
    """The detector reads the RECORD: a label backed by an `INC-*` line is fine."""
    snapshot = board(board_issue(100, state="CLOSED"), board_issue(900, state="OPEN"))
    report = report_factory([incident(1)], snapshot=snapshot)
    assert CODE_BOARD_INCIDENT_WITHOUT_RCA not in codes(report)
    assert only(report, CODE_BOARD_INCIDENT_PENDING)[0].subject == "#900"


def test_a_board_issue_without_the_record_label_is_out_of_scope(report_factory):
    snapshot = board({**board_issue(900), "labels": ["area:board"]})
    report = report_factory([incident(1)], snapshot=snapshot)
    assert report.counts["board_incidents_scanned"] == 0
    assert CODE_BOARD_INCIDENT_WITHOUT_RCA not in codes(report)
    assert CODE_BOARD_INCIDENT_PENDING not in codes(report)


# --- the policy: the label is the scope, and there are no exemptions --------


def test_the_shipped_policy_names_the_record_label_and_no_exemptions():
    policy = load_policy(REPO_ROOT / "governance/lessons/policy.yaml")
    assert policy.incident_label == INCIDENT_LABEL
    assert not policy.incident_label.startswith("area:")
    assert not hasattr(policy, "exemptions")
    assert policy.review_cadence_days == 180


def test_the_snapshot_today_is_green_with_the_shipped_policy():
    """The real board plus the real policy must not fail the gate — no exemption.

    The four issues that used to carry a hand-written exemption are named: each
    is a work item in the incident-response *area*, so none is a finding.
    """
    probe = GitProbe(REPO_ROOT)
    if not probe.available:
        pytest.skip("not a git work tree")
    ledger = load_ledger(REPO_ROOT / "governance/lessons/ledger.jsonl")
    snapshot = load_snapshot(REPO_ROOT / ".board/snapshot.json")
    policy = load_policy(REPO_ROOT / "governance/lessons/policy.yaml")
    report = check_ledger(
        ledger,
        root=REPO_ROOT,
        snapshot=snapshot,
        policy=policy,
        today=date(2026, 9, 15),
        git=probe,
    )
    assert errors(report.findings) == []
    labelled = [
        number
        for number, issue in snapshot.items()
        if policy.incident_label in (issue.get("labels") or [])
    ]
    assert report.counts["board_incidents_scanned"] == len(labelled)
    subjects = {finding.subject for finding in report.findings}
    for ref in ("#141", "#494", "#495", "#497"):
        assert ref not in subjects


def test_a_malformed_policy_cannot_be_loaded(tmp_path):
    broken = tmp_path / "policy.yaml"
    broken.write_text("schema: [unclosed\n", encoding="utf-8")
    with pytest.raises(PolicyUnavailable):
        load_policy(broken)


def test_a_policy_that_declares_exemptions_is_refused(tmp_path):
    """The retired by-issue exemptions cannot come back as a YAML edit (#766)."""
    path = tmp_path / "policy.yaml"
    path.write_text(
        "board:\n"
        "  incident_label: incident\n"
        "  exemptions:\n"
        "    - ref: '#141'\n"
        "      reason: the retired hand-written exemption\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicyUnavailable) as refused:
        load_policy(path)
    assert "exemptions" in str(refused.value)


def test_a_policy_with_an_area_label_is_refused_by_name(tmp_path):
    """An area cannot manufacture an incident: the loader refuses it (#766)."""
    path = tmp_path / "policy.yaml"
    path.write_text(
        "board:\n  incident_label: area:incident-response\n", encoding="utf-8"
    )
    with pytest.raises(PolicyUnavailable) as refused:
        load_policy(path)
    assert "AREA label" in str(refused.value)
    assert "area:incident-response" in str(refused.value)


def test_a_policy_with_a_bad_cadence_is_rejected(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text("review_cadence_days: soon\n", encoding="utf-8")
    with pytest.raises(PolicyUnavailable):
        load_policy(path)


def test_a_missing_policy_raises_unavailable(tmp_path):
    with pytest.raises(PolicyUnavailable):
        load_policy(tmp_path / "absent.yaml")


def test_the_policy_cadence_drives_the_review_check(report_factory, clean_records):
    tight = Policy(review_cadence_days=1)
    report = report_factory(clean_records, policy=tight, today=date(2026, 9, 20))
    assert CODE_RCA_REVIEW_OVERDUE in codes(report)


# --- strict escalation and the self-control ---------------------------------


def test_strict_escalates_deviations_to_errors(report_factory, clean_records):
    records = clean_records + [suggestion(1)]
    soft = report_factory(records)
    assert errors(soft.findings) == []
    strict = report_factory(records, strict=True)
    assert errors(strict.findings) != []
    assert warnings(strict.findings) == []


def test_breaking_a_required_link_turns_the_gate_red(report_factory, clean_records):
    """Self-control: the checker must fail when a required link is removed."""
    assert errors(report_factory(clean_records).findings) == []
    broken = [
        incident(1),
        rca(1, corrective_actions=["CA-0099"]),
        action(1),
        lesson(1),
    ]
    assert CODE_CORRECTIVE_ACTION_UNRECORDED in codes(report_factory(broken))


def test_the_repositorys_own_ledger_is_green():
    """The shipped ledger must satisfy the gate it installs (real data).

    The gate of record (``cli.py check``) loads ``policy.yaml`` before it
    checks, so this self-control mirrors it instead of using the empty default
    policy. There is no exemption path: #141/#494/#495/#497 are work items in
    the incident-response *area*, and the RECORD label keeps them out of scope
    by construction (issue #766).
    """
    probe = GitProbe(REPO_ROOT)
    if not probe.available:
        pytest.skip("not a git work tree; tracked-ness cannot be assessed")
    ledger = load_ledger(REPO_ROOT / "governance/lessons/ledger.jsonl")
    snapshot = load_snapshot(REPO_ROOT / ".board/snapshot.json")
    policy = load_policy(REPO_ROOT / "governance/lessons/policy.yaml")
    report = check_ledger(
        ledger,
        root=REPO_ROOT,
        snapshot=snapshot,
        policy=policy,
        today=date(2026, 9, 15),
        git=probe,
    )
    assert errors(report.findings) == []
    assert not [f for f in report.findings if f.code.startswith("board-incident")]
    assert report.counts["incidents"] >= 5
    assert report.counts["incidents_closed"] >= 4
    assert report.counts["rcas"] >= 5
    assert report.counts["lessons"] >= 4


def test_the_repositorys_ledger_documents_a_closed_incident_with_an_action():
    """DoD: at least one closed incident with a linked corrective action."""
    ledger = load_ledger(REPO_ROOT / "governance/lessons/ledger.jsonl")
    closed = {
        record["id"] for record in ledger.of_kind("incident")
        if record.get("status") == "closed"
    }
    assert closed
    linked = [
        record for record in ledger.of_kind("rca")
        if record.get("incident") in closed and record.get("corrective_actions")
    ]
    assert linked
    for record in linked:
        for action_id in record["corrective_actions"]:
            assert action_id in ledger.records


def test_git_probe_answers_about_this_repository():
    probe = GitProbe(REPO_ROOT)
    if not probe.available:
        pytest.skip("not a git work tree")
    assert probe.tracked("governance/lessons/ledger.jsonl") is True
    assert probe.tracked("governance/lessons/absent-file.jsonl") is False
    assert probe.shallow in (True, False)


def test_git_probe_reports_unavailable_outside_a_work_tree(tmp_path):
    probe = GitProbe(tmp_path)
    assert probe.available is False
    assert probe.tracked("anything") is None
    assert probe.commit_exists("abc1234") is None
    assert probe.shallow is False


def test_report_is_written_as_json(report_factory, tmp_path, clean_records):
    from checker import write_report

    report = report_factory(clean_records)
    path = write_report(report, tmp_path / "out" / "lessons-report.json")
    assert path.is_file()
    assert '"errors": []' in path.read_text(encoding="utf-8")


def test_ledger_parsing_keeps_file_order():
    ledger = parse_ledger_text(
        "\n".join(
            [
                '{"id": "INC-0001", "kind": "incident"}',
                '{"id": "RCA-0001", "kind": "rca"}',
            ]
        ),
        Path("ledger.jsonl"),
    )
    assert [entry.line for entry in ledger.entries] == [1, 2]
    assert [entry.id for entry in ledger.entries] == ["INC-0001", "RCA-0001"]
