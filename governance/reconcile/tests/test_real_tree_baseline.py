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
    repository_venue,
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


def test_a_transient_git_failure_is_retried_and_recovers(scratch_repo: Path, monkeypatch):
    """#1620: on a busy box, a single `git log` call for a branch can race a
    concurrent ref-write from another session and come back empty/nonzero even
    though the branch is perfectly measurable. `_branch_age_seconds` must
    retry before fail-closing to OLD — a real, present branch must still be
    classified as recently-touched (young), not lost to one bad race."""
    from governance.reconcile import real_tree_baseline as rtb

    real_run = subprocess.run
    calls = {"n": 0}

    def flaky_run(cmd, **kwargs):
        if cmd[:2] == ["git", "-C"] and "log" in cmd:
            calls["n"] += 1
            if calls["n"] == 1:
                # simulate the race: git ran, but returned nothing usable
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(rtb, "subprocess", subprocess)
    monkeypatch.setattr(subprocess, "run", flaky_run)
    monkeypatch.setattr(rtb, "_BRANCH_AGE_RETRY_BACKOFF", (0.0, 0.0, 0.0))
    try:
        age = rtb._branch_age_seconds(scratch_repo, "issue-orphan-1", at=time.time())
    finally:
        monkeypatch.setattr(subprocess, "run", real_run)

    assert calls["n"] >= 2, "the retry never re-tried the git call"
    assert age is not None
    assert age < 3600, f"a just-created branch measured as {age}s old — the retry recovered a stale value"


