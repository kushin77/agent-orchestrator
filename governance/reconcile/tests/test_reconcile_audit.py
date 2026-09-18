"""The worktree/branch audit: what it names, what it refuses, and what it never does.

The defect this suite is written against is a *wrong OK*. `sweep` discovers
sessions from the sessions that beat, so a lane that left no heartbeat was not
merely missed — it was invisible, and `status` reported `0 orphan(s)` while the
worktrees of closed issues sat on disk. Every test here therefore asserts one of
two things: an artifact nothing explains is **named**, or the audit that could not
read the state says **CANNOT-ASSESS** instead of OK. And one test asserts the
structural invariant the whole change rests on: the port the audit reads through
has no way to remove anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.reconcile.audit import (
    BRANCH,
    EXEMPT,
    MATCHED,
    UNMATCHED,
    WORKTREE,
    AuditOps,
    AuditUnavailable,
    WorktreeEntry,
    audit,
    describe,
    parse_worktrees,
    read_beats,
)
from governance.reconcile.heartbeat import SHELVED, stamp

import importlib.util as _importlib_util  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_reconcile_tests_conftest", Path(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
AGENT = _conftest.AGENT
FakeAuditOps = _conftest.FakeAuditOps

AT = 1_000_000.0


def worktree(path: str, branch: str = "", *, primary: bool = False) -> WorktreeEntry:
    return WorktreeEntry(path=path, branch=branch, head="0f1e2d3c", primary=primary)


def primary(path: str = "/repo") -> WorktreeEntry:
    """A repository always lists its own (primary) worktree first."""
    return worktree(path, "master", primary=True)


def names(report, disposition: str) -> set[str]:
    return {item.artifact.name for item in report.explanations if item.disposition == disposition}


def lane(path: str = "/lanes/ao-628-deadbeef", branch: str = "issue-628") -> WorktreeEntry:
    return worktree(path, branch)


# --- what is named ----------------------------------------------------------


def test_an_unexplained_worktree_is_reported_by_name(root: Path):
    report = audit(root, ops=FakeAuditOps(worktrees=(primary(), lane())), at=AT)
    assert names(report, UNMATCHED) == {"/lanes/ao-628-deadbeef"}
    assert report.exit_code == 1
    assert report.assessable


def test_an_unexplained_issue_branch_is_reported_by_name(root: Path):
    ops = FakeAuditOps(worktrees=(primary(),), branches=("issue-628", "issue-9"))
    report = audit(root, ops=ops, at=AT)
    assert names(report, UNMATCHED) == {"issue-628", "issue-9"}
    assert {item.artifact.kind for item in report.unmatched} == {BRANCH}


def test_a_branch_that_is_not_an_issue_branch_is_not_examined(root: Path):
    """The rule is scoped: only `issue-*` branches are artifacts here."""
    ops = FakeAuditOps(worktrees=(primary(),), branches=("lane-at-risk", "feature/x"))
    report = audit(root, ops=ops, at=AT)
    assert [item.artifact.name for item in report.explanations] == ["/repo"]
    assert names(report, UNMATCHED) == set()


def test_a_session_beat_explains_its_worktree_and_its_branch(root: Path):
    stamp("beaten-one", issue=628, agent=AGENT, root=root, branch="issue-628",
          worktree="/lanes/ao-628-deadbeef", at=AT)
    ops = FakeAuditOps(worktrees=(primary(), lane()), branches=("issue-628",))
    report = audit(root, ops=ops, at=AT)
    assert report.unmatched == []
    assert report.exit_code == 0
    assert all("beat:beaten-one" in item.evidence for item in report.matched)


def test_a_beat_that_left_no_worktree_still_explains_its_branch(root: Path):
    """The worktree is gone but the lane is still beating: the branch is accounted for."""
    stamp("beaten-two", issue=628, agent=AGENT, root=root, branch="issue-628", worktree="", at=AT)
    ops = FakeAuditOps(worktrees=(primary(),), branches=("issue-628",))
    assert audit(root, ops=ops, at=AT).unmatched == []


def test_a_claim_record_explains_an_artifact(root: Path):
    ops = FakeAuditOps(worktrees=(primary(), lane()), claims={628: "subagent-other"})
    report = audit(root, ops=ops, at=AT)
    assert report.unmatched == []
    assert any("claim:#628:subagent-other" in item.evidence for item in report.matched)


def test_the_landing_history_explains_an_artifact(root: Path):
    """A landed item leaves a journal — the worktree is then a leftover, not a mystery."""
    ops = FakeAuditOps(worktrees=(primary(), lane()), landed={628})
    report = audit(root, ops=ops, at=AT)
    assert report.unmatched == []
    assert any("landed:#628" in item.evidence for item in report.matched)


def test_the_three_evidence_sources_are_each_sufficient_alone(root: Path):
    """One source is enough; an artifact needs explaining, not corroborating."""
    for number, ops in (
        (1, FakeAuditOps(worktrees=(primary(), worktree("/lanes/ao-1-x", "issue-1")),
                         claims={1: "a"})),
        (2, FakeAuditOps(worktrees=(primary(), worktree("/lanes/ao-2-x", "issue-2")),
                         landed={2})),
    ):
        assert audit(root, ops=ops, at=AT).unmatched == [], number
    stamp("beaten-three", issue=3, agent=AGENT, root=root, branch="issue-3", worktree="/lanes/ao-3-x", at=AT)
    ops = FakeAuditOps(worktrees=(primary(), worktree("/lanes/ao-3-x", "issue-3")))
    assert audit(root, ops=ops, at=AT).unmatched == []


def test_the_primary_worktree_is_exempt_rather_than_unmatched(root: Path):
    """"The repository itself is always listed, so it can never be an orphan."""
    report = audit(root, ops=FakeAuditOps(worktrees=(primary("/repo"),)), at=AT)
    assert names(report, EXEMPT) == {"/repo"}
    assert report.unmatched == []
    assert report.exit_code == 0


