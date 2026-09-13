"""GitHub routing: dedup against existing issues, escalation (issue #142).

Uses an injectable ``runner`` (matching governance/dispatch/snapshot.py's own
convention) so this exercises the real command-construction and
response-parsing logic without a network call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List

from github import create_issue, existing_remediation_issues, find_existing, route
from model import RemediationIssue


def _issue(key="class-missing:issue-1", severity="high", occurrences=1):
    return RemediationIssue(
        key=key, code="class-missing", subject="issue-1", title="[remediation] class-missing",
        severity=severity, owner_lane="governance", sla_hours=72,
        policy_ref="governance/conformance/policy.yaml", corrective_steps=("fix it",),
        evidence=["ev"], occurrences=occurrences, scope="repo", repo="acme/widgets",
    )


@dataclass
class _FakeResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class _FakeRunner:
    """Records every `gh` invocation and returns scripted responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: List[list] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        return self.responses.pop(0)


def test_existing_remediation_issues_parses_gh_json():
    runner = _FakeRunner([_FakeResult(0, stdout=json.dumps([{"number": 5, "body": "x"}]))])
    records = existing_remediation_issues("acme/widgets", runner=runner)
    assert records == [{"number": 5, "body": "x"}]
    assert runner.calls[0][:3] == ["gh", "issue", "list"]


def test_existing_remediation_issues_raises_on_gh_failure():
    runner = _FakeRunner([_FakeResult(1, stderr="boom")])
    try:
        existing_remediation_issues("acme/widgets", runner=runner)
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:  # pragma: no cover - the assertion is the point
        raise AssertionError("expected RuntimeError")


def test_find_existing_matches_the_remediation_key_marker():
    issue = _issue()
    body = issue.body()
    records = [{"number": 9, "body": body}]
    match = find_existing(records, issue.key)
    assert match["number"] == 9
    assert find_existing(records, "some-other-key") is None


def test_create_issue_parses_the_issue_number_from_the_url():
    runner = _FakeRunner([_FakeResult(0, stdout="https://github.com/acme/widgets/issues/42\n")])
    number = create_issue(_issue(), "acme/widgets", runner=runner)
    assert number == 42
    cmd = runner.calls[0]
    assert "--title" in cmd and "--body" in cmd
    assert "remediation:auto" in cmd


def test_route_dry_run_makes_no_network_calls():
    runner = _FakeRunner([])
    results = route([_issue()], "acme/widgets", runner=runner, dry_run=True)
    assert results[0].action == "dry-run"
    assert not runner.calls


def test_route_creates_new_issue_when_no_duplicate_exists():
    runner = _FakeRunner(
        [
            _FakeResult(0, stdout="[]"),  # existing_remediation_issues: none
            _FakeResult(0, stdout="https://github.com/acme/widgets/issues/7\n"),  # create
        ]
    )
    results = route([_issue()], "acme/widgets", runner=runner, dry_run=False)
    assert results[0].action == "created"
    assert results[0].number == 7


def test_route_comments_on_existing_issue_instead_of_duplicating():
    dup = _issue()
    existing_body = dup.body()
    runner = _FakeRunner(
        [
            _FakeResult(0, stdout=json.dumps([{"number": 3, "body": existing_body}])),
            _FakeResult(0, stdout=""),  # comment
        ]
    )
    results = route([dup], "acme/widgets", runner=runner, dry_run=False)
    assert results[0].action == "commented"
    assert results[0].number == 3
    assert runner.calls[1][:3] == ["gh", "issue", "comment"]


def test_route_escalates_when_the_issue_qualifies():
    hot = _issue(severity="critical")
    runner = _FakeRunner(
        [
            _FakeResult(0, stdout="[]"),
            _FakeResult(0, stdout="https://github.com/acme/widgets/issues/8\n"),
            _FakeResult(0, stdout=""),  # escalate: add labels
        ]
    )
    results = route([hot], "acme/widgets", runner=runner, dry_run=False)
    assert results[0].escalated is True
    assert runner.calls[-1][:3] == ["gh", "issue", "edit"]
    assert "remediation:escalated" in runner.calls[-1]
