"""Violation -> remediation-issue payload construction, dedup/merge (issue #142)."""

from __future__ import annotations

from conftest import FakeFinding

from generator import build_issue, generate, merge
from model import SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM


def test_build_issue_carries_title_labels_summary_lane_steps_policy_evidence():
    finding = FakeFinding(
        code="class-missing",
        message="issue #143 declares no class",
        severity="error",
        subject="issue-143",
        remediation="add a `class:<rung>` label",
    )
    issue = build_issue(finding, policy_ref="governance/conformance/policy.yaml")

    assert issue.code == "class-missing"
    assert issue.subject == "issue-143"
    assert "class-missing" in issue.title
    assert issue.severity == SEVERITY_HIGH  # error -> high
    assert issue.owner_lane == "governance"
    assert issue.policy_ref == "governance/conformance/policy.yaml"
    assert issue.corrective_steps == ("add a `class:<rung>` label",)
    assert issue.evidence == ["issue #143 declares no class"]
    assert "remediation:auto" in issue.labels


def test_build_issue_falls_back_to_generic_steps_when_finding_has_none():
    finding = FakeFinding(code="scope-mismatch", message="m", severity="warning")
    issue = build_issue(finding)
    assert issue.corrective_steps  # non-empty generic fallback
    assert issue.corrective_steps[0] != ""


def test_merge_collapses_same_dedup_key_and_accumulates_occurrences_and_evidence():
    finding = FakeFinding(
        code="class-expectation-unmet", message="first sighting", severity="warning",
        subject="issue-150",
    )
    same_finding_again = FakeFinding(
        code="class-expectation-unmet", message="second sighting", severity="warning",
        subject="issue-150",
    )
    issues = [build_issue(finding), build_issue(same_finding_again)]
    merged = merge(issues)

    assert len(merged) == 1
    assert merged[0].occurrences == 2
    assert "first sighting" in merged[0].evidence
    assert "second sighting" in merged[0].evidence


def test_merge_keeps_distinct_subjects_as_distinct_issues():
    a = build_issue(FakeFinding(code="class-missing", message="a", subject="issue-1"))
    b = build_issue(FakeFinding(code="class-missing", message="b", subject="issue-2"))
    merged = merge([a, b])
    assert len(merged) == 2


def test_merge_ratchets_severity_up_not_down():
    warn = build_issue(
        FakeFinding(code="class-expectation-unmet", message="w", severity="warning", subject="x")
    )
    hot = build_issue(
        FakeFinding(code="class-expectation-unmet", message="h", severity="error", subject="x")
    )
    merged = merge([warn, hot])
    assert merged[0].severity == SEVERITY_HIGH

    # Order reversed: the error arriving first must not be downgraded by a
    # later warning re-observation of the same violation.
    merged_reverse = merge([hot, warn])
    assert merged_reverse[0].severity == SEVERITY_HIGH


def test_repeated_findings_escalate_via_occurrence_threshold():
    findings = [
        FakeFinding(code="class-expectation-unmet", message="m%d" % i, severity="warning", subject="x")
        for i in range(3)
    ]
    merged = merge([build_issue(f) for f in findings])
    assert merged[0].severity == SEVERITY_MEDIUM
    assert merged[0].escalate is True  # occurrence threshold, not severity


def test_generate_is_the_full_pipeline_findings_to_deduped_issues():
    findings = [
        FakeFinding(code="secret-exposure", message="token committed", severity="error", subject="path/x"),
        FakeFinding(code="secret-exposure", message="token still there", severity="error", subject="path/x"),
        FakeFinding(code="class-missing", message="no class", severity="error", subject="issue-9"),
    ]
    issues = generate(findings, repo="kushin77/agent-orchestrator")

    assert len(issues) == 2
    by_code = {i.code: i for i in issues}
    assert by_code["secret-exposure"].occurrences == 2
    assert by_code["secret-exposure"].severity == SEVERITY_CRITICAL
    assert by_code["secret-exposure"].escalate is True
    assert by_code["class-missing"].occurrences == 1
    assert by_code["secret-exposure"].repo == "kushin77/agent-orchestrator"
