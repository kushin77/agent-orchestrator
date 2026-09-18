"""Tests for the real-tree baseline (#740, item 1).

`scripts/check-reconcile.sh` proves the audit only against a synthetic fixture
repo; it never once points `audit()` at the real repository it governs. These
tests exercise the baseline mechanism itself (new violation / stale entry /
clean match) against a small real scratch repo, independent of the shell gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from governance.reconcile.real_tree_baseline import (  # noqa: E402
    BaselineUnavailable,
    check_real_tree,
    load_baseline,
    load_quarantine,
)
from governance.reconcile.sweep import RepoOps  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout


@pytest.fixture()
def scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "scratch"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "gate@example.com")
    _git(repo, "config", "user.name", "Gate")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "branch", "issue-orphan-1")
    _git(repo, "branch", "issue-orphan-2")
    return repo


def _write_baseline(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps({"note": "test", "entries": entries}), encoding="utf-8")


def test_matching_baseline_is_ok(scratch_repo: Path, tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    _write_baseline(
        baseline,
        [
            {"kind": "branch", "name": "issue-orphan-1", "reason": "known, unresolved"},
            {"kind": "branch", "name": "issue-orphan-2", "reason": "known, unresolved"},
        ],
    )
    verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo), grace_hours=0)
    assert verdict.assessable
    assert verdict.ok
    assert verdict.new_violations == ()
    assert verdict.stale_entries == ()


def test_new_unbaselined_artifact_fails(scratch_repo: Path, tmp_path: Path):
    """A NEW unmatched artifact absent from the baseline is a violation, named."""
    baseline = tmp_path / "baseline.json"
    _write_baseline(
        baseline,
        [{"kind": "branch", "name": "issue-orphan-1", "reason": "known, unresolved"}],
    )
    verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo), grace_hours=0)
    assert verdict.assessable
    assert not verdict.ok
    names = {(e.kind, e.name) for e in verdict.new_violations}
    assert ("branch", "issue-orphan-2") in names
    assert verdict.stale_entries == ()


def test_stale_entry_is_reported_but_does_not_fail(scratch_repo: Path, tmp_path: Path):
    """A baselined artifact that is no longer unmatched is reported by name and
    counted — but does NOT fail the gate (second #740 follow-up). Unlike
    landed-baseline.json's commits, disk artifacts are MEANT to disappear once
    reclaimed or cleaned up; a stale entry records that cleanup already
    happened, which must never itself be a violation."""
    baseline = tmp_path / "baseline.json"
    _write_baseline(
        baseline,
        [
            {"kind": "branch", "name": "issue-orphan-1", "reason": "known, unresolved"},
            {"kind": "branch", "name": "issue-orphan-2", "reason": "known, unresolved"},
            {"kind": "branch", "name": "issue-does-not-exist", "reason": "stale by construction"},
        ],
    )
    verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo), grace_hours=0)
    assert verdict.assessable
    assert verdict.ok, f"a stale entry alone must not fail the gate: {verdict.describe()}"
    assert verdict.new_violations == ()
    stale_names = {(e.kind, e.name) for e in verdict.stale_entries}
    assert ("branch", "issue-does-not-exist") in stale_names
    assert "STALE" in verdict.describe()


def test_stale_and_new_violation_together_still_fails_on_the_new_one(scratch_repo: Path, tmp_path: Path):
    """A stale entry never masks a genuine new-and-old violation sitting next
    to it — the two are independent."""
    baseline = tmp_path / "baseline.json"
    _write_baseline(
        baseline,
        [
            {"kind": "branch", "name": "issue-orphan-1", "reason": "known, unresolved"},
            {"kind": "branch", "name": "issue-does-not-exist", "reason": "stale by construction"},
        ],
    )
    verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo), grace_hours=0)
    assert not verdict.ok
    assert ("branch", "issue-orphan-2") in {(e.kind, e.name) for e in verdict.new_violations}
    assert ("branch", "issue-does-not-exist") in {(e.kind, e.name) for e in verdict.stale_entries}


def test_prune_stale_drops_only_stale_entries(scratch_repo: Path, tmp_path: Path):
    """`prune_stale` rewrites the baseline dropping exactly the entries the
    audit itself just reported as no-longer-unmatched — never a still-live
    artifact, never `new_violations` or `young`."""
    from governance.reconcile.real_tree_baseline import prune_stale

    baseline = tmp_path / "baseline.json"
    _write_baseline(
        baseline,
        [
            {"kind": "branch", "name": "issue-orphan-1", "reason": "known, unresolved"},
            {"kind": "branch", "name": "issue-orphan-2", "reason": "known, unresolved"},
            {"kind": "branch", "name": "issue-does-not-exist", "reason": "stale by construction"},
        ],
    )
    verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo), grace_hours=0)
    assert len(verdict.stale_entries) == 1

    removed = prune_stale(baseline, verdict)
    assert removed == 1

    reloaded = load_baseline(baseline)
    kept_names = {e.name for e in reloaded}
    assert kept_names == {"issue-orphan-1", "issue-orphan-2"}

    # a second prune, with a fresh (clean) verdict, is a no-op
    clean_verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo), grace_hours=0)
    assert clean_verdict.ok
    assert prune_stale(baseline, clean_verdict) == 0


def test_prune_stale_is_a_noop_when_not_assessable(scratch_repo: Path, tmp_path: Path):
    from governance.reconcile.real_tree_baseline import RealTreeVerdict, prune_stale

    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [{"kind": "branch", "name": "issue-x", "reason": "r"}])
    before = baseline.read_text(encoding="utf-8")
    removed = prune_stale(baseline, RealTreeVerdict(assessable=False, reason="unreadable"))
    assert removed == 0
    assert baseline.read_text(encoding="utf-8") == before


def test_malformed_baseline_is_cannot_assess(scratch_repo: Path, tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{ not json", encoding="utf-8")
    verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo), grace_hours=0)
    assert not verdict.assessable
    assert not verdict.ok


def test_load_baseline_rejects_missing_fields(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"entries": [{"kind": "branch"}]}), encoding="utf-8")
    with pytest.raises(BaselineUnavailable):
        load_baseline(baseline)


def test_the_real_repo_baseline_file_exists_and_is_well_formed():
    """The committed baseline must exist and parse — but NOT be checked against
    the live disk of whoever runs this suite: a fleet that is actively working
    creates worktrees/branches constantly, so a test tied to that snapshot goes
    red for any agent, at any time, for no defect of its own (measured: #1050
    merged clean and the very next gate run failed on three brand-new,
    perfectly legitimate worktrees). The fixture tests below cover the real
    behaviour; the shell gate (`scripts/check-reconcile.sh`) is what actually
    audits the real tree at gate time, in its own process, against its own
    disk."""
    baseline_path = REPO_ROOT / "governance" / "reconcile" / "real-tree-baseline.json"
    assert baseline_path.exists(), "governance/reconcile/real-tree-baseline.json must exist (#740)"
    entries = load_baseline(baseline_path)
    assert entries, "the real-tree baseline must not be empty"
    for entry in entries:
        assert entry.kind in ("worktree", "branch")
        assert entry.name
        assert entry.reason


# --- age grace (#740 follow-up): a young unmatched artifact must not fail ---


def _git_commit_with_date(repo: Path, message: str, *, when_epoch: float) -> None:
    import os as _os

    when = str(int(when_epoch))
    env = dict(**_os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    result = subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", message],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr


def test_a_young_unmatched_branch_is_reported_but_does_not_fail(scratch_repo: Path, tmp_path: Path):
    """A branch whose tip commit is minutes old — a lane just created — must be
    reported as `young`, counted, but must NOT make the gate fail."""
    now = time.time()
    _git(scratch_repo, "checkout", "-q", "-b", "issue-brand-new")
    _git_commit_with_date(scratch_repo, "fresh lane", when_epoch=now - 60)  # 1 minute old
    _git(scratch_repo, "checkout", "-q", "master")

    baseline = tmp_path / "baseline.json"
    _write_baseline(
        baseline,
        [{"kind": "branch", "name": "issue-orphan-1", "reason": "known, unresolved"}],
    )
    verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo), grace_hours=24, at=now)
    young_names = {e.name for e in verdict.young}
    assert "issue-brand-new" in young_names
    assert verdict.ok, f"a young artifact must not fail the gate: {verdict.describe()}"
    new_names = {e.name for e in verdict.new_violations}
    assert "issue-brand-new" not in new_names


def test_an_old_unmatched_branch_still_fails_by_name(scratch_repo: Path, tmp_path: Path):
    """A branch whose tip commit is well past the grace window, and is not
    baselined, still fails — by name."""
    now = time.time()
    _git(scratch_repo, "checkout", "-q", "-b", "issue-ancient")
    _git_commit_with_date(scratch_repo, "ancient lane", when_epoch=now - 30 * 24 * 3600)  # 30 days old
    _git(scratch_repo, "checkout", "-q", "master")

    baseline = tmp_path / "baseline.json"
    _write_baseline(
        baseline,
        [
            {"kind": "branch", "name": "issue-orphan-1", "reason": "known, unresolved"},
            {"kind": "branch", "name": "issue-orphan-2", "reason": "known, unresolved"},
        ],
    )
    verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo), grace_hours=24, at=now)
    assert not verdict.ok
    new_names = {e.name for e in verdict.new_violations}
    assert "issue-ancient" in new_names
    young_names = {e.name for e in verdict.young}
    assert "issue-ancient" not in young_names


def test_a_young_worktree_is_reported_but_does_not_fail(scratch_repo: Path, tmp_path: Path):
    """Same rule for a worktree: freshly created, reported `young`, not failed —
    the exact shape of the false-positive #1050 caused (a subagent's own
    worktree, the gate's own scratch worktree)."""
    now = time.time()
    lane = tmp_path / "fresh-worktree"
    _git(scratch_repo, "worktree", "add", "-q", "-b", "issue-fresh-lane", str(lane), "master")

    baseline = tmp_path / "baseline.json"
    _write_baseline(
        baseline,
        [
            {"kind": "branch", "name": "issue-orphan-1", "reason": "known, unresolved"},
            {"kind": "branch", "name": "issue-orphan-2", "reason": "known, unresolved"},
        ],
    )
    verdict = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo), grace_hours=24, at=now)
    young_kinds_names = {(e.kind, e.name) for e in verdict.young}
    assert ("worktree", str(lane)) in young_kinds_names
    assert verdict.ok, f"a young worktree must not fail the gate: {verdict.describe()}"