def test_exhausted_retries_still_fail_closed_to_old(scratch_repo: Path, monkeypatch):
    """The retry absorbs a transient loss; it must never turn into a longer
    license to call a genuinely unmeasurable branch 'young'."""
    from governance.reconcile import real_tree_baseline as rtb

    real_run = subprocess.run

    def always_empty(cmd, **kwargs):
        if cmd[:2] == ["git", "-C"] and "log" in cmd:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", always_empty)
    monkeypatch.setattr(rtb, "_BRANCH_AGE_RETRY_BACKOFF", (0.0, 0.0, 0.0))
    try:
        age = rtb._branch_age_seconds(scratch_repo, "issue-orphan-1", at=time.time())
    finally:
        monkeypatch.setattr(subprocess, "run", real_run)

    assert age is None


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
    venue_of: Path | None = None,
    venue: str | None = None,
) -> None:
    """A fixture document. It declares a venue by default (#1321).

    ``venue_of`` names a repository whose instance the exemptions were measured
    in; a caller that wants a document measured *elsewhere* passes ``venue``
    explicitly. An exemption without a venue cannot be interpreted at all, so
    the default is the scratch repo rather than nothing.
    """
    moment = time.time() if measured_at is None else measured_at
    payload = {
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
    resolved_venue = venue if venue is not None else repository_venue(venue_of)
    if resolved_venue:
        payload["venue"] = {"git_common_dir": resolved_venue, "measured_on": "the test suite"}
    path.write_text(json.dumps(payload), encoding="utf-8")


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
        venue_of=scratch_repo,
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
        venue_of=scratch_repo,
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
        venue_of=scratch_repo,
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
        venue_of=scratch_repo,
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
    _write_quarantine(
        document, [{"kind": "branch", "name": "issue-unpinned", "reason": "no tip"}], venue_of=scratch_repo
    )
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
    lease, entries, venue = load_quarantine(document)
    assert lease.tracked_by.startswith("#")
    assert lease.state in {"open", "closed"}
    assert lease.max_age_hours > 0
    # The venue is what makes an entry interpretable at all (#1321): "the
    # artifact is gone" (a resolution, at its own venue) and "the artifact was
    # never here" (inert, at any other) are different facts, so a document that
    # does not declare one cannot be read. The *check* is the thing that
    # compares it against the instance it runs in — being a pristine clone is a
    # legitimate reason for this document not to be in force, not a defect, so
    # the invariant asserted HERE is that the declaration exists and is a
    # path-shaped identity, never that it equals this checkout's.
    assert venue.git_common_dir.startswith("/"), "the venue must be an absolute path"
    assert venue.git_common_dir.rstrip("/").endswith(".git"), (
        "the venue is the repository instance (git_common_dir), not a display name"
    )
    # An empty list is a legitimate state: every previously-named exemption has
    # been resolved (landed, pushed or reclaimed) and the document shrank to
    # nothing, exactly as designed (#1291) — it is not required to always hold
    # at least one entry, only to be well-formed when it holds any.
    keys = [(entry.kind, entry.name) for entry in entries]
    assert len(keys) == len(set(keys)), "two entries for one artifact: which one is the record?"
    for entry in entries:
        assert entry.kind in {"branch", "worktree"}
        assert entry.tip and len(entry.tip) == 40
        assert len(entry.reason.strip()) > 40, f"the reason must say something: {entry.name}"


# --- an exemption that protects nothing is refuted (#1311) -------------------
#
# The rules above measure an entry against *itself* (gone, moved, lapsed). These
# measure the claim the entry exists to make — that this work exists on no other
# ref — and they are the ones the five false reasons on issue #1311 slipped past.
# The positive halves matter as much as the negative one: a rule that refuted
# every exemption would satisfy the refutation test alone.


def _landed_by_content_branch(
    repo: Path, name: str = "issue-7000", *, landed: bool = True
) -> tuple[str, str | None]:
    """``(tip, landing)`` — a branch whose whole work IS on the default branch,
    yet stays a finding, built to the shape measured on issue #1311:

    * the two commits are **distinct objects carrying the same diff** (the branch
      commit is backdated, so it is never the landing commit itself) — a squash
      landing, which is what makes ancestry blind to it;
    * the landed path has drifted since — tree containment compares the *current*
      default branch, so it fails;
    * the landing's message names the pull request (``#7001``), never the issue,
      so the audit's candidate set for ``issue-7000`` is empty.

    ``landed=False`` builds the same branch with **no** landing on the default
    branch: the positive control, where the exemption must survive. Either way
    ``refs/remotes/origin/master`` is created, because without a default ref the
    refuter proves nothing (fail-closed) — which is the state every other fixture
    in this file is in.
    """
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "base with f.txt")
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "-b", name, base)
    (repo / "f.txt").write_text("landed\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git_commit_with_date(
        repo,
        "ask the clock invariant where a lane is shaped (#7001)",
        when_epoch=time.time() - 30 * 24 * 3600,
    )
    tip = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "master")
    landing: str | None = None
    if landed:
        (repo / "f.txt").write_text("landed\n", encoding="utf-8")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-q", "-m", "ask the clock invariant where a lane is shaped (#7001)")
        landing = _git(repo, "rev-parse", "HEAD").strip()
        (repo / "f.txt").write_text("drifted\n", encoding="utf-8")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-q", "-m", "drift the landed path")
    _git(repo, "update-ref", "refs/remotes/origin/master", _git(repo, "rev-parse", "master").strip())
    assert tip != (landing or ""), "the branch commit must be a different object from the landing"
    return tip, landing


def test_an_exemption_whose_work_is_on_the_default_branch_is_refuted(
    scratch_repo: Path, tmp_path: Path
):
    """The negative control for #1311: the rule MUST fire, by name, and it must
    still *honour* the entry so the ONE named failure is the demand to shrink the
    document (the artifact is on disk and the audit still cannot explain it)."""
    branch_tip, landing = _landed_by_content_branch(scratch_repo)
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    _write_quarantine(
        document,
        [
            {
                "kind": "branch",
                "name": "issue-7000",
                "tip": branch_tip,
                "reason": "rule 17: no commit on the default branch carries its work",
            }
        ],
        venue_of=scratch_repo,
    )
    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=0
    )
    assert verdict.assessable
    assert not verdict.ok, "an exemption that protects nothing must fail the verdict"
    assert [e.name for e in verdict.refuted_quarantine] == ["issue-7000"]
    assert [e.name for e in verdict.quarantined] == ["issue-7000"], (
        "refuting must not stop the entry honouring: the single named failure is the "
        "demand that the document shrink, not a second finding for the same artifact"
    )
    assert "issue-7000" not in {e.name for e in verdict.new_violations}
    text = verdict.describe()
    assert "REFUTED-QUARANTINE branch issue-7000" in text
    assert "already on the default branch" in text, text
    assert landing and landing[:12] in verdict.refuted_quarantine[0].reason, (
        "the refusal must name the commit that carries the work, not merely claim it"
    )


def test_the_same_artifact_is_honoured_when_its_work_is_not_landed(
    scratch_repo: Path, tmp_path: Path
):
    """The positive control: the SAME branch with no landing on the default branch,
    and a default ref the refuter can read. If the rule refuted this, it would be
    satisfied by a rule that excuses nothing."""
    tip, landing = _landed_by_content_branch(scratch_repo, name="issue-7002", landed=False)
    assert landing is None
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    _write_quarantine(
        document,
        [
            {
                "kind": "branch",
                "name": "issue-7002",
                "tip": tip,
                "reason": "rule 17: work exists nowhere but this box",
            }
        ],
        venue_of=scratch_repo,
    )
    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=0
    )
    assert verdict.assessable
    assert verdict.refuted_quarantine == ()
    assert verdict.ok, verdict.describe()
    assert [e.name for e in verdict.quarantined] == ["issue-7002"]
    assert "REFUTED-QUARANTINE" not in verdict.describe()


