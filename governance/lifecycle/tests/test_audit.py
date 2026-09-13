"""The audit: one provocation per invariant, and a quarantine that only shrinks.

Every closure invariant is broken on purpose here, and the audit is required to
name it. The quarantine tests matter as much: an excuse that can outlive its
tracking issue, or that spreads from one item to another, would turn "legacy is
grandfathered" into "nothing is ever enforced".
"""

from __future__ import annotations

from governance.lifecycle.audit import Quarantine, audit, audit_item, hygiene, in_scope

from conftest import HEAD_COMMIT, MERGE_COMMIT, clean_item, record  # noqa: E402


def codes(findings) -> set[str]:
    return {finding.code for finding in findings}


def test_a_hygienic_item_produces_no_findings():
    assert audit_item(clean_item()) == []


def test_an_unmerged_pull_request_is_a_finding():
    item = clean_item(pr={"number": 271, "state": "open", "branch": "issue-269", "head_commit": HEAD_COMMIT})
    assert "PR_NOT_MERGED" in codes(audit_item(item))


def test_no_verification_attestation_is_a_finding():
    assert "VERIFY_EVIDENCE_MISSING" in codes(audit_item(clean_item(verify={})))


def test_evidence_that_names_the_wrong_commit_is_a_finding():
    """The point of evidence is that it names the tree that was verified."""
    item = clean_item(verify={"ok": True, "commit": MERGE_COMMIT})
    findings = audit_item(item)
    assert "VERIFY_EVIDENCE_MISSING" in codes(findings)
    assert "not the verified head commit" in str(findings[0])


def test_a_surviving_branch_is_a_finding():
    """Measured on #263: the local merge command reported success and left it."""
    findings = audit_item(clean_item(branch_deleted=False))
    assert "BRANCH_NOT_DELETED" in codes(findings)
    assert "issue-269" in str(findings[0])


def test_a_live_claim_on_a_closed_item_is_a_finding():
    item = clean_item(claim={"agent": "subagent-dead", "live": True})
    findings = audit_item(item)
    assert "CLAIM_STILL_HELD" in codes(findings)
    assert "subagent-dead" in str(findings[0])


def test_a_directive_left_sent_is_a_finding():
    """A pending directive is re-executed the moment the claim frees (#263)."""
    assert "DIRECTIVE_NOT_CONSUMED" in codes(audit_item(clean_item(directive={"id": "d-269", "state": "sent"})))


def test_an_item_with_no_directive_is_not_charged_for_one():
    assert audit_item(clean_item(directive={})) == []


def test_a_lane_still_provisioned_is_a_finding():
    item = clean_item(lane={"session_id": "s-269", "present": True})
    findings = audit_item(item)
    assert "LANE_NOT_RECLAIMED" in codes(findings)
    assert "s-269" in str(findings[0])


def test_a_lane_that_is_gone_is_accepted():
    assert audit_item(clean_item(lane={"session_id": "s-269", "present": False})) == []


def test_a_close_without_evidence_is_a_finding():
    assert "CLOSING_EVIDENCE_MISSING" in codes(audit_item(clean_item(closing_evidence=False)))


def test_an_issue_left_open_is_a_finding():
    """The change landed, so the item is not finished until it is off the board."""
    item = clean_item(state="open", closing_evidence=False)
    assert "ISSUE_NOT_CLOSED" in codes(audit_item(item))


def test_a_closed_item_is_not_charged_with_being_open():
    assert audit_item(clean_item()) == []


def test_github_casing_of_state_and_pr_is_read_correctly():
    """A hygienic item whose state came back from GitHub in canonical casing."""
    item = clean_item(state="CLOSED", pr={**clean_item()["pr"], "state": "MERGED"})
    assert audit_item(item) == []


def test_an_open_milestoned_item_without_a_declaring_label_is_a_finding():
    item = clean_item(state="open", labels=["enhancement"], pr={}, verify={}, closing_evidence=False)
    findings = audit_item(item)
    assert codes(findings) == {"FILING_LABELS_MISSING"}


def test_an_open_item_with_a_declaring_label_is_accepted():
    item = clean_item(state="open", labels=["class:elite"], pr={}, verify={}, closing_evidence=False)
    assert audit_item(item) == []


def test_an_open_unmilestoned_item_is_out_of_scope():
    item = clean_item(state="open", milestone=None, labels=["enhancement"], pr={}, verify={})
    assert audit_item(item) == []


def test_the_report_states_its_scope():
    """A narrow audit must not read as a clean board."""
    report = hygiene(record(clean_item(), scope="closed items with a lane, last 30 days"))
    assert report["scope"] == "closed items with a lane, last 30 days"
    assert report["items"] == 1 and report["closed"] == 1
    assert report["hygienic"] is True


def test_a_finding_carries_its_remediation():
    report = hygiene(record(clean_item(branch_deleted=False)))
    assert report["hygienic"] is False
    finding = report["findings"][0]
    assert finding["code"] == "BRANCH_NOT_DELETED"
    assert "git push origin --delete" in finding["remediation"]


def test_findings_are_reported_per_item_not_merged_together():
    broken = clean_item(issue=300, branch_deleted=False, closing_evidence=False)
    report = hygiene(record(clean_item(), broken))
    assert {finding["subject"] for finding in report["findings"]} == {"#300"}
    assert len(report["findings"]) == 2


def test_a_pr_alone_is_not_ownership():
    """A historical item closed before the lifecycle existed has a PR, nothing else."""
    assert in_scope(closed=True) is False
    assert in_scope(closed=False) is False