def test_mutation_disabling_the_grace_window_makes_the_young_artifact_fail(
    scratch_repo: Path, tmp_path: Path
):
    """Self-control for the grace predicate itself: with `grace_hours=0` a
    brand-new branch is no longer young, and the gate DOES fail on it — proving
    the grace window, not some other accident, is what keeps a young artifact
    from failing. This is the provocation the coordinator asked for: a broken
    (mutated) grace predicate must be caught by this suite."""
    now = time.time()
    _git(scratch_repo, "checkout", "-q", "-b", "issue-brand-new-2")
    _git_commit_with_date(scratch_repo, "fresh lane", when_epoch=now - 60)
    _git(scratch_repo, "checkout", "-q", "master")

    baseline = tmp_path / "baseline.json"
    _write_baseline(
        baseline,
        [{"kind": "branch", "name": "issue-orphan-1", "reason": "known, unresolved"}],
    )

    healthy = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo), grace_hours=24, at=now)
    assert healthy.ok
    assert "issue-brand-new-2" in {e.name for e in healthy.young}

    mutated = check_real_tree(scratch_repo, baseline, ops=RepoOps(scratch_repo), grace_hours=0, at=now)
    assert not mutated.ok
    assert "issue-brand-new-2" in {e.name for e in mutated.new_violations}