def test_a_worktree_holding_its_own_uncommitted_work_is_not_refuted(
    scratch_repo: Path, tmp_path: Path
):
    """The ground that decides a worktree (measured on #1311: two of the kept
    entries are exactly this shape — a landed HEAD plus work on no branch at all).
    Residue a machine rewrites is a different thing from work, and only the second
    keeps the exemption."""
    tip, _landing = _landed_by_content_branch(scratch_repo, name="issue-7003")
    worktree = tmp_path / "lane-wt"
    _git(scratch_repo, "worktree", "add", "-q", str(worktree), "issue-7003")
    (worktree / "f.txt").write_text("half-written lane work\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    _write_baseline(
        baseline,
        [{"kind": "branch", "name": "issue-7003", "reason": "the branch this worktree is on: known"}],
    )
    document = tmp_path / "quarantine.json"
    _write_quarantine(
        document,
        [
            {
                "kind": "worktree",
                "name": str(worktree),
                "tip": tip,
                "reason": "rule 17: its own uncommitted work is on no branch at all",
            }
        ],
        venue_of=scratch_repo,
    )
    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=0
    )
    assert verdict.assessable
    assert [e.name for e in verdict.quarantined] == [str(worktree)]
    assert verdict.refuted_quarantine == (), (
        f"a tree holding its own uncommitted work must keep its exemption: {verdict.describe()}"
    )
    assert verdict.ok, verdict.describe()


def test_a_document_that_is_not_in_force_here_is_never_refuted(scratch_repo: Path, tmp_path: Path):
    """Refutation is venue-scoped like every other tooth (#1321): a document
    measured in another instance is inert here, so it can neither excuse nor be
    refuted on this checkout's disk."""
    tip, _landing = _landed_by_content_branch(scratch_repo, name="issue-7004")
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    _write_quarantine(
        document,
        [
            {
                "kind": "branch",
                "name": "issue-7004",
                "tip": tip,
                "reason": "rule 17: measured in another repository instance",
            }
        ],
        venue="/somewhere/else/.git",
    )
    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=0
    )
    assert verdict.assessable
    assert verdict.refuted_quarantine == ()
    assert verdict.quarantined == ()
    assert [e.name for e in verdict.inapplicable_quarantine] == ["issue-7004"]
    assert "issue-7004" in {e.name for e in verdict.new_violations}, (
        "an inert document excuses nothing, so the artifact stays a finding here"
    )


def test_the_refuted_count_reaches_the_ledger(scratch_repo: Path, tmp_path: Path):
    """A shrunk document must stay visible in the trail after the edit that
    removed the entries (the ledger record is what outlives the document)."""
    from governance.reconcile import ledger

    tip, _landing = _landed_by_content_branch(scratch_repo, name="issue-7005")
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    _write_quarantine(
        document,
        [
            {
                "kind": "branch",
                "name": "issue-7005",
                "tip": tip,
                "reason": "rule 17: no commit on the default branch carries its work",
            }
        ],
        venue_of=scratch_repo,
    )
    check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=0
    )
    records = [r for r in ledger.read(scratch_repo) if r["kind"] == ledger.REAL_TREE_VERDICT]
    assert records, "check_real_tree must write a real-tree-verdict ledger record"
    assert records[-1]["refuted_quarantine"] == 1
    assert records[-1]["ok"] is False


# --- the venue of an exemption (#1321) ---------------------------------------
#
# #1300 quarantined 23 artifacts by name; #1317 emptied the document because on
# any checkout but one box those entries "match nothing" and therefore FAIL as
# stale exemptions — measured `0 new-and-old, 538 stale; 23 stale quarantine
# exemption(s)` on a pristine clone. Both readings were right about their own
# checkout and wrong about the other: an entry names a disk artifact of ONE
# repository instance. So the document declares the instance it was measured in,
# and a check running anywhere else must (a) honour nothing from it (fail-closed:
# the artifact stays a finding) and (b) not fail on it (its staleness is a claim
# about a disk this checkout cannot observe) — while the instance it does speak
# for keeps every tooth: the tests above, which declare `venue_of=scratch_repo`.


