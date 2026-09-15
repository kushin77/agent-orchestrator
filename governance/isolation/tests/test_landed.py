"""The landed-history enforcement surface, and its quarantine (issue #287).

Issue #287's second half is that the ticket-trailer rule had no surface over
history that had already landed: the lane audit only ever looked at live
worktrees, and ``audit --all`` was wired into nothing. ``governance/isolation/
landed.py`` is that surface, and these tests pin the property that makes it
usable on a repository whose landed history does **not** satisfy the rule: the
predicate's frozen class boundary is consumed, not restated; the measured residue
is recorded by commit; the recording can only shrink; and every input whose
absence would otherwise produce green is refused instead.

Each refusal is provoked here rather than asserted in prose — a check whose pass
and fail paths collapse is a formality (GR-12). The scratch repositories put the
enforcement boundary at their own seed commit, so the range under test contains
exactly the commits the test authored, and the real repository's boundary is
never what decides the verdict.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from governance.isolation import cli, landed, trailer
from governance.isolation.landed import VERDICT_CANNOT_ASSESS, VERDICT_NOT_OK, VERDICT_OK
from governance.isolation.trailer import LandedResult, PredicateUnavailable

from conftest import REPO_ROOT, commit, git  # noqa: E402  (suite-local helper; conftest bootstraps sys.path)

SEED_TRAILER = "Refs kushin77/agent-orchestrator#263"


def write_baseline(path: Path, *shas: str, code: str = "commit-missing-ticket-trailer") -> Path:
    """A recorded-legacy baseline naming ``shas``, shaped like the committed one."""
    payload = {
        "measured_at": "2026-09-15",
        "measured_head": shas[0] if shas else "",
        "entries": [{"sha": sha, "code": code, "why": "test fixture"} for sha in shas],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def boundary(tmp_path: Path) -> tuple[Path, str]:
    """A scratch repository whose seed commit is a *clean* enforcement boundary.

    Clean on purpose: the shared predicate checks the boundary commit too, so a
    seed without a trailer would add an ``enforcement-gate-missing-trailer``
    finding to every case and hide the finding each test is actually about.
    """
    root = tmp_path / "landed-repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "master", str(root)], check=True, capture_output=True, text=True
    )
    git(root, "config", "user.name", "Human Dev")
    git(root, "config", "user.email", "human@example.com")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(root, "add", "seed.txt")
    git(root, "commit", "-q", "-m", "the enforcement boundary", "-m", SEED_TRAILER)
    return root, git(root, "rev-parse", "HEAD").stdout.strip()


def assessed_range(base: str) -> str:
    return f"{base}..HEAD"


# --- the verdict, and what makes it unproven --------------------------------


def test_a_clean_post_boundary_history_is_ok(boundary, tmp_path: Path):
    repo, base = boundary
    commit(repo, "work.txt", "do the work", trailer=SEED_TRAILER)
    result = landed.assess(repo, assessed_range(base), write_baseline(tmp_path / "b.json"), base)
    assert result.verdict == VERDICT_OK
    assert result.quarantined == ()
    assert result.unenforced == ()
    assert result.assessed == 1


def test_an_unrecorded_non_compliant_commit_is_refused_by_name(boundary, tmp_path: Path):
    repo, base = boundary
    commit(repo, "work.txt", "a commit with no ticket reference at all")
    result = landed.assess(repo, assessed_range(base), write_baseline(tmp_path / "b.json"), base)
    assert result.verdict == VERDICT_NOT_OK
    assert [finding.code for finding in result.unenforced] == ["commit-missing-ticket-trailer"]
    assert any("commit-missing-ticket-trailer:" in line for line in result.lines())


def test_a_subject_only_reference_is_refused_by_the_shared_predicate(boundary, tmp_path: Path):
    """A substring test accepts this; the delegated positional rule must not."""
    repo, base = boundary
    commit(repo, "work.txt", f"{SEED_TRAILER}: the ref is only in the subject")
    result = landed.assess(repo, assessed_range(base), write_baseline(tmp_path / "b.json"), base)
    assert result.verdict == VERDICT_NOT_OK
    assert [finding.code for finding in result.unenforced] == ["commit-ref-only-in-subject"]


def test_a_recorded_legacy_commit_is_accepted_and_still_reported(boundary, tmp_path: Path):
    repo, base = boundary
    sha = commit(repo, "work.txt", "a commit with no ticket reference at all")
    result = landed.assess(repo, assessed_range(base), write_baseline(tmp_path / "b.json", sha), base)
    assert result.verdict == VERDICT_OK
    assert [finding.sha for finding in result.quarantined] == [sha[:12]]
    # Recorded, not silently accepted: the report names it on every run.
    assert any("recorded legacy" in line for line in result.lines())


def test_a_recorded_entry_that_now_complies_is_stale(boundary, tmp_path: Path):
    """The baseline can only shrink: a stale entry is a failure, not a grandfather."""
    repo, base = boundary
    sha = commit(repo, "work.txt", "do the work", trailer=SEED_TRAILER)
    result = landed.assess(repo, assessed_range(base), write_baseline(tmp_path / "b.json", sha), base)
    assert result.verdict == VERDICT_NOT_OK
    assert [entry.sha for entry in result.stale] == [sha]
    assert any("quarantine-entry-stale" in line for line in result.lines())


def test_a_recorded_entry_outside_the_range_is_not_assessed(boundary, tmp_path: Path):
    """An unreconcilable baseline is unproven — CANNOT-ASSESS, never a pass."""
    repo, base = boundary
    commit(repo, "work.txt", "do the work", trailer=SEED_TRAILER)
    result = landed.assess(repo, assessed_range(base), write_baseline(tmp_path / "b.json", base), base)
    assert result.verdict == VERDICT_CANNOT_ASSESS
    assert [entry.sha for entry in result.unassessed] == [base]
    assert any("quarantine-entry-not-assessed" in line for line in result.lines())


def test_an_empty_range_is_cannot_assess(boundary, tmp_path: Path):
    """Nothing assessed is not a pass — the classic vacuous green (GR-12)."""
    repo, base = boundary
    result = landed.assess(repo, "HEAD..HEAD", write_baseline(tmp_path / "b.json"), base)
    assert result.verdict == VERDICT_CANNOT_ASSESS
    assert "no non-merge commit" in result.reason


def test_an_unresolvable_range_is_cannot_assess(boundary, tmp_path: Path):
    repo, base = boundary
    result = landed.assess(repo, "no-such-ref..HEAD", write_baseline(tmp_path / "b.json"), base)
    assert result.verdict == VERDICT_CANNOT_ASSESS
    assert "does not resolve" in result.reason


def test_a_missing_baseline_fails_rather_than_skipping(boundary, tmp_path: Path):
    """Otherwise deleting the file switches the check off, and a skipped check is not a red."""
    repo, base = boundary
    commit(repo, "work.txt", "do the work", trailer=SEED_TRAILER)
    result = landed.assess(repo, assessed_range(base), tmp_path / "absent.json", base)
    assert result.verdict == VERDICT_NOT_OK
    assert "baseline-missing" in result.reason


def test_a_malformed_baseline_is_refused(boundary, tmp_path: Path):
    repo, base = boundary
    commit(repo, "work.txt", "do the work", trailer=SEED_TRAILER)
    broken = tmp_path / "broken.json"
    broken.write_text('{"entries": [{"sha": "not-a-commit", "code": "", "why": ""}]}', encoding="utf-8")
    result = landed.assess(repo, assessed_range(base), broken, base)
    assert result.verdict == VERDICT_NOT_OK
    assert "baseline-malformed" in result.reason


def test_an_unavailable_predicate_is_cannot_assess(boundary, tmp_path: Path, monkeypatch):
    """An unrunnable rule is unproven, never satisfied (no false green)."""
    repo, base = boundary
    commit(repo, "work.txt", "do the work", trailer=SEED_TRAILER)

    def unavailable(*_args, **_kwargs):
        raise PredicateUnavailable("the shared predicate is missing")

    monkeypatch.setattr(landed, "run_landed", unavailable)
    result = landed.assess(repo, assessed_range(base), write_baseline(tmp_path / "b.json"), base)
    assert result.verdict == VERDICT_CANNOT_ASSESS
    assert "unavailable" in result.reason


def test_a_predicate_that_fails_without_a_parseable_finding_is_cannot_assess(boundary, tmp_path: Path, monkeypatch):
    """A red this module cannot explain must not become a green."""
    repo, base = boundary
    commit(repo, "work.txt", "do the work", trailer=SEED_TRAILER)
    monkeypatch.setattr(landed, "run_landed", lambda *_a, **_k: LandedResult(returncode=1, output="something else went wrong\n"))
    result = landed.assess(repo, assessed_range(base), write_baseline(tmp_path / "b.json"), base)
    assert result.verdict == VERDICT_CANNOT_ASSESS
    assert "without a parseable finding" in result.reason


def test_a_predicate_that_passes_while_reporting_findings_is_cannot_assess(boundary, tmp_path: Path, monkeypatch):
    """The contradiction proves the adapter is not simply reading the exit code."""
    repo, base = boundary
    commit(repo, "work.txt", "do the work", trailer=SEED_TRAILER)
    contradicting = LandedResult(returncode=0, output="  FAIL  commit-missing-ticket-trailer:0123456789ab\n")
    monkeypatch.setattr(landed, "run_landed", lambda *_a, **_k: contradicting)
    result = landed.assess(repo, assessed_range(base), write_baseline(tmp_path / "b.json"), base)
    assert result.verdict == VERDICT_CANNOT_ASSESS
    assert "exited 0" in result.reason


# --- the CLI contract --------------------------------------------------------


def test_the_cli_maps_every_verdict_onto_the_tri_state_code(boundary, tmp_path: Path, capsys):
    repo, base = boundary
    commit(repo, "work.txt", "do the work", trailer=SEED_TRAILER)
    empty = write_baseline(tmp_path / "b.json")
    ok = cli.main(["enforce", "--main", str(repo), "--range", assessed_range(base), "--baseline", str(empty), "--gate", base])
    assert ok == 0
    assert "isolation-enforce: OK" in capsys.readouterr().out
    commit(repo, "second.txt", "a commit with no ticket reference at all")
    bad = cli.main(["enforce", "--main", str(repo), "--range", assessed_range(base), "--baseline", str(empty), "--gate", base])
    assert bad == 1
    assert "isolation-enforce: NOT-OK" in capsys.readouterr().err
    unproven = cli.main(["enforce", "--main", str(repo), "--range", "HEAD..HEAD", "--baseline", str(empty), "--gate", base])
    assert unproven == 2
    assert "isolation-enforce: CANNOT-ASSESS" in capsys.readouterr().err


def test_the_default_range_is_not_relative_to_a_remote_tracking_ref():
    """A remote-relative default is empty in the shared checkout: a vacuous green."""
    assert landed.DEFAULT_RANGE == "HEAD"


# --- the committed baseline is real, and describes this repository -----------


def test_the_committed_baseline_is_well_formed():
    """Its shape is pinned, its size deliberately is not.

    The baseline is allowed to shrink to nothing as the recorded commits are
    fixed; pinning a count would turn a real remediation into a red test. What is
    pinned is that every entry is usable as a key and says why it is there.
    """
    baseline = landed.load_baseline(landed.BASELINE_PATH)
    assert baseline.measured_at, "the recorded residue must say when it was measured"
    assert len(baseline.measured_head) == 40
    vocabulary = {
        "commit-missing-ticket-trailer",
        "commit-ref-only-in-subject",
        "commit-ref-outside-the-trailer-block",
    }
    for entry in baseline.entries:
        assert len(entry.sha) == 40
        assert entry.code in vocabulary, f"{entry.sha[:12]} records a finding the predicate does not print"
        assert entry.why, "every recorded commit says why it is recorded"


def test_every_committed_baseline_entry_is_a_real_commit_in_this_clone():
    """A hand-edited or mistyped entry would otherwise grand nothing, silently."""
    shallow = git(REPO_ROOT, "rev-parse", "--is-shallow-repository").stdout.strip()
    if shallow == "true":
        pytest.skip("a shallow clone cannot prove the baseline's commits exist")
    baseline = landed.load_baseline(landed.BASELINE_PATH)
    for entry in baseline.entries:
        exists = git(REPO_ROOT, "cat-file", "-e", f"{entry.sha}^{{commit}}", check=False)
        assert exists.returncode == 0, f"{entry.sha[:12]} is recorded but is not a commit in this clone"