def test_default_grace_hours_come_from_the_declared_policy():
    """The grace window is not a bare literal in this module — it is read from
    governance/policy/lease.py, the single declared source of every fleet
    lease/TTL."""
    from governance.policy import lease
    from governance.reconcile.real_tree_baseline import DEFAULT_GRACE_HOURS

    assert DEFAULT_GRACE_HOURS == lease.REAL_TREE_GRACE_HOURS


def test_unmeasurable_age_is_treated_as_old_fail_closed(scratch_repo: Path, tmp_path: Path):
    """A branch git cannot date (a name with no matching ref, simulating an
    unreadable/garbled artifact) must be treated as OLD, never as young — an
    unmeasurable age must never be read as license to ignore an artifact."""
    from governance.reconcile.real_tree_baseline import artifact_age_seconds

    age = artifact_age_seconds(scratch_repo, "branch", "issue-does-not-exist-at-all", at=time.time())
    assert age is None

    age_worktree = artifact_age_seconds(
        scratch_repo, "worktree", str(tmp_path / "no-such-worktree-dir"), at=time.time()
    )
    assert age_worktree is None


# --- vanished-between-list-and-measure (#885 follow-up) ---------------------
#
# Concurrent lane churn during `make verify` can delete a worktree/branch
# between the disk *listing* (`audit()`) and this module's *age measurement*
# of the same unmatched artifact. That must be reported as VANISHED — never a
# violation, and never confused with a genuinely present-but-unreadable
# artifact (which stays fail-closed to OLD, `artifact_age_seconds` unchanged).


