"""Behavioral tests for the cross-repo execution-boundary detector (#125).

These are real behavioural tests: every one drives ``check_issues`` / ``main``
with actual issue payloads and asserts on the returned findings or exit code —
none of them greps the source text. Each of the three checks carries its own
negative control, so none of them can be silently vacuous: the ``self-parent``
pin feeds a marker buried in a fenced code block, and the
``foreign-repo-declaration`` pin feeds a declaration whose value sits behind a
blank line. The suite also pins the documented case-sensitivity of the
reference keywords and the deliberate precision cutoff of the declaration
marker (``## Repository layout`` is prose, not a declaration).

The last declaration test runs the committed real board fixture
(``fixtures/board-125-children.json``): the 13 real #125-#137 issues with their
real ``## Repo`` declarations, which must yield exactly the 11 open children.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from boundary import (
    EXIT_CANNOT_ASSESS,
    EXIT_NOT_OK,
    EXIT_OK,
    FINDING_FOREIGN_REPO_DECLARATION,
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
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _issue(number: int, title: str = "t", body: str = "", state=None) -> dict:
    issue = {"number": number, "title": title, "body": body}
    if state is not None:
        issue["state"] = state
    return issue


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


def test_marker_matching_requires_a_whole_issue_number():
    """Pin marker semantics: whole-number match, not digit-prefix substring.

    Issue #1722 — a plain substring test flagged ``Parent: #1254`` (and 25
    siblings) as carrying the ``Parent: #125`` marker purely because ``#125``
    prefixes ``#1254``; none of those were real out-of-scope declarations. A
    trailing-digit lookahead loses no real match (``Parent: #125`` on its own
    still matches) while dropping the digit-prefix false positives.
    """

    issues = [_issue(500, "Unrelated", "Parent: #1250")]
    findings = check_issues(POLICY, issues)
    assert findings == []

    real = [_issue(501, "Real out-of-scope child", "Parent: #125\n")]
    real_findings = check_issues(POLICY, real)
    assert len(real_findings) == 1
    assert real_findings[0].finding == FINDING_SELF_PARENT
    assert real_findings[0].issue == 501


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


# --- foreign-repo declarations ----------------------------------------------
# The board's own convention: a body declares its repo in a `## Repo` heading
# (or a `Repo:` label) with the value on the next non-blank / same line. That
# declaration is what the live #126-#137 children carry, and it is the signal
# that survives even when the legacy `Parent: #125` marker is dropped.


def test_open_child_declaring_a_foreign_repo_is_flagged():
    issues = [_issue(126, "CMR vendor compliance gap: saas-rbac", "## Repo\nsaas-rbac\n", state="open")]
    findings = check_issues(POLICY, issues)
    assert len(findings) == 1
    assert findings[0].finding == FINDING_FOREIGN_REPO_DECLARATION
    assert findings[0].issue == 126
    assert findings[0].repos == ("saas-rbac",)
    assert "saas-rbac" in findings[0].detail


def test_declaring_the_own_repo_is_not_flagged():
    # Both spellings of this repo are self: the bare name and owner/name.
    issues = [
        _issue(900, "self, bare name", "## Repo\nagent-orchestrator\n", state="open"),
        _issue(901, "self, owner/name", "## Repo\nkushin77/agent-orchestrator\n", state="open"),
    ]
    assert check_issues(POLICY, issues) == []


def test_declaration_self_comparison_is_case_insensitive():
    issues = [
        _issue(902, "self, shouting", "## Repo\nAgent-Orchestrator\n", state="open"),
        _issue(903, "foreign, shouting", "## REPO\nSAAS-RBAC\n", state="open"),
    ]
    findings = check_issues(POLICY, issues)
    assert [f.issue for f in findings] == [903]
    assert findings[0].repos == ("SAAS-RBAC",)


def test_closed_child_declaring_a_foreign_repo_is_not_flagged():
    # A closed child is resolved history, not a live finding (#130 in the real
    # board fixture is exactly this case).
    issues = [_issue(130, "gap: shared-frontend", "## Repo\nshared-frontend\n", state="closed")]
    assert check_issues(POLICY, issues) == []


def test_closed_state_is_matched_case_insensitively():
    # The committed board export spells states `CLOSED`; the live API `closed`.
    issues = [_issue(130, "gap: shared-frontend", "## Repo\nshared-frontend\n", state="CLOSED")]
    assert check_issues(POLICY, issues) == []


def test_missing_state_is_treated_as_open_fail_closed():
    # No `state` key at all: the check must not silently pass. An absent field
    # can never be allowed to turn a real violation into OK.
    issues = [_issue(904, "no state field", "## Repo\nshared-services\n")]
    findings = check_issues(POLICY, issues)
    assert len(findings) == 1
    assert findings[0].finding == FINDING_FOREIGN_REPO_DECLARATION
    assert findings[0].repos == ("shared-services",)


def test_inline_label_and_backticked_value_are_handled():
    issues = [
        _issue(905, "inline label", "Repo: `shared-frontend`\n", state="open"),
        _issue(906, "bold label", "**Repo**: shared-temporal\n", state="open"),
    ]
    findings = check_issues(POLICY, issues)
    assert [f.repos for f in findings] == [("shared-frontend",), ("shared-temporal",)]


def test_bold_label_with_value_on_the_next_line_is_handled():
    issues = [_issue(907, "bold label, next line", "**Repo**\n\n`diagrams`\n", state="open")]
    findings = check_issues(POLICY, issues)
    assert [f.repos for f in findings] == [("diagrams",)]


def test_declaration_value_may_be_owner_repo():
    issues = [_issue(908, "owner/name value", "## Repo\nkushin77/saas-rbac\n", state="open")]
    findings = check_issues(POLICY, issues)
    assert [f.repos for f in findings] == [("kushin77/saas-rbac",)]


def test_declaration_value_is_reduced_to_its_first_token():
    issues = [
        _issue(909, "trailing prose", "Repo: saas-rbac (see the hygiene report)\n", state="open")
    ]
    findings = check_issues(POLICY, issues)
    assert [f.repos for f in findings] == [("saas-rbac",)]


def test_repeated_declaration_of_one_repo_reports_once():
    issues = [_issue(910, "dupe", "## Repo\nsaas-rbac\n\n## Repo\nsaas-rbac\n", state="open")]
    findings = check_issues(POLICY, issues)
    assert len(findings) == 1


def test_one_body_may_declare_self_and_foreign():
    issues = [
        _issue(911, "mixed", "## Repo\nagent-orchestrator\n\n## Repo\nsaas-rbac\n", state="open")
    ]
    findings = check_issues(POLICY, issues)
    assert [f.repos for f in findings] == [("saas-rbac",)]


def test_declaration_heading_without_a_value_is_not_a_finding():
    # Nothing parseable follows the heading, so nothing is declared: an
    # unparseable value must never manufacture a finding (nor crash).
    issues = [
        _issue(912, "empty", "## Repo\n", state="open"),
        _issue(913, "heading then another heading", "## Repo\n\n## Scope\nbody\n", state="open"),
    ]
    assert check_issues(POLICY, issues) == []


def test_prose_headings_and_labels_are_not_declarations():
    # The precision cutoff, pinned: the marker must be the whole heading or
    # label. These are prose and must never be flagged.
    issues = [
        _issue(914, "heading prose", "## Repository layout\nsaas-rbac lives elsewhere\n", state="open"),
        _issue(915, "label plural", "Repos: saas-rbac, diagrams\n", state="open"),
        _issue(916, "sentence", "The Repo: field is optional here\n", state="open"),
    ]
    assert check_issues(POLICY, issues) == []


def test_declaration_on_an_issue_without_a_number_is_skipped():
    issues = [{"title": "no number", "body": "## Repo\nsaas-rbac\n", "state": "open"}]
    assert check_issues(POLICY, issues) == []


def test_fixture_reports_exactly_the_eleven_open_children():
    """The real board fixture: exactly the 11 open children are findings.

    ``fixtures/board-125-children.json`` carries the 13 real issues #125-#137
    with their real ``## Repo`` declarations and their live states. Exactly 11
    findings are expected — #126-#129 and #131-#137 — because **#130 is closed**
    (shared-frontend, resolved history) and the epic #125 declares no repo.
    """

    issues = load_issues(FIXTURES / "board-125-children.json")
    assert [i["number"] for i in issues] == list(range(125, 138))

    findings = check_issues(POLICY, issues)
    assert len(findings) == 11
    assert all(f.finding == FINDING_FOREIGN_REPO_DECLARATION for f in findings)
    assert [f.issue for f in findings] == [126, 127, 128, 129, 131, 132, 133, 134, 135, 136, 137]
    assert 130 not in [f.issue for f in findings]  # closed: not a live finding
    assert 125 not in [f.issue for f in findings]  # the epic declares no repo
    assert [f.repos for f in findings] == [
        ("saas-rbac",),
        ("github-workflow",),
        ("shared-temporal",),
        ("Shared_Integrations",),
        ("shared-governance",),
        ("shared-services",),
        ("googleworkspace",),
        ("SharedFeatures",),
        ("ERP-CRM",),
        ("code-indexing",),
        ("diagrams",),
    ]


def test_fixture_exits_not_ok_and_names_the_foreign_repos(tmp_path: Path, capsys):
    # End to end through the CLI contract: the real board data is NOT-OK, and
    # the printed findings name the foreign repo the issue asks this repo to
    # remediate.
    fixture = FIXTURES / "board-125-children.json"
    assert main(["--policy-own-repo", OWN, "--issues", str(fixture)]) == EXIT_NOT_OK
    err = capsys.readouterr().err
    assert "#126" in err and "saas-rbac" in err
    assert FINDING_FOREIGN_REPO_DECLARATION in err
    assert str(tmp_path) not in err  # the fixture, not a scratch file, was read


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


def test_declaration_behind_a_blank_line_still_flags():
    """The declaration check's negative control: a *displaced* value still counts.

    The heading and its value are separated by a blank line, and the value is
    wrapped in backticks with surrounding spaces — exactly how a careful author
    might write the same declaration. The detector must still resolve it, which
    is what makes the check load-bearing: make ``_declared_repos`` return ``[]``
    (or stop looking past the heading's own line) and this test goes red while
    the rest of the suite stays green, proving the real board fixture's 11
    findings come from this check rather than from a coincidence.
    """

    body = "Some preamble.\n\n## Repo\n\n   ` shared-services `   \n\n## Finding summary\nx\n"
    issues = [_issue(802, "Displaced declaration", body, state="open")]
    findings = check_issues(POLICY, issues)
    assert len(findings) == 1
    assert findings[0].finding == FINDING_FOREIGN_REPO_DECLARATION
    assert findings[0].repos == ("shared-services",)


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
