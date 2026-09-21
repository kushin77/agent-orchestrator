"""Conformance checking — negative controls (issue #140).

Each test drives one failure mode deliberately. A conformance checker that only
ever reports success is the false-green the policy exists to prevent, so the codes
below are all reachable by construction.
"""

from __future__ import annotations

import json
from pathlib import Path

import importlib.util as _importlib_util  # noqa: E402
from pathlib import Path as _ConftestPath  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702, #1042).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_conformance_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
issue = _conftest.issue

from checker import (
    check_board,
    check_change_set,
    classify,
    load_snapshot,
    missing_suite_registration,
)
from model import (
    CODE_CLASS_AMBIGUOUS,
    CODE_CLASS_EXPECTATION_UNMET,
    CODE_CLASS_MISSING,
    CODE_CLASS_UNKNOWN,
    CODE_CLASSIFICATION_INCOMPLETE,
    CODE_DEPENDENCY_MISSING,
    CODE_IAC_MANDATE_UNMET,
    CODE_SCOPE_MISMATCH,
    errors,
    warnings,
)


def codes(findings):
    return sorted(f.code for f in findings)


# -- board: classification is required ---------------------------------------


def test_milestoned_issue_without_a_class_is_an_error(policy):
    """NEGATIVE CONTROL: unclassified commitment cannot be held to a standard."""
    report = check_board([issue(7, labels=("type:feature",))], policy)

    assert CODE_CLASS_MISSING in codes(report.findings)
    assert report.conformant is False
    finding = next(f for f in report.findings if f.code == CODE_CLASS_MISSING)
    assert "class:" in finding.remediation


def test_class_outside_the_ladder_is_an_error(policy):
    """NEGATIVE CONTROL: a class that is not a rung names no standard."""
    report = check_board([issue(7, labels=("class:platinum", "type:feature"))], policy)

    assert CODE_CLASS_UNKNOWN in codes(report.findings)
    assert "platinum" in next(
        f for f in report.findings if f.code == CODE_CLASS_UNKNOWN
    ).message


def test_two_classes_on_one_issue_is_an_error(policy):
    """A claim must name one rung; two rungs is an ambiguous claim."""
    report = check_board(
        [issue(7, labels=("class:elite", "class:enterprise", "type:feature"))], policy
    )

    assert CODE_CLASS_AMBIGUOUS in codes(report.findings)


def test_missing_companion_label_is_an_error(policy):
    """NEGATIVE CONTROL: every rung requires type/priority/area."""
    report = check_board([issue(7, labels=("class:enterprise",))], policy)

    assert CODE_CLASSIFICATION_INCOMPLETE in codes(report.findings)
    messages = [
        f.message for f in report.findings if f.code == CODE_CLASSIFICATION_INCOMPLETE
    ]
    for name in ("type", "priority", "area"):
        assert any("no `%s:` label" % name in message for message in messages)


# -- board: declared-vs-actual expectations ----------------------------------


def test_declared_class_expectation_is_a_reported_deviation(policy):
    """NEGATIVE CONTROL: elite declaring no pillar is a mismatch, reported."""
    report = check_board([issue(7, labels=("class:elite", "type:feature",
                                           "priority:P0", "area:board",
                                           "gdc:enterprise"))], policy)

    assert CODE_CLASS_EXPECTATION_UNMET in codes(report.findings)
    finding = next(f for f in report.findings if f.code == CODE_CLASS_EXPECTATION_UNMET)
    assert finding.severity == "warning"
    assert "pillar" in finding.message
    assert report.conformant is True  # a deviation, not a wedge
    # The cascade in #1694 (69 milestoned issues stuck on this exact warning)
    # showed the fix has to be a one-paste command, not a description of one.
    assert "gh issue edit 7 --add-label pillar:" in finding.remediation


def test_strict_escalates_a_deviation_to_an_error(policy):
    """--strict is how a milestone that has caught up is checked."""
    report = check_board(
        [issue(7, labels=("class:elite", "type:feature", "priority:P0",
                          "area:board", "gdc:enterprise"))],
        policy,
        strict=True,
    )

    assert report.conformant is False
    assert CODE_CLASS_EXPECTATION_UNMET in codes(errors(report.findings))


def test_a_fully_classified_issue_is_conformant(policy):
    report = check_board(
        [issue(7, labels=("class:elite", "type:governance", "priority:P0",
                          "area:board", "gdc:enterprise", "pillar:governance"))],
        policy,
    )
    assert report.findings == []
    assert report.conformant is True


def test_closed_issues_are_out_of_scope(policy):
    report = check_board([issue(7, state="CLOSED", labels=("class:elite",))], policy)
    assert report.scanned == 0
    assert report.findings == []


