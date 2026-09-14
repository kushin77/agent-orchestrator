"""Behavioral tests for the cross-repo execution-boundary detector (#125).

These are real behavioural tests: every one drives ``check_issues`` / ``main``
with actual issue payloads and asserts on the returned findings or exit code —
none of them greps the source text. The last test is the falsifiability pin: it
proves the ``self-parent`` check is not vacuous by feeding an issue that *looks*
compliant (the marker is buried in a fenced code block) and asserting it is
still flagged, and it pins the documented case-sensitivity of the reference
keywords.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from boundary import (
    EXIT_CANNOT_ASSESS,
    EXIT_NOT_OK,
    EXIT_OK,
    FINDING_FOREIGN_REPO_ISSUE,
    FINDING_SELF_PARENT,
    BoundaryPolicy,
    Finding,
    check_issues,
    load_issues,
    main,
)

OWN = "kushin77/agent-orchestrator"
POLICY = BoundaryPolicy(own_repo=OWN)


def _issue(number: int, title: str = "t", body: str = "") -> dict:
    return {"number": number, "title": title, "body": body}


def _snapshot(tmp_path: Path, issues: list) -> Path:
    path = tmp_path / "issues.json"
    path.write_text(json.dumps(issues), encoding="utf-8")
    return path


def _kinds(findings: list) -> list:
    return sorted(f.finding for f in findings)


# --- self-parent ------------------------------------------------------------


def test_self_parent_marker_is_reported():
    issues = [
        _issue(
            126,
            "CMR vendor compliance gap: saas-rbac",
            "Parent: #125\n\nEnable branch protection in kushin77/saas-rbac.",
        )
    ]
    findings = check_issues(POLICY, issues)
    self_parent = [f for f in findings if f.finding == FINDING_SELF_PARENT]
    assert len(self_parent) == 1
    assert self_parent[0].issue == 126
    assert "Parent: #125" in self_parent[0].detail


def test_correctly_parented_issue_is_not_reported():
    # A child that is genuinely owned by this repo carries no out-of-scope
    # marker and points at a same-repo parent, so it must be clean.
    issues = [_issue(140, "Conformance gate", "Parent: #4\nCloses #139")]
    assert check_issues(POLICY, issues) == []


def test_marker_matching_is_a_plain_substring_by_contract():
    """Pin marker semantics: a plain substring match, deliberately.

    The boundary check errs toward *over*-reporting: a body that mentions the
    marker anywhere (here ``Parent: #1250``) is flagged, and the human
    quarantines the false positive by name. Widening a marker to a smarter
    pattern would make the detector silently miss a real child, which is the
    failure mode this gate exists to prevent.
    """

    issues = [_issue(500, "Unrelated", "Parent: #1250")]
    findings = check_issues(POLICY, issues)
    assert len(findings) == 1
    assert findings[0].finding == FINDING_SELF_PARENT
    assert findings[0].issue == 500


# --- foreign-repo references ------------------------------------------------


def test_foreign_repo_ref_is_reported():
    issues = [_issue(700, "Fix it over there", "Closes kushin77/saas-rbac#42")]
    findings = [f for f in check_issues(POLICY, issues) if f.finding == FINDING_FOREIGN_REPO_ISSUE]
    assert len(findings) == 1
    assert "kushin77/saas-rbac#42" in findings[0].detail


def test_same_repo_closes_ref_is_not_reported():
    issues = [_issue(701, "Local work", "Closes #42\nRefs #43")]
    assert check_issues(POLICY, issues) == []


def test_all_three_reference_forms_are_checked():
    issues = [
        _issue(
            702,
            "Three foreign refs",
            "Parent: kushin77/CMR#12\nRefs kushin77/shared-frontend#9\n"
            "Closes kushin77/diagrams#3",
        )
    ]
    findings = check_issues(POLICY, issues)
    assert len(findings) == 3
    assert all(f.finding == FINDING_FOREIGN_REPO_ISSUE for f in findings)
    details = " ".join(f.detail for f in findings)
    for repo in ("kushin77/CMR#12", "kushin77/shared-frontend#9", "kushin77/diagrams#3"):
        assert repo in details


def test_own_repo_reference_is_not_foreign():
    issues = [_issue(703, "Self ref", "Closes kushin77/agent-orchestrator#125")]
    assert check_issues(POLICY, issues) == []


def test_own_repo_reference_matches_exactly_not_by_prefix():
    # A repo whose name merely *starts with* the own repo name is a different
    # repo and must still be flagged as foreign.
    issues = [_issue(704, "Prefix trap", "Closes kushin77/agent-orchestrator-extras#1")]
    findings = check_issues(POLICY, issues)
    assert len(findings) == 1
    assert findings[0].finding == FINDING_FOREIGN_REPO_ISSUE


# --- aggregation / clean sets ----------------------------------------------


def test_clean_set_yields_zero_findings():
    issues = [
        _issue(1, "a", "Parent: #4\nCloses #2"),
        _issue(2, "b", "Refs #1"),
        _issue(3, "c", ""),
    ]
    assert check_issues(POLICY, issues) == []


def test_one_issue_can_carry_both_finding_kinds():
    issues = [
        _issue(
            705,
            "Both kinds",
            "Parent: #125\nCloses kushin77/shared-services#4040",
        )
    ]
    findings = check_issues(POLICY, issues)
    assert _kinds(findings) == [
        FINDING_FOREIGN_REPO_ISSUE,
        FINDING_SELF_PARENT,
    ]


def test_duplicate_foreign_refs_report_once():
    issues = [_issue(706, "Dupe", "Refs kushin77/saas-rbac#42\nRefs kushin77/saas-rbac#42")]
    findings = check_issues(POLICY, issues)
    assert len(findings) == 1


def test_issues_without_a_usable_number_are_skipped():
    issues = [{"title": "no number", "body": "Parent: #125"}, {"number": "abc", "body": "Parent: #125"}]
    assert check_issues(POLICY, issues) == []


def test_custom_marker_overrides_the_default():
    policy = BoundaryPolicy(own_repo=OWN, out_of_scope_markers=("Owned by: other-repo",))
    issues = [
        _issue(707, "custom", "Owned by: other-repo"),
        _issue(708, "default marker ignored", "Parent: #125"),
    ]
    findings = check_issues(policy, issues)
    assert len(findings) == 1
    assert findings[0].issue == 707


# --- load_issues ------------------------------------------------------------


def test_load_issues_accepts_a_bare_list(tmp_path: Path):
    path = _snapshot(tmp_path, [_issue(1, "a")])
    assert load_issues(path) == [_issue(1, "a")]


def test_load_issues_accepts_an_items_wrapper(tmp_path: Path):
    path = tmp_path / "wrapped.json"
    path.write_text(json.dumps({"items": [_issue(2, "b")]}), encoding="utf-8")
    assert [i["number"] for i in load_issues(path)] == [2]


def test_load_issues_rejects_a_non_list_payload(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"total": 0}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_issues(path)


# --- main() exit-code contract ---------------------------------------------


def test_main_returns_cannot_assess_on_missing_file(tmp_path: Path, capsys):
    missing = tmp_path / "not-there.json"
    rc = main(["--policy-own-repo", OWN, "--issues", str(missing)])
    assert rc == EXIT_CANNOT_ASSESS
    assert rc != EXIT_OK
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_main_returns_cannot_assess_on_empty_issue_list(tmp_path: Path, capsys):
    path = _snapshot(tmp_path, [])
    rc = main(["--policy-own-repo", OWN, "--issues", str(path)])
    assert rc == EXIT_CANNOT_ASSESS
    assert rc != EXIT_OK
    assert "empty" in capsys.readouterr().err


def test_main_returns_cannot_assess_on_unreadable_json(tmp_path: Path, capsys):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    rc = main(["--policy-own-repo", OWN, "--issues", str(path)])
    assert rc == EXIT_CANNOT_ASSESS
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_main_returns_cannot_assess_without_own_repo(capsys):
    rc = main(["--issues", "whatever.json"])
    assert rc == EXIT_CANNOT_ASSESS
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_main_returns_ok_on_a_clean_snapshot(tmp_path: Path, capsys):
    path = _snapshot(tmp_path, [_issue(1, "a", "Closes #2")])
    rc = main(["--policy-own-repo", OWN, "--issues", str(path)])
    assert rc == EXIT_OK
    assert "OK" in capsys.readouterr().out


def test_main_returns_not_ok_and_names_the_violation(tmp_path: Path, capsys):
    path = _snapshot(tmp_path, [_issue(126, "gap: saas-rbac", "Parent: #125")])
    rc = main(["--policy-own-repo", OWN, "--issues", str(path)])
    assert rc == EXIT_NOT_OK
    err = capsys.readouterr().err
    assert "#126" in err
    assert FINDING_SELF_PARENT in err


# --- falsifiability pins ----------------------------------------------------


def test_marker_inside_a_fenced_code_block_still_flags():
    """The negative control: a *compliant-looking* issue is still caught.

    The issue below reads as documentation of the boundary — the marker sits
    inside a fenced code block, as if quoting the rule — but the body still
    carries the out-of-scope marker, so the detector must flag it. This is the
    mutation the suite is built to survive: make ``check_issues`` return ``[]``
    unconditionally (or drop the marker scan) and this test goes red while the
    rest of the suite stays green, proving the check is load-bearing rather
    than decorative.
    """

    body = (
        "Boundary notes.\n\n"
        "```text\n"
        "Parent: #125\n"
        "```\n\n"
        "Everything above is a quote of the child-issue convention.\n"
    )
    issues = [_issue(800, "Quoted marker", body)]
    findings = check_issues(POLICY, issues)
    assert len(findings) == 1
    assert findings[0].finding == FINDING_SELF_PARENT


def test_reference_keywords_are_case_sensitive_as_documented():
    """Pin the documented case sensitivity of ``Closes``/``Refs``/``Parent:``.

    Lowercase forms are not treated as references, so an issue that discusses
    a foreign repo casually is not flagged. If a future change makes matching
    case-insensitive, this test fails and the behaviour change has to be
    intentional (and documented) rather than silent.
    """

    issues = [_issue(801, "Lowercase", "closes kushin77/saas-rbac#42\nrefs kushin77/diagrams#3")]
    assert check_issues(POLICY, issues) == []


def test_finding_dataclass_is_frozen():
    finding = Finding(issue=1, title="t", finding=FINDING_SELF_PARENT, detail="d")
    with pytest.raises(Exception):
        finding.issue = 2  # type: ignore[misc]
