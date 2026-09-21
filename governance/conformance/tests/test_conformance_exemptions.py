"""The NAMED pre-mandate exemption seam — both directions (issue #1694).

The board check enforces `class`/`type`/`priority`/`area` on every OPEN + MILESTONED
issue. The un-milestoned backlog is *counted, not failed* by a broad predicate; the
pre-mandate MILESTONED backlog (which the class backfill exposed, and for which
`priority`/`area` have no honest bulk default) is counted too — but **explicitly and
by name**, through `governance/conformance/exemptions.json`.

Every test here drives the seam deliberately, because a check that cannot fail is a
formality (GR-12) and an exemption that cannot be *refused* is a silent broad
grandfather:

* a NAMED issue is counted (its undeclared-metadata findings become a warning);
* an issue that is NOT named still fails BY NAME — the property the whole seam rests
  on, since it is what stops a newly filed issue inheriting the exemption;
* a declared-but-wrong class is never excused (an exemption covers missing metadata,
  not a false claim);
* a STALE entry fails by name — an entry whose issue is out of scope, whose issue now
  conforms, or whose `owner` has closed;
* the declared SIZE is asserted, so the list cannot grow quietly to absorb new debt;
* a malformed or unreadable document is refused by name, never read as still excused.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import json
import subprocess
import sys
from pathlib import Path as _ConftestPath

# conftest.py under this directory is loaded by absolute path: when the whole
# governance tree is collected, every suite's `tests/conftest.py` shares the bare
# module name `conftest`, so whichever lands last silently wins (issues #699,
# #702, #1042).
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_conformance_tests_conftest_exemptions",
    _ConftestPath(__file__).with_name("conftest.py"),
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
issue = _conftest.issue

from checker import (  # noqa: E402
    CODE_EXEMPTION_APPLIED,
    CODE_EXEMPTION_MALFORMED,
    CODE_EXEMPTION_SIZE,
    CODE_EXEMPTION_STALE,
    CODE_CLASS_MISSING,
    CODE_CLASS_UNKNOWN,
    CODE_CLASSIFICATION_INCOMPLETE,
    EXEMPTIONS_RELPATH,
    SNAPSHOT_RELPATH,
    POLICY_RELPATH,
    check_board,
    load_exemptions,
    load_policy,
    load_snapshot,
)
from model import errors, warnings  # noqa: E402

ROOT = _ConftestPath(__file__).resolve().parents[3]

DEBT_LABELS = ("class:enterprise", "type:feature")  # no `priority:`, no `area:`
WHOLE = ("class:enterprise", "type:feature", "priority:P2", "area:standards")


def codes(findings):
    return sorted(f.code for f in findings)


def entry(ref, owner="#1254", reason="pre-mandate backlog; triaged by #1254"):
    return {"issue": ref, "owner": owner, "reason": reason}


def document(tmp_path, entries, *, expected=None, **extra):
    """Write an exemption document and load it through the real loader."""
    payload = {
        "schema": "cmr.conformance/exemptions-v1",
        "expected": len(entries) if expected is None else expected,
        "exemptions": entries,
    }
    payload.update(extra)
    path = tmp_path / "exemptions.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_exemptions(path)


# -- the exemption is granted, and only to the named issue -------------------


def test_a_named_issue_is_counted_not_failed(policy, tmp_path):
    board = [issue(1254, labels=DEBT_LABELS)]
    report = check_board(board, policy, exemptions=document(tmp_path, [entry("#1254")]))

    assert report.conformant is True, codes(report.findings)
    assert CODE_CLASSIFICATION_INCOMPLETE not in codes(report.findings)
    applied = [f for f in report.findings if f.code == CODE_EXEMPTION_APPLIED]
    assert len(applied) == 1
    assert applied[0].severity == "warning"  # counted, not failed
    assert "1 pre-mandate" in applied[0].message
    assert "#1254" in applied[0].message


def test_an_issue_not_named_still_fails_by_name(policy, tmp_path):
    """NEGATIVE CONTROL: this is what stops a new issue inheriting the exemption."""
    board = [
        issue(1254, labels=DEBT_LABELS),
        issue(1255, labels=DEBT_LABELS),  # identical debt, deliberately NOT named
    ]
    report = check_board(board, policy, exemptions=document(tmp_path, [entry("#1254")]))

    assert report.conformant is False
    incomplete = [f for f in report.findings if f.code == CODE_CLASSIFICATION_INCOMPLETE]
    assert len(incomplete) == 2, codes(report.findings)
    assert {f.subject for f in incomplete} == {"issue-1255"}
    assert all("no `priority:`" in f.message or "no `area:`" in f.message for f in incomplete)
    assert errors(report.findings)  # a hard error, not a deviation


def test_no_exemptions_at_all_means_every_debt_fails(policy):
    board = [issue(1254, labels=DEBT_LABELS)]
    report = check_board(board, policy)

    assert CODE_CLASSIFICATION_INCOMPLETE in codes(report.findings)
    assert report.conformant is False


def test_a_declared_but_wrong_class_is_never_excused(policy, tmp_path):
    """An exemption covers what the board never recorded, not what it got wrong."""
    board = [issue(1254, labels=("class:platinum", "type:feature"))]
    report = check_board(board, policy, exemptions=document(tmp_path, [entry("#1254")]))

    assert CODE_CLASS_UNKNOWN in codes(report.findings)
    assert report.conformant is False


def test_an_exempt_issue_with_no_class_is_still_counted(policy, tmp_path):
    """The class layer this seam was exposed by: `class` is undeclared, so exempt."""
    board = [issue(1254, labels=("type:feature",))]
    report = check_board(board, policy, exemptions=document(tmp_path, [entry("#1254")]))

    assert report.conformant is True, codes(report.findings)
    assert CODE_CLASS_MISSING not in codes(report.findings)
    assert [f.code for f in report.findings].count(CODE_EXEMPTION_APPLIED) == 1


# -- a stale entry fails by name ---------------------------------------------


def test_a_stale_entry_whose_issue_now_conforms_is_an_error(policy, tmp_path):
    """A debt cleared without removing its entry must be caught."""
    board = [issue(1254, labels=WHOLE)]
    report = check_board(board, policy, exemptions=document(tmp_path, [entry("#1254")]))

    stale = [f for f in report.findings if f.code == CODE_EXEMPTION_STALE]
    assert len(stale) == 1
    assert stale[0].subject == "issue-1254"
    assert "excuses nothing" in stale[0].message
    assert report.conformant is False


def test_a_stale_entry_whose_issue_left_scope_is_an_error(policy, tmp_path):
    board = [
        issue(1254, labels=DEBT_LABELS),  # the owner, in scope
        issue(1255, labels=DEBT_LABELS, state="CLOSED"),  # the named issue, left scope
    ]
    report = check_board(
        board, policy, exemptions=document(tmp_path, [entry("#1255", owner="#1254")])
    )

    stale = [f for f in report.findings if f.code == CODE_EXEMPTION_STALE]
    assert len(stale) == 1
    assert stale[0].subject == "issue-1255"
    assert "cannot bite" in stale[0].message
    assert report.conformant is False


def test_an_entry_whose_owner_closed_lapses_and_the_debt_fails(policy, tmp_path):
    board = [
        issue(1254, labels=DEBT_LABELS, state="CLOSED"),  # the owner, now closed
        issue(1255, labels=DEBT_LABELS),  # the target, still in scope with a debt
    ]
    report = check_board(
        board, policy, exemptions=document(tmp_path, [entry("#1255", owner="#1254")])
    )

    assert report.conformant is False
    assert CODE_EXEMPTION_STALE in codes(report.findings)
    stale = next(f for f in report.findings if f.code == CODE_EXEMPTION_STALE)
    assert "owner #1254 is not an OPEN issue" in stale.message
    # the lapsed entry stopped excusing anything: the debt itself surfaces
    assert any(
        f.code == CODE_CLASSIFICATION_INCOMPLETE and f.subject == "issue-1255"
        for f in report.findings
    )


# -- the size is asserted (the list can only shrink toward the truth) --------


def test_the_declared_size_is_asserted_when_the_list_has_grown(policy, tmp_path):
    board = [
        issue(1254, labels=DEBT_LABELS),
        issue(1255, labels=DEBT_LABELS),
    ]
    # an entry ADDED without removing a debt (the declared size was not bumped)
    report = check_board(
        board,
        policy,
        exemptions=document(tmp_path, [entry("#1254"), entry("#1255")], expected=1),
    )

    size = [f for f in report.findings if f.code == CODE_EXEMPTION_SIZE]
    assert len(size) == 1
    assert "carries 2 entries but declares `expected: 1`" in size[0].message
    assert report.conformant is False


def test_the_declared_size_is_asserted_when_a_debt_was_cleared(policy, tmp_path):
    board = [issue(1254, labels=DEBT_LABELS)]
    report = check_board(
        board,
        policy,
        exemptions=document(tmp_path, [entry("#1254")], expected=0),
    )

    size = [f for f in report.findings if f.code == CODE_EXEMPTION_SIZE]
    assert len(size) == 1
    assert "carries 1 entries but declares `expected: 0`" in size[0].message


def test_a_missing_integer_expected_is_refused_by_name(policy, tmp_path):
    payload = {"exemptions": [entry("#1254")]}
    path = tmp_path / "exemptions.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = check_board(
        [issue(1254, labels=DEBT_LABELS)], policy, exemptions=load_exemptions(path)
    )
    assert CODE_EXEMPTION_MALFORMED in codes(report.findings)
    assert report.conformant is False


# -- a malformed / unreadable document is never read as still excused --------


def test_an_entry_missing_its_reason_is_refused_by_name(policy, tmp_path):
    bad = [{"issue": "#1254", "owner": "#1254"}]
    report = check_board(
        [issue(1254, labels=DEBT_LABELS)],
        policy,
        exemptions=document(tmp_path, bad),
    )

    assert CODE_EXEMPTION_MALFORMED in codes(report.findings)
    assert CODE_CLASSIFICATION_INCOMPLETE in codes(report.findings)  # debt still fails
    assert report.conformant is False


def test_an_entry_missing_its_owner_is_refused_by_name(policy, tmp_path):
    bad = [{"issue": "#1254", "reason": "no owner"}]
    report = check_board(
        [issue(1254, labels=DEBT_LABELS)], policy, exemptions=document(tmp_path, bad)
    )
    assert CODE_EXEMPTION_MALFORMED in codes(report.findings)
    assert report.conformant is False


def test_an_unreadable_document_fails_by_name(policy, tmp_path):
    path = tmp_path / "exemptions.json"
    path.write_text("{not json", encoding="utf-8")

    loaded = load_exemptions(path)
    assert loaded.present is True and loaded.problems

    report = check_board([issue(1254, labels=DEBT_LABELS)], policy, exemptions=loaded)
    assert CODE_EXEMPTION_MALFORMED in codes(report.findings)
    assert CODE_CLASSIFICATION_INCOMPLETE in codes(report.findings)
    assert report.conformant is False


def test_an_absent_document_grants_nothing_and_is_not_itself_a_defect(policy, tmp_path):
    """A venue with no document excuses nothing; the debts fail on their own merit."""
    loaded = load_exemptions(tmp_path / "absent.json")
    assert loaded.present is False and loaded.entries == () and loaded.problems == ()

    report = check_board([issue(1254, labels=DEBT_LABELS)], policy, exemptions=loaded)
    assert CODE_EXEMPTION_MALFORMED not in codes(report.findings)
    assert CODE_CLASSIFICATION_INCOMPLETE in codes(report.findings)
    assert report.conformant is False


# -- the committed document itself (the acceptance) --------------------------


def test_the_committed_exemption_list_is_measured_well_formed_and_complete():
    """The shipped document: every entry named, owned, and sized; and it bites."""
    document_path = ROOT / EXEMPTIONS_RELPATH
    assert document_path.is_file(), "the named exemption list must ship"

    loaded = load_exemptions(document_path)
    assert loaded.present is True
    assert loaded.problems == ()
    assert loaded.expected == len(loaded.entries)
    assert loaded.entries, "an empty exemption list would be a no-op, not a mechanism"
    for item in loaded.entries:
        assert item.issue.startswith("#")
        assert item.owner.startswith("#")
        assert item.reason.strip()
    assert len({item.issue for item in loaded.entries}) == len(loaded.entries)

    report = check_board(
        load_snapshot(ROOT / SNAPSHOT_RELPATH),
        load_policy(ROOT / POLICY_RELPATH),
        exemptions=loaded,
    )
    assert report.conformant is True, codes(errors(report.findings))
    applied = [f for f in report.findings if f.code == CODE_EXEMPTION_APPLIED]
    assert len(applied) == 1
    assert "%d pre-mandate" % loaded.expected in applied[0].message
    assert len(warnings(report.findings)) >= 1


def test_the_cli_check_is_green_and_names_the_exemption(tmp_path):
    """The gate-of-record command itself, on the committed board."""
    result = subprocess.run(
        [sys.executable, "governance/conformance/cli.py", "check"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "conformance: OK" in result.stdout
    assert CODE_EXEMPTION_APPLIED in result.stdout
    assert "COUNTED, not failed" in result.stdout