def test_unmilestoned_backlog_is_counted_not_failed(policy):
    """NEGATIVE CONTROL: the pre-convention backlog is surfaced, not fatal."""
    report = check_board([issue(7, milestone="", labels=())], policy)

    assert report.scanned == 0
    assert report.conformant is True
    assert CODE_SCOPE_MISMATCH in codes(report.findings)
    assert "no milestone" in next(
        f for f in report.findings if f.code == CODE_SCOPE_MISMATCH
    ).message


def test_unmilestoned_can_be_brought_into_scope(policy):
    report = check_board(
        [issue(7, milestone="", labels=())], policy, include_unmilestoned=True
    )
    assert report.scanned == 1
    assert CODE_CLASS_MISSING in codes(report.findings)


def test_milestone_filter_narrows_scope(policy):
    issues = [
        issue(1, milestone="M24 - Enterprise Knowledge Index",
              labels=("class:enterprise", "type:feature", "priority:P1",
                      "area:board", "gdc:enterprise")),
        issue(2, milestone="M25 - Per-Repo Agent Fleet", labels=()),
    ]
    report = check_board(issues, policy, milestone="M24 - Enterprise Knowledge Index")
    assert report.scanned == 1
    assert report.findings == []


def test_class_counts_are_reported_for_the_board(policy, snapshot_file: Path):
    issues = load_snapshot(snapshot_file)
    report = check_board(issues, policy)

    assert report.scanned == 2  # the CLOSED and the un-milestoned are out of scope
    assert report.class_counts == {"enterprise": 1, "elite": 1}


def test_classify_parses_declared_class_and_companions():
    item = classify(issue(9, labels=("class:elite", "area:board", "gdc:enterprise")))
    assert item.declared == "elite"
    assert item.value_of("area") == "board"
    assert item.has("gdc") is True
    assert item.has("pillar") is False


# -- change set: the IaC mandate ---------------------------------------------


def test_new_workflow_file_is_an_error(policy, tmp_path: Path):
    """NEGATIVE CONTROL: fleet GR-15 keeps automation code-native."""
    findings = check_change_set(
        [".github/workflows/ci.yml"], policy, added=[".github/workflows/ci.yml"]
    )

    assert CODE_IAC_MANDATE_UNMET in codes(findings)
    assert "GR-15" in findings[0].message


def test_new_infra_without_a_flag_is_an_error(policy, tmp_path: Path):
    """NEGATIVE CONTROL: new infrastructure must ship flag-gated OFF."""
    infra = tmp_path / "infra"
    infra.mkdir(parents=True, exist_ok=True)
    (infra / "plain.tf").write_text('resource "x" "y" {}\n', encoding="utf-8")

    findings = check_change_set(
        ["infra/plain.tf"], policy, added=["infra/plain.tf"], root=tmp_path
    )

    assert CODE_IAC_MANDATE_UNMET in codes(findings)
    assert "flag-gated" in findings[0].message


def test_new_infra_with_a_flag_conforms(policy, tmp_path: Path):
    """The positive control: same shape, declared OFF, no finding."""
    infra = tmp_path / "infra"
    infra.mkdir(parents=True, exist_ok=True)
    (infra / "flagged.tf").write_text(
        'variable "enable_thing" { default = false }\n', encoding="utf-8"
    )

    findings = check_change_set(
        ["infra/flagged.tf"], policy, added=["infra/flagged.tf"], root=tmp_path
    )
    assert findings == []


def test_modifying_existing_infra_is_not_a_mandate_breach(policy, tmp_path: Path):
    """Only creation is constrained; edits to declared infra are normal work."""
    findings = check_change_set(["infra/apply.yaml"], policy, added=[])
    assert findings == []


def test_untracked_package_without_a_suite_is_reported(policy):
    findings = missing_suite_registration(
        ["governance/newthing/model.py"], ["governance/merge"]
    )
    assert CODE_DEPENDENCY_MISSING in codes(findings)
    assert warnings(findings)


def test_package_with_a_declared_suite_is_quiet(policy):
    findings = missing_suite_registration(
        ["governance/merge/engine.py"], ["governance/merge"]
    )
    assert findings == []


# -- determinism -------------------------------------------------------------


def test_board_check_is_deterministic(policy):
    issues = [
        issue(1, labels=("class:elite",)),
        issue(2, labels=("class:platinum",)),
        issue(3, labels=()),
    ]
    first = check_board(issues, policy, generated_at="fixed")
    second = check_board(issues, policy, generated_at="fixed")

    assert [f.as_dict() for f in first.findings] == [f.as_dict() for f in second.findings]
    assert first.as_dict() == second.as_dict()