def test_a_detached_worktree_no_record_explains_is_named(root: Path):
    report = audit(root, ops=FakeAuditOps(worktrees=(primary(), worktree("/lanes/detached"))), at=AT)
    assert names(report, UNMATCHED) == {"/lanes/detached"}


# --- the verdict that matters: CANNOT-ASSESS, never a wrong OK ---------------


def test_an_unreadable_git_is_cannot_assess_not_a_clean_ok(root: Path):
    report = audit(root, ops=FakeAuditOps(fail=("list_worktrees",)), at=AT)
    assert not report.assessable
    assert report.exit_code == 2
    assert "list_worktrees" in report.reason


def test_an_empty_worktree_list_is_cannot_assess(root: Path):
    """git always lists at least the repository's own worktree: empty means unread."""
    report = audit(root, ops=FakeAuditOps(worktrees=()), at=AT)
    assert not report.assessable
    assert report.exit_code == 2


def test_a_listing_with_no_primary_entry_is_cannot_assess(root: Path):
    report = audit(root, ops=FakeAuditOps(worktrees=(lane(),)), at=AT)
    assert not report.assessable
    assert report.exit_code == 2


def test_an_unreadable_claim_ledger_is_cannot_assess(root: Path):
    report = audit(root, ops=FakeAuditOps(worktrees=(primary(),), fail=("active_claims",)), at=AT)
    assert not report.assessable
    assert report.exit_code == 2


def test_an_unreadable_landing_history_is_cannot_assess(root: Path):
    report = audit(root, ops=FakeAuditOps(worktrees=(primary(),), fail=("landed_issues",)), at=AT)
    assert not report.assessable
    assert report.exit_code == 2


def test_a_corrupt_session_beat_is_cannot_assess(root: Path):
    """A skipped beat would read as absence — the defect — so it is unreadable instead."""
    sessions = root / ".fleet" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "half-written.json").write_text("{ not json", encoding="utf-8")
    report = audit(root, ops=FakeAuditOps(worktrees=(primary(),)), at=AT)
    assert not report.assessable
    assert "half-written.json" in report.reason
    assert report.exit_code == 2


def test_the_strict_beat_reader_refuses_what_the_lenient_one_skips(root: Path):
    """`read_beats` is deliberately stricter than `heartbeat.list_sessions`."""
    sessions = root / ".fleet" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "broken.json").write_text("{}", encoding="utf-8")  # missing session_id
    with pytest.raises(AuditUnavailable):
        read_beats(root)


def test_an_absent_sessions_directory_is_not_an_unreadable_one(root: Path):
    """No beats at all is a fact, not a failure: nothing beats yet."""
    assert read_beats(root) == []


# --- it reports; it never removes -------------------------------------------


def test_the_audit_only_reads_through_its_port(root: Path):
    ops = FakeAuditOps(worktrees=(primary(), lane()), branches=("issue-9",))
    audit(root, ops=ops, at=AT)
    assert set(ops.calls) <= {"list_worktrees", "list_local_branches", "active_claims", "landed_issues"}


def test_the_port_has_no_removal_method_at_all():
    """The invariant is structural: there is nothing destructive for the audit to call."""
    surface = {
        name
        for name in dir(AuditOps)
        if not name.startswith("_") and callable(getattr(AuditOps, name, None))
    }
    assert surface == {"list_worktrees", "list_local_branches", "active_claims", "landed_issues"}
    for forbidden in ("remove_worktree", "delete_branch", "release_claim", "forget_lane", "clear_session"):
        assert not hasattr(AuditOps, forbidden)
        assert not hasattr(FakeAuditOps(), forbidden)


