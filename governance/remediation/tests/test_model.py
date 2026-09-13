"""Model tests: severity/lane/SLA mapping, dedup keys, escalation (issue #142)."""

from __future__ import annotations

from remediation_model import (
    ESCALATE_OCCURRENCE_THRESHOLD,
    LANE_GOVERNANCE,
    LANE_PLATFORM,
    LANE_QA,
    LANE_SECURITY,
    RemediationIssue,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    dedup_key,
    lane_for,
    severity_for,
    sla_hours_for,
)


def test_always_critical_codes_override_base_severity():
    assert severity_for("secret-exposure", "warning") == SEVERITY_CRITICAL
    assert severity_for("iac-mandate-unmet", "warning") == SEVERITY_CRITICAL


def test_error_maps_to_high_warning_to_medium():
    assert severity_for("class-missing", "error") == SEVERITY_HIGH
    assert severity_for("class-expectation-unmet", "warning") == SEVERITY_MEDIUM


def test_lane_routing_by_code_prefix():
    assert lane_for("secret-exposure") == LANE_SECURITY
    assert lane_for("iac-mandate-unmet") == LANE_PLATFORM
    assert lane_for("dependency-missing") == LANE_QA
    assert lane_for("class-missing") == LANE_GOVERNANCE
    assert lane_for("some-unknown-code") == LANE_GOVERNANCE


def test_sla_hours_scale_with_severity():
    assert sla_hours_for(SEVERITY_CRITICAL) < sla_hours_for(SEVERITY_HIGH)
    assert sla_hours_for(SEVERITY_HIGH) < sla_hours_for(SEVERITY_MEDIUM)


def test_dedup_key_is_stable_for_same_code_and_subject():
    assert dedup_key("class-missing", "issue-143") == dedup_key("class-missing", "issue-143")
    assert dedup_key("class-missing", "issue-143") != dedup_key("class-missing", "issue-150")


def test_dedup_key_falls_back_to_repo_when_no_subject():
    assert dedup_key("scope-mismatch", "") == "scope-mismatch:(repo)"


def _issue(**overrides):
    defaults = dict(
        key="k", code="class-missing", subject="issue-1", title="t",
        severity=SEVERITY_MEDIUM, owner_lane=LANE_GOVERNANCE, sla_hours=168,
        policy_ref="governance/conformance/policy.yaml", corrective_steps=("do it",),
        evidence=["ev"], occurrences=1, scope="repo", repo="",
    )
    defaults.update(overrides)
    return RemediationIssue(**defaults)


def test_critical_severity_always_escalates():
    assert _issue(severity=SEVERITY_CRITICAL, occurrences=1).escalate is True


def test_single_medium_occurrence_does_not_escalate():
    assert _issue(severity=SEVERITY_MEDIUM, occurrences=1).escalate is False


def test_repeated_occurrences_escalate_regardless_of_severity():
    issue = _issue(severity=SEVERITY_MEDIUM, occurrences=ESCALATE_OCCURRENCE_THRESHOLD)
    assert issue.escalate is True


def test_escalation_label_present_only_when_escalating():
    calm = _issue(severity=SEVERITY_MEDIUM, occurrences=1)
    hot = _issue(severity=SEVERITY_CRITICAL, occurrences=1)
    assert "remediation:escalated" not in calm.labels
    assert "remediation:escalated" in hot.labels


def test_body_embeds_the_dedup_key_marker_and_evidence():
    issue = _issue()
    body = issue.body()
    assert "<!-- remediation-key: k -->" in body
    assert "ev" in body
    assert "do it" in body