class _FakeAuditOps:
    """Reports artifacts that were never real on disk — simulating "the
    listing saw it, but it is gone by the time anything looks again"."""

    def __init__(self, *, worktrees, branches):
        self._worktrees = worktrees
        self._branches = branches

    def list_worktrees(self):
        return self._worktrees

    def list_local_branches(self):
        return self._branches

    def active_claims(self):
        return {}

    def landed_issues(self):
        return set()


def test_a_vanished_branch_is_reported_but_does_not_fail(scratch_repo: Path, tmp_path: Path):
    from governance.reconcile.audit import WorktreeEntry

    ops = _FakeAuditOps(
        worktrees=[WorktreeEntry(path=str(scratch_repo), primary=True)],
        branches=["issue-vanished-branch"],  # never a real ref in scratch_repo
    )
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    verdict = check_real_tree(scratch_repo, baseline, ops=ops, grace_hours=0)
    assert verdict.assessable
    assert verdict.ok, "a vanished artifact must never fail the gate"
    assert verdict.new_violations == ()
    names = {(e.kind, e.name) for e in verdict.vanished}
    assert ("branch", "issue-vanished-branch") in names


def test_a_vanished_worktree_is_reported_but_does_not_fail(scratch_repo: Path, tmp_path: Path):
    from governance.reconcile.audit import WorktreeEntry

    gone = str(tmp_path / "worktree-that-is-already-gone")
    ops = _FakeAuditOps(
        worktrees=[
            WorktreeEntry(path=str(scratch_repo), primary=True),
            WorktreeEntry(path=gone, branch="lane-gone"),
        ],
        branches=[],
    )
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    verdict = check_real_tree(scratch_repo, baseline, ops=ops, grace_hours=0)
    assert verdict.assessable
    assert verdict.ok, "a vanished artifact must never fail the gate"
    assert verdict.new_violations == ()
    names = {(e.kind, e.name) for e in verdict.vanished}
    assert ("worktree", gone) in names


def test_artifact_vanished_true_for_missing_worktree_path(tmp_path: Path, scratch_repo: Path):
    from governance.reconcile.real_tree_baseline import artifact_vanished

    assert artifact_vanished(scratch_repo, "worktree", str(tmp_path / "does-not-exist")) is True


def test_artifact_vanished_false_for_a_real_branch(scratch_repo: Path):
    from governance.reconcile.real_tree_baseline import artifact_vanished

    assert artifact_vanished(scratch_repo, "branch", "issue-orphan-1") is False


def test_artifact_vanished_true_for_a_deleted_branch_ref(scratch_repo: Path):
    """A branch that resolved when listed but was deleted before this check
    runs — the concurrent-cleanup race this module now distinguishes."""
    from governance.reconcile.real_tree_baseline import artifact_vanished

    _git(scratch_repo, "branch", "-D", "issue-orphan-2")
    assert artifact_vanished(scratch_repo, "branch", "issue-orphan-2") is True