def test_an_exemption_measured_elsewhere_is_inert_here(scratch_repo: Path, tmp_path: Path):
    """The #1317 case, both halves, differing ONLY in the declared venue.

    Same artifact, same entry, same tip: at another venue it honours nothing and
    is not fatal; at its own venue it is honoured and reported. Without the
    second half this would be satisfied by a document that never honours
    anything anywhere.
    """
    tip = _ancient_branch(scratch_repo, "issue-box-local")
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    entry = {"kind": "branch", "name": "issue-box-local", "tip": tip, "reason": "rule 17: box-local"}

    elsewhere = tmp_path / "quarantine-elsewhere.json"
    _write_quarantine(elsewhere, [entry], venue="/somewhere/else/.git")
    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=elsewhere, ops=RepoOps(scratch_repo), grace_hours=24
    )
    assert verdict.assessable
    assert verdict.quarantined == (), "no exemption may be honoured outside its own venue"
    assert [e.name for e in verdict.inapplicable_quarantine] == ["issue-box-local"]
    assert verdict.stale_quarantine == (), (
        "an entry that is merely inert here must not fail here — that is exactly the red"
        " #1317 measured on a pristine clone"
    )
    assert "issue-box-local" in {e.name for e in verdict.new_violations}, (
        "the artifact must stay a finding: a document that speaks for another venue cannot"
        " excuse it, so inert is fail-closed, never a pass"
    )
    assert "NOT IN FORCE" in verdict.describe()
    assert "issue-box-local" in verdict.describe(), "an inert exemption is still reported by name"

    own_venue = tmp_path / "quarantine-own.json"
    _write_quarantine(own_venue, [entry], venue_of=scratch_repo)
    same = check_real_tree(
        scratch_repo, baseline, quarantine_path=own_venue, ops=RepoOps(scratch_repo), grace_hours=24
    )
    assert same.ok, same.describe()
    assert [e.name for e in same.quarantined] == ["issue-box-local"]
    assert same.inapplicable_quarantine == ()


def test_entries_without_a_declared_venue_are_cannot_assess(scratch_repo: Path, tmp_path: Path):
    """Without a venue there is no way to tell "gone" from "never here": rc 2, never a pass."""
    tip = _ancient_branch(scratch_repo, "issue-no-venue")
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    _write_quarantine(
        document,
        [{"kind": "branch", "name": "issue-no-venue", "tip": tip, "reason": "no venue declared"}],
        venue="",
    )
    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=24
    )
    assert not verdict.assessable
    assert not verdict.ok
    assert "venue" in verdict.reason
    assert "CANNOT-ASSESS" in verdict.describe()


def test_an_empty_document_is_inert_and_its_lease_is_not_evaluated(
    scratch_repo: Path, tmp_path: Path
):
    """A lease bounding no entry cannot turn anything green, so it is not a violation.

    GR-12: a check whose pass and fail paths collapse into the same verdict — a
    lapsed lease over an empty exemption list — is a formality, not a check.
    This does not open a green either: an artifact the document does not name
    (any of them, once the list is empty) fails by name as unbaselined-and-old.
    """
    _ancient_branch(scratch_repo, "issue-not-named-anywhere")
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    _write_quarantine(
        document,
        [],
        state="closed",
        measured_at=time.time() - 30 * 3600,
        venue="",
    )
    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=24
    )
    assert verdict.assessable
    assert verdict.stale_quarantine == ()
    assert verdict.quarantined == ()
    assert "empty" in verdict.describe()
    assert "issue-not-named-anywhere" in {e.name for e in verdict.new_violations}


def test_the_venue_identity_is_shared_by_every_worktree_of_one_instance(
    scratch_repo: Path, tmp_path: Path
):
    """Why `git_common_dir`, and not the worktree path: lanes must answer like their checkout.

    A lane worktree is the normal place this gate runs, and the shared checkout
    is where the artifacts live — one identity for both is what makes a
    box-scoped document usable from a lane at all. A different repository
    instance (any clone, any other init) must answer differently, which is the
    whole property the venue rule rests on.
    """
    lane = tmp_path / "lane-wt"
    _git(scratch_repo, "worktree", "add", "-q", "-b", "issue-lane", str(lane))
    assert repository_venue(lane) == repository_venue(scratch_repo)

    other = tmp_path / "another-instance"
    other.mkdir()
    _git(other, "init", "-q", "-b", "master")
    assert repository_venue(other) != repository_venue(scratch_repo)
    assert repository_venue(other).startswith("/")
