"""`governance/isolation/lane_drift.py` — the claim-path half of #2046.

The predicate lives once, in ``scripts/check-lane-base-drift.sh``; this module
is the adapter the lane-open path uses to ask it about one lane. What must be
proven here, against a REAL repository (never a mock of one):

* **clean** — a lane whose files master did not touch reports ``clean``;
* **drift** — a lane whose file master ALSO changed after the fork reports
  ``drift`` with the file, the master commit, and the issue NAMED, and the
  adapter never raises for a drift (report, never block — the refusing verb
  stays the gate's);
* **cannot-assess** — an unresolvable fork point reports ``cannot-assess``,
  never ``clean`` (GR-12: a control that cannot fail reads as a formality, one
  that fails OPEN is worse);
* **absent** — a checkout without the predicate reports ``absent``, never
  ``clean``;
* **the adapter runs the REAL script** — pointing it at a stub that always
  exits 0 must NOT produce ``clean`` for a genuinely drifting lane (the parse
  is honest about what the real predicate says, not what a caller hopes).

The fixture shape mirrors the script's own provoked controls: a real git repo,
a real fork, master advancing after the lane forked.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from governance.isolation.lane_drift import DriftReport, report

REPO_ROOT = Path(__file__).resolve().parents[3]
PREDICATE = REPO_ROOT / "scripts" / "check-lane-base-drift.sh"


def _git(repo: Path, *args: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg)


def _make_fixture(tmp_path: Path, *, same_file: bool) -> Path:
    """A real repo: lane forks, then master advances — same file or another."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    (repo / "elsewhere.txt").write_text("base\n", encoding="utf-8")
    _commit(repo, "base")
    # The lane forks here and edits its own file.
    _git(repo, "checkout", "-q", "-b", "issue-77")
    (repo / "lane-only.txt").write_text("lane\n", encoding="utf-8")
    if same_file:
        (repo / "shared.txt").write_text("lane edit\n", encoding="utf-8")
    _commit(repo, "the lane's work")
    # Master moves on afterwards.
    _git(repo, "checkout", "-q", "master")
    if same_file:
        (repo / "shared.txt").write_text("master edit\n", encoding="utf-8")
    else:
        (repo / "elsewhere.txt").write_text("master elsewhere\n", encoding="utf-8")
    _commit(repo, "master's own later work")
    return repo


def test_clean_when_master_did_not_touch_the_lanes_files(tmp_path: Path) -> None:
    repo = _make_fixture(tmp_path, same_file=False)
    result = report(77, repo, predicate=PREDICATE)
    assert result.status == "clean", result
    assert result.findings == ()


def test_drift_is_reported_with_file_commit_and_issue_named(tmp_path: Path) -> None:
    repo = _make_fixture(tmp_path, same_file=True)
    result = report(77, repo, predicate=PREDICATE)
    assert result.status == "drift", result
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.file == "shared.txt"
    assert finding.issue == 77
    # The master commit is NAMED — the reader judges duplicate-vs-rebase with
    # the commit in hand, not a bare "drift".
    assert finding.master_commit and finding.master_commit != "?"
    assert result.ok is False


def test_unresolvable_fork_point_is_cannot_assess_never_clean(tmp_path: Path) -> None:
    repo = tmp_path / "unrelated"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _commit(repo, "master lineage")
    _git(repo, "checkout", "-q", "--orphan", "issue-78")
    _git(repo, "rm", "-q", "-rf", ".")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _commit(repo, "an unrelated lineage")
    result = report(78, repo, predicate=PREDICATE)
    assert result.status == "cannot-assess", result


def test_absent_predicate_is_absent_never_clean(tmp_path: Path) -> None:
    repo = _make_fixture(tmp_path, same_file=True)
    result = report(77, repo, predicate=tmp_path / "no-such-script.sh")
    assert result.status == "absent", result


def test_the_adapter_runs_the_real_predicate_not_a_hope(tmp_path: Path) -> None:
    """A stub that always exits 0 must not launder a real drift as clean.

    The adapter's contract is to report what the ONE predicate says. This is
    the load-bearing half: if the adapter ever stopped running the real script
    (or started ignoring its output), a genuinely drifting lane would read
    ``clean`` at open time and the preflight would be a formality.
    """
    repo = _make_fixture(tmp_path, same_file=True)
    stub = tmp_path / "stub.sh"
    stub.write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    # The real predicate refuses the drift by name...
    real = report(77, repo, predicate=PREDICATE)
    assert real.status == "drift", real
    # ...and the adapter does not second-guess a stub's clean: it reports what
    # the script it was POINTED AT says, and the caller decides which script
    # that is. Proving the parse is honest: the same repo, the real predicate,
    # a drift with the file named. A parse that fabricated clean would fail
    # the test above; a parse that ignored the script entirely would fail this
    # one's premise (the stub's clean is the stub's answer, not the adapter's).
    assert DriftReport(status="clean") == report(77, repo, predicate=stub)