def test_present_but_unmeasurable_still_fails_closed_old_not_vanished(
    scratch_repo: Path, tmp_path: Path
):
    """The pre-existing fail-closed case (#740) must be untouched: an artifact
    that genuinely exists but whose age this audit cannot read is OLD, never
    vanished and never young — `artifact_vanished` must say False for it."""
    from governance.reconcile.real_tree_baseline import artifact_vanished

    worktree = tmp_path / "present-worktree"
    worktree.mkdir()
    # A real, existing path: not vanished, even though its age may later be
    # unmeasurable in some other scenario (permission, corrupt log, etc.) —
    # this module's vanished check is existence/resolution only.
    assert artifact_vanished(scratch_repo, "worktree", str(worktree)) is False


def test_vanished_count_is_recorded_on_the_ledger(scratch_repo: Path, tmp_path: Path):
    import sys as _sys

    from governance.reconcile import ledger
    from governance.reconcile.audit import WorktreeEntry

    ops = _FakeAuditOps(
        worktrees=[WorktreeEntry(path=str(scratch_repo), primary=True)],
        branches=["issue-vanished-for-ledger"],
    )
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    check_real_tree(scratch_repo, baseline, ops=ops, grace_hours=0)
    records = [r for r in ledger.read(scratch_repo) if r["kind"] == ledger.REAL_TREE_VERDICT]
    assert records, "check_real_tree must write a real-tree-verdict ledger record"
    assert records[-1]["vanished"] == 1


# --- the named, leased quarantine (#1291) ------------------------------------
#
# Exemptions are the only thing in this mechanism that can turn a finding into a
# pass, so every test here is about what they must REFUSE to excuse: an artifact
# the document does not name, an artifact whose tip has moved, and a lease that is
# closed or older than its own declared bound. The positive half — an entry naming
# a real, present, unmatched artifact at its exact tip IS honoured — exists so the
# refusals cannot be satisfied by a rule that simply excuses nothing.


def _write_quarantine(
    path: Path,
    entries: list[dict],
    *,
    tracked_by: str = "#1291",
    state: str = "open",
    measured_at: float | None = None,
    max_age_hours: float = 24,
) -> None:
    moment = time.time() if measured_at is None else measured_at
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "note": "test",
                "tracked_by": tracked_by,
                "tracking": {
                    "state": state,
                    "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment)),
                    "measured_by": "the test suite",
                    "max_age_hours": max_age_hours,
                },
                "quarantine": entries,
            }
        ),
        encoding="utf-8",
    )


def _ancient_branch(repo: Path, name: str) -> str:
    """A branch, 30 days old, belonging to no baseline: a real finding."""
    now = time.time()
    _git(repo, "checkout", "-q", "-b", name)
    _git_commit_with_date(repo, f"{name} lane", when_epoch=now - 30 * 24 * 3600)
    _git(repo, "checkout", "-q", "master")
    return _git(repo, "rev-parse", name).strip()


def test_a_named_entry_at_the_recorded_tip_is_honoured(scratch_repo: Path, tmp_path: Path):
    """The positive half: without it, a rule that excused nothing would pass."""
    tip = _ancient_branch(scratch_repo, "issue-rule17-work")
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    _write_quarantine(
        document,
        [{"kind": "branch", "name": "issue-rule17-work", "tip": tip, "reason": "rule 17: work exists nowhere else"}],
    )
    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=24
    )
    assert verdict.ok, verdict.describe()
    assert [e.name for e in verdict.quarantined] == ["issue-rule17-work"]
    assert "issue-rule17-work" not in {e.name for e in verdict.new_violations}
    assert "issue-rule17-work" in verdict.describe(), "a quarantined artifact is still reported by name"


def test_an_entry_that_matches_nothing_fails_as_a_stale_exemption(scratch_repo: Path, tmp_path: Path):
    """The shrink: the document can only ever lose entries, never rot in place."""
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    _write_quarantine(
        document,
        [{"kind": "branch", "name": "issue-long-gone", "tip": "0" * 40, "reason": "nothing is there"}],
    )
    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=24
    )
    assert not verdict.ok
    assert [e.name for e in verdict.stale_quarantine] == ["issue-long-gone"]
    assert "issue-long-gone" in verdict.describe()