def test_an_item_the_process_owns_is_in_scope():
    for owned in (
        {"lane": {"session_id": "s", "present": True}},
        {"claim": "copilot-brain"},
        {"directive": {"id": "d", "state": "done"}},
        {"journal": {"verify": {"ok": True, "commit": HEAD_COMMIT}}},
    ):
        assert in_scope(closed=True, **owned) is True


def test_open_milestoned_work_is_in_scope_for_the_filing_rule():
    assert in_scope(closed=False, milestone="M26 - Session Fleet Operating Model") is True
    assert in_scope(closed=False) is False


def test_a_closed_item_out_of_scope_never_reaches_the_audit():
    """The collector drops it, so this is about the predicate boundary."""
    assert in_scope(closed=True) is False


def test_a_quarantine_is_honoured_in_whatever_casing_the_tracker_arrives():
    """The collector returns OPEN/CLOSED; the audit must read lowercase."""
    item = clean_item(issue=253, state="open", labels=[], milestone="Portal", pr={}, verify={})
    entry = Quarantine(code="FILING_LABELS_MISSING", subject="#253", tracked_by="#174")
    report = hygiene(record(item, tracking={"#174": "OPEN"}), [entry])
    assert report["hygienic"] is True
    stale = hygiene(record(item, tracking={"#174": "CLOSED"}), [entry])
    assert [f["code"] for f in stale["findings"]] == ["QUARANTINE_STALE"]


def test_a_quarantined_item_is_excused_while_its_tracking_issue_is_open():
    item = clean_item(issue=253, state="open", labels=["enhancement"], milestone="Portal", pr={}, verify={})
    entry = Quarantine(code="FILING_LABELS_MISSING", subject="#253", tracked_by="#174")
    report = hygiene(record(item, tracking={"#174": "open"}), [entry])
    assert report["hygienic"] is True


def test_a_quarantine_leaks_to_no_other_item():
    """Otherwise one excuse would silently cover every similarly-broken item."""
    legacy = clean_item(issue=253, state="open", labels=["enhancement"], milestone="Portal", pr={}, verify={})
    fresh = clean_item(issue=400, state="open", labels=["enhancement"], milestone="M26", pr={}, verify={})
    entry = Quarantine(code="FILING_LABELS_MISSING", subject="#253", tracked_by="#174")
    report = hygiene(record(legacy, fresh, tracking={"#174": "open"}), [entry])
    assert [finding["subject"] for finding in report["findings"]] == ["#400"]


def test_a_quarantine_whose_tracking_issue_closed_becomes_a_finding():
    """The quarantine is a lease on legacy debt, not a permanent exemption."""
    item = clean_item(issue=253, state="open", labels=["enhancement"], milestone="Portal", pr={}, verify={})
    entry = Quarantine(code="FILING_LABELS_MISSING", subject="#253", tracked_by="#174")
    report = hygiene(record(item, tracking={"#174": "closed"}), [entry])
    assert [finding["code"] for finding in report["findings"]] == ["QUARANTINE_STALE"]
    assert "retire the quarantine entry" in report["findings"][0]["detail"]


def test_a_quarantine_with_an_unreachable_tracking_issue_is_a_finding():
    """An excuse whose tracker cannot be read is not an excuse."""
    item = clean_item(issue=253, state="open", labels=[], milestone="Portal", pr={}, verify={})
    entry = Quarantine(code="FILING_LABELS_MISSING", subject="#253", tracked_by="#174")
    report = hygiene(record(item), [entry])  # no tracking states recorded at all
    assert "QUARANTINE_STALE" in {finding["code"] for finding in report["findings"]}


def test_a_quarantine_naming_an_unknown_invariant_is_a_finding():
    entry = Quarantine(code="MADE_UP", subject="#253", tracked_by="#174")
    report = hygiene(record(tracking={"#174": "open"}), [entry])
    assert [finding["code"] for finding in report["findings"]] == ["QUARANTINE_STALE"]


def test_audit_over_a_record_and_over_one_item_agree():
    item = clean_item(branch_deleted=False)
    assert [str(finding) for finding in audit(record(item))] == [str(finding) for finding in audit_item(item)]


def test_every_code_the_auditor_emits_is_in_the_closed_vocabulary():
    """Totality: an emitted code the vocabulary does not define has no remediation.

    This is the check that caught ``QUARANTINE_STALE`` being invented outside the
    vocabulary, which made rendering a report raise instead of explaining itself.
    """
    from governance.lifecycle.model import INVARIANTS_BY_CODE

    provocations = [
        clean_item(issue=1, pr={"number": 1, "state": "open", "branch": "issue-1"}),
        clean_item(issue=2, verify={}),
        clean_item(issue=3, branch_deleted=False),
        clean_item(issue=4, claim={"agent": "a", "live": True}),
        clean_item(issue=5, directive={"id": "d", "state": "sent"}),
        clean_item(issue=6, lane={"session_id": "s", "present": True}),
        clean_item(issue=7, closing_evidence=False),
        clean_item(issue=8, state="open", labels=[], milestone="M26"),
        clean_item(issue=9, state="open", closing_evidence=False),
    ]
    stale = Quarantine(code="FILING_LABELS_MISSING", subject="#8", tracked_by="#174")
    findings = audit(record(*provocations, tracking={"#174": "closed"}), [stale])

    assert findings
    for finding in findings:
        assert finding.code in INVARIANTS_BY_CODE, finding.code
        assert finding.remediation  # every reporting code can say how to clear it