def test_an_unmatched_artifact_is_still_there_after_the_audit(root: Path, tmp_path: Path):
    """The finding is a report: the directory the audit named is untouched."""
    directory = tmp_path / "left-alone"
    directory.mkdir()
    (directory / "work.txt").write_text("unmerged\n", encoding="utf-8")
    report = audit(root, ops=FakeAuditOps(worktrees=(primary(), worktree(str(directory), "issue-7"))), at=AT)
    assert str(directory) in names(report, UNMATCHED)
    assert directory.exists() and (directory / "work.txt").read_text(encoding="utf-8") == "unmerged\n"


def test_the_audit_refuses_to_run_without_an_operations_port(root: Path):
    with pytest.raises(ValueError):
        audit(root)


# --- the report itself ------------------------------------------------------


def test_the_report_is_machine_readable(root: Path):
    stamp("beaten-four", issue=3, agent=AGENT, root=root, branch="issue-3", worktree="/lanes/ao-3-x", at=AT)
    ops = FakeAuditOps(worktrees=(primary(), worktree("/lanes/ao-3-x", "issue-3"), lane()),
                       branches=("issue-628",))
    payload = audit(root, ops=ops, at=AT).to_json()
    assert payload["assessable"] is True
    assert payload["counts"][EXEMPT] == 1
    # The beaten worktree is explained; the beatless lane and its branch are not,
    # and both are counted — the report never silently drops what it cannot explain.
    assert payload["counts"][MATCHED] == 1
    assert payload["counts"][UNMATCHED] == 2
    assert payload["artifacts"]


def test_a_cannot_assess_report_says_so_in_its_json(root: Path):
    payload = audit(root, ops=FakeAuditOps(fail=("list_worktrees",)), at=AT).to_json()
    assert payload["assessable"] is False
    assert payload["reason"]


def test_the_description_names_every_unmatched_artifact(root: Path):
    ops = FakeAuditOps(worktrees=(primary(), lane()), branches=("issue-9",))
    text = describe(audit(root, ops=ops, at=AT))
    assert "/lanes/ao-628-deadbeef" in text
    assert "issue-9" in text
    assert "unmatched=2" in text


def test_the_description_of_a_cannot_assess_report_says_so(root: Path):
    text = describe(audit(root, ops=FakeAuditOps(fail=("list_worktrees",)), at=AT))
    assert "CANNOT-ASSESS" in text


def test_a_shelved_lane_is_still_counted_as_explained_by_its_beat(root: Path):
    """A shelved lane keeps its beat, so its worktree stays explained — not re-reported."""
    stamp("shelved-one", issue=628, agent=AGENT, root=root, branch="issue-628",
          worktree="/lanes/ao-628-deadbeef", state=SHELVED, note="unmerged work", at=AT)
    report = audit(root, ops=FakeAuditOps(worktrees=(primary(), lane())), at=AT)
    assert report.unmatched == []
    assert report.matched[0].evidence == ("beat:shelved-one",)


# --- the porcelain parse ----------------------------------------------------


def test_parse_worktrees_reads_a_porcelain_listing():
    text = (
        "worktree /repo\n"
        "HEAD 1111111111111111111111111111111111111111\n"
        "branch refs/heads/master\n"
        "\n"
        "worktree /lanes/ao-1-x\n"
        "HEAD 2222222222222222222222222222222222222222\n"
        "branch refs/heads/issue-1\n"
        "\n"
        "worktree /lanes/detached\n"
        "HEAD 3333333333333333333333333333333333333333\n"
        "detached\n"
        "\n"
    )
    entries = parse_worktrees(text)
    assert [entry.path for entry in entries] == ["/repo", "/lanes/ao-1-x", "/lanes/detached"]
    assert entries[0].primary and entries[0].branch == "master"
    assert entries[1].branch == "issue-1" and not entries[1].primary
    assert entries[2].detached and entries[2].branch == ""


def test_parse_worktrees_marks_locked_and_prunable_entries():
    text = (
        "worktree /repo\nHEAD 1111111111111111111111111111111111111111\nbranch refs/heads/master\n\n"
        "worktree /lanes/gone\nHEAD 2222222222222222222222222222222222222222\ndetached\nprunable gitdir file points to non-existent location\n\n"
    )
    entries = parse_worktrees(text)
    assert entries[1].prunable and not entries[1].locked


def test_parse_worktrees_tolerates_trailing_newlines():
    assert parse_worktrees("") == []
    assert len(parse_worktrees("worktree /repo\nHEAD abc\nbranch refs/heads/master\n")) == 1


def test_the_worktree_entry_is_json_serialisable():
    payload = json.loads(json.dumps(worktree("/lanes/x", "issue-1").to_json()))
    assert payload["path"] == "/lanes/x" and payload["branch"] == "issue-1"