def test_an_artifact_whose_tip_has_moved_is_not_absorbed(scratch_repo: Path, tmp_path: Path):
    """A moved branch is a different artifact: it fails immediately, by name."""
    tip = _ancient_branch(scratch_repo, "issue-moved")
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    _write_quarantine(
        document,
        [{"kind": "branch", "name": "issue-moved", "tip": "f" * 40, "reason": "recorded at some other tip"}],
    )
    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=24
    )
    assert not verdict.ok
    assert "issue-moved" in {e.name for e in verdict.new_violations}
    assert "issue-moved" in {e.name for e in verdict.stale_quarantine}
    assert tip  # the recorded tip and the real one are what the verdict compared


@pytest.mark.parametrize(
    "lease",
    [
        {"state": "closed"},
        {"state": "open", "measured_at": time.time() - 30 * 3600, "max_age_hours": 24},
    ],
    ids=["tracking-issue-closed", "measurement-past-its-lease"],
)
def test_a_lease_that_does_not_hold_honours_nothing(scratch_repo: Path, tmp_path: Path, lease):
    """An exemption nobody can show is current is not an exemption."""
    tip = _ancient_branch(scratch_repo, "issue-loan")
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    _write_quarantine(
        document,
        [{"kind": "branch", "name": "issue-loan", "tip": tip, "reason": "would be honoured"}],
        **lease,
    )
    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=24
    )
    assert not verdict.ok
    assert verdict.quarantined == ()
    assert "#1291" in {e.name for e in verdict.stale_quarantine}
    assert "issue-loan" in {e.name for e in verdict.new_violations}


def test_an_unreadable_quarantine_is_cannot_assess(scratch_repo: Path, tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    document.write_text("{ not json", encoding="utf-8")
    verdict = check_real_tree(scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo))
    assert not verdict.assessable
    assert not verdict.ok
    assert "CANNOT-ASSESS" in verdict.describe()


def test_an_entry_without_a_tip_is_refused_outright(scratch_repo: Path, tmp_path: Path):
    """A name alone would excuse whatever appeared under it later."""
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    _write_quarantine(document, [{"kind": "branch", "name": "issue-unpinned", "reason": "no tip"}])
    verdict = check_real_tree(scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo))
    assert not verdict.assessable
    assert "tip" in verdict.reason


def test_an_absent_quarantine_document_excuses_nothing(scratch_repo: Path, tmp_path: Path):
    _ancient_branch(scratch_repo, "issue-unexcused")
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    verdict = check_real_tree(
        scratch_repo,
        baseline,
        quarantine_path=tmp_path / "not-written.json",
        ops=RepoOps(scratch_repo),
        grace_hours=24,
    )
    assert not verdict.ok
    assert verdict.quarantined == ()
    assert "issue-unexcused" in {e.name for e in verdict.new_violations}


def test_the_real_quarantine_document_is_well_formed_and_every_entry_is_pinned():
    """The tracked document is load-bearing: it is read, and every entry pins a tip.

    Its entries name artifacts whose work exists nowhere else (AGENTS.md rule 17),
    so this asserts the *shape* that makes them safe to honour — never their
    number, which is allowed to shrink as the fleet resolves them.
    """
    document = REPO_ROOT / "governance" / "reconcile" / "real-tree-quarantine.json"
    assert document.exists(), "the named quarantine must exist for the gate to be honest about it"
    lease, entries = load_quarantine(document)
    assert lease.tracked_by.startswith("#")
    assert lease.state in {"open", "closed"}
    assert lease.max_age_hours > 0
    assert entries, "an empty document cannot excuse the artifacts the gate reports"
    keys = [(entry.kind, entry.name) for entry in entries]
    assert len(keys) == len(set(keys)), "two entries for one artifact: which one is the record?"
    for entry in entries:
        assert entry.kind in {"branch", "worktree"}
        assert entry.tip and len(entry.tip) == 40
        assert len(entry.reason.strip()) > 40, f"the reason must say something: {entry.name}"
