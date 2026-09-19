"""The exemption document's central claim, re-measured and refused when it is false (#1338).

`scripts/check-reconcile.sh` §6e provokes every way a named exemption could be
*abused* — an entry matching nothing, a moved tip, a lapsed lease, a document
from another repository instance, an unreadable document — and the unit suite
provokes the #1311 *refutation* (an entry whose whole work is already on the
default branch). None of that re-measures the claim the document exists to make:
**this artifact's work exists nowhere else.**

That gap is not hypothetical. Measured on #1338: three of the document's rows
asserted "carried by no remote-tracking ref" and, minutes later, two of them were
false — the tips had been pushed to `origin` by name, which is *this document's
own rule 2* (`push it` — the branch reaches a remote, so the artifact stops being
the only copy, and the entry goes in the same reviewed edit). The gate saw
nothing, because a preserved tip is still unmatched and still unlanded: only the
sentence in the row changed truth-value.

So this module proves two things, in the two directions the repository requires
(GR-12 — a control that cannot fail is a formality):

* a row whose "exists nowhere else" claim is false IS refused, by name, in all
  three measured shapes — work on the default branch, a tip preserved on a
  remote, and a worktree protecting nothing but machine-managed residue;
* the same row, while the claim is true, is still honoured — so the refusal
  cannot be satisfied by a checker that refuses everything.

The last test re-measures the **shipped** document's own rows with it, and states
what an EMPTY document can still get wrong — its terminal state for #1338, once
every row it held named work that a live `git ls-remote` shows preserved on a
remote and each row was resolved into the baseline. Its failure message names the
row and the reviewed edit it needs, because that is exactly the signal rule 2 asks
for and no other arm produces.
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

from governance.isolation.worktree import foreign_uncommitted  # noqa: E402
from governance.reconcile.landing import RepoLanding  # noqa: E402
from governance.reconcile.real_tree_baseline import (  # noqa: E402
    check_real_tree,
    load_quarantine,
    repository_venue,
)
from governance.reconcile.sweep import RepoOps  # noqa: E402

SHIPPED_DOCUMENT = REPO_ROOT / "governance/reconcile/real-tree-quarantine.json"


# --- the claim checker -------------------------------------------------------


def claim_refusals(root: Path | str, rows: list[dict]) -> list[tuple[dict, str]]:
    """The rows whose "its work exists nowhere else" claim is no longer true.

    One rule per kind, each the *only* thing an exemption of that kind protects:

    * a **branch** has no uncommitted state, so its claim is that the commit
      itself is the only copy — a tip carried by any remote-tracking ref has a
      copy elsewhere, which is rule 2's `push it` resolution;
    * a **worktree**'s committed work is judged by the gate's own refuter, and
      what only the working tree can hold is work on no branch at all — so a
      tree whose uncommitted paths are all machine-managed residue is protecting
      nothing.

    Deliberately narrow: it is a *claim* checker, not a second refuter, so it
    never restates the patch-identity proof the verdict already runs.
    """
    prover = RepoLanding(root)
    refusals: list[tuple[dict, str]] = []
    for row in rows:
        kind, name = row["kind"], row["name"]
        if kind == "worktree":
            try:
                own = list(foreign_uncommitted(name))
            except Exception as exc:  # noqa: BLE001 — unreadable is never a refusal
                refusals.append((row, f"it could not be read ({type(exc).__name__})"))
                continue
            if not own:
                refusals.append(
                    (
                        row,
                        "it holds no uncommitted work of its own, and every dirty path it has is "
                        "machine-managed residue, so the exemption protects nothing",
                    )
                )
            continue
        carriers = _remote_refs_containing(root, row["tip"])
        if carriers:
            refusals.append(
                (
                    row,
                    f"its tip {row['tip'][:12]} is carried by {', '.join(carriers)} — the work is "
                    "preserved on a remote, so this exemption no longer protects the only copy",
                )
            )
            continue
        landing = prover.surplus_patch_identity(row["tip"])
        if landing:
            refusals.append(
                (
                    row,
                    f"its whole work is already on the default branch at {landing[:12]} "
                    "(patch identity), so this exemption protects nothing",
                )
            )
    return refusals


def _remote_refs_containing(root: Path | str, tip: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "for-each-ref", "--contains", tip, "refs/remotes/",
         "--format=%(refname:short)"],
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout


# --- fixtures ----------------------------------------------------------------


@pytest.fixture()
def scratch_repo(tmp_path: Path) -> Path:
    """A real repository with a real default ref, so nothing fails open."""
    repo = tmp_path / "scratch"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "gate@example.com")
    _git(repo, "config", "user.name", "Gate")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "base with f.txt")
    _git(repo, "update-ref", "refs/remotes/origin/master", _git(repo, "rev-parse", "master").strip())
    return repo


def _commit_backdated(repo: Path, message: str, when_epoch: float) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(when_epoch))
    import os

    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
        "GIT_AUTHOR_NAME": "Gate",
        "GIT_AUTHOR_EMAIL": "gate@example.com",
        "GIT_COMMITTER_NAME": "Gate",
        "GIT_COMMITTER_EMAIL": "gate@example.com",
    }
    _git(repo, "commit", "-q", "-m", message, env=env)


def _landed_branch(repo: Path, name: str) -> tuple[str, str]:
    """``(tip, landing)`` — a branch whose whole work IS on the default branch.

    Built to the shape measured on #1311: the branch commit and the squash that
    landed it are **distinct objects carrying the same diff** (different
    messages, and the branch commit is backdated), the landed path drifts
    afterwards so tree containment is blind, and the landing names the pull
    request rather than the issue, so the audit's declared convention finds
    nothing. Ancestry, containment and the declared convention all fail; only
    patch identity over the default branch's own commits sees the landing.
    """
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "-b", name, base)
    (repo / "f.txt").write_text("landed\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _commit_backdated(repo, "ask the clock invariant where a lane is shaped (#7001)", time.time() - 30 * 24 * 3600)
    tip = _git(repo, "rev-parse", name).strip()
    _git(repo, "checkout", "-q", "master")
    (repo / "f.txt").write_text("landed\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "ask the clock invariant where a lane is shaped (#7001) (#7002)")
    landing = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "f.txt").write_text("drifted\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "drift the landed path")
    _git(repo, "update-ref", "refs/remotes/origin/master", _git(repo, "rev-parse", "master").strip())
    assert tip != landing, "the branch commit and its landing must be distinct objects"
    return tip, landing


def _unlanded_branch(repo: Path, name: str) -> str:
    """A branch whose work really is on no other ref — the positive control."""
    base = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "-b", name, base)
    (repo / "g.txt").write_text("lane work\n", encoding="utf-8")
    _git(repo, "add", "g.txt")
    _commit_backdated(repo, f"{name} lane", time.time() - 30 * 24 * 3600)
    tip = _git(repo, "rev-parse", name).strip()
    _git(repo, "checkout", "-q", "master")
    return tip


def _write_baseline(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps({"note": "test", "entries": entries}), encoding="utf-8")


def _write_document(path: Path, rows: list[dict], venue_of: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "note": "test",
                "tracked_by": "#1338",
                "tracking": {
                    "state": "open",
                    "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "measured_by": "the test suite",
                    "max_age_hours": 24,
                },
                "venue": {"git_common_dir": repository_venue(venue_of), "measured_on": "the test suite"},
                "quarantine": rows,
            }
        ),
        encoding="utf-8",
    )


def _row(kind: str, name: str, tip: str) -> dict:
    return {
        "kind": kind,
        "name": name,
        "tip": tip,
        "reason": "rule 17: no commit on the default branch and no remote ref carries its work",
    }


# --- the provocation ---------------------------------------------------------


def test_a_row_asserting_exists_nowhere_else_is_refused_when_the_work_is_on_the_default_branch(
    scratch_repo: Path, tmp_path: Path
):
    """The refusal the document cannot make for itself: a row claiming the work
    exists nowhere else, for a tip whose whole patch IS on the default branch."""
    tip, landing = _landed_branch(scratch_repo, "issue-7100")
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    row = _row("branch", "issue-7100", tip)
    _write_document(document, [row], venue_of=scratch_repo)

    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=0
    )
    assert verdict.assessable
    assert not verdict.ok, "an exemption protecting nothing must fail the verdict"
    assert [e.name for e in verdict.refuted_quarantine] == ["issue-7100"]
    assert "REFUTED-QUARANTINE branch issue-7100" in verdict.describe()
    assert landing[:12] in verdict.refuted_quarantine[0].reason

    refusals = claim_refusals(scratch_repo, [row])
    assert [r[0]["name"] for r in refusals] == ["issue-7100"]
    assert "on the default branch" in refusals[0][1], refusals[0][1]


def test_a_row_asserting_exists_nowhere_else_is_refused_when_its_tip_reached_a_remote(
    scratch_repo: Path, tmp_path: Path
):
    """The half nothing else measures (#1338): the commit is unlanded and
    unmatched, so the gate is green — and its work still exists somewhere else,
    because rule 2's `push it` put it on a remote. The row's own sentence is the
    only thing that changed, which is why it has to be re-measured."""
    tip = _unlanded_branch(scratch_repo, "issue-7101")
    _git(scratch_repo, "update-ref", "refs/remotes/origin/issue-7101", tip)
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [])
    document = tmp_path / "quarantine.json"
    row = _row("branch", "issue-7101", tip)
    _write_document(document, [row], venue_of=scratch_repo)

    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=0
    )
    assert verdict.ok, verdict.describe()
    assert [e.name for e in verdict.quarantined] == ["issue-7101"], (
        "the gate honours it — the artifact is unmatched and unlanded — which is exactly why the "
        "claim needs its own arm"
    )
    assert verdict.refuted_quarantine == ()

    refusals = claim_refusals(scratch_repo, [row])
    assert [r[0]["name"] for r in refusals] == ["issue-7101"]
    assert "preserved on a remote" in refusals[0][1], refusals[0][1]
    assert "origin/issue-7101" in refusals[0][1]


def test_a_worktree_row_protecting_only_machine_managed_residue_is_refused(
    scratch_repo: Path, tmp_path: Path
):
    """The third lie shape: a worktree exemption whose only ground is residue a
    machine rewrites (`.board/`, `.fleet/`) is protecting nothing at all."""
    tip = _unlanded_branch(scratch_repo, "issue-7102")
    _git(scratch_repo, "update-ref", "refs/remotes/origin/issue-7102", tip)
    worktree = tmp_path / "lane-wt"
    _git(scratch_repo, "worktree", "add", "-q", str(worktree), "issue-7102")
    (worktree / ".board").mkdir()
    (worktree / ".board/focus.json").write_text("{}\n", encoding="utf-8")

    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, [{"kind": "branch", "name": "issue-7102", "reason": "known"}])
    document = tmp_path / "quarantine.json"
    row = _row("worktree", str(worktree), tip)
    _write_document(document, [row], venue_of=scratch_repo)
    verdict = check_real_tree(
        scratch_repo, baseline, quarantine_path=document, ops=RepoOps(scratch_repo), grace_hours=0
    )
    assert [e.name for e in verdict.quarantined] == [str(worktree)], verdict.describe()

    refusals = claim_refusals(scratch_repo, [row])
    assert [r[0]["name"] for r in refusals] == [str(worktree)]
    assert "machine-managed residue" in refusals[0][1], refusals[0][1]


def test_the_same_row_is_accepted_while_the_claim_is_true(scratch_repo: Path):
    """The negative control. Without it, a checker that refused everything would
    pass every arm above — the formality GR-12 forbids."""
    tip = _unlanded_branch(scratch_repo, "issue-7103")
    assert claim_refusals(scratch_repo, [_row("branch", "issue-7103", tip)]) == []

    worktree = scratch_repo.parent / "kept-wt"
    _git(scratch_repo, "worktree", "add", "-q", str(worktree), "issue-7103")
    (worktree / "half-written.txt").write_text("lane work, on no branch at all\n", encoding="utf-8")
    assert claim_refusals(scratch_repo, [_row("worktree", str(worktree), tip)]) == []


# --- the shipped document ----------------------------------------------------


def test_the_shipped_document_re_measures_its_own_rows():
    """The real document, re-measured with the same checker.

    A failure here is not "the document is malformed" — it is that a row asserts
    work exists nowhere else and the measurement says otherwise. The remedy is
    the one rule 2 already names: resolve it (land it, or push it so it reaches a
    remote), then remove the entry in the same reviewed edit that records the
    resolution and add it to real-tree-baseline.json.

    An EMPTY document is that resolution carried to its end, so the empty state is
    asserted rather than skipped: there are then no shipped rows to re-measure, and
    the arm says so, but it still refuses the two ways an empty document can lie —
    by honouring something it does not name, and by being reported as a stale
    exemption itself. `assert entries` (this module's own first shape, written when
    the document still held rows) would have made the terminal state unreachable,
    which is the one thing a shrinking document must not be.
    """
    if not SHIPPED_DOCUMENT.exists():
        pytest.skip("the exemption document is not present in this checkout")
    lease, entries, venue = load_quarantine(SHIPPED_DOCUMENT)
    assert venue.git_common_dir == repository_venue(REPO_ROOT), (
        "this document speaks for another repository instance; re-measure it in the venue it names"
    )

    if not entries:
        verdict = check_real_tree(
            REPO_ROOT,
            REPO_ROOT / "governance/reconcile/real-tree-baseline.json",
            quarantine_path=SHIPPED_DOCUMENT,
            ops=RepoOps(REPO_ROOT),
        )
        assert verdict.assessable, verdict.reason
        assert verdict.quarantined == (), "an empty document must honour nothing"
        assert verdict.stale_quarantine == (), "an empty document cannot be a stale exemption"
        assert "declares no exemptions" in verdict.describe(), verdict.describe()
        return

    rows = [
        {"kind": entry.kind, "name": entry.name, "tip": entry.tip, "reason": entry.reason}
        for entry in entries
    ]
    refusals = claim_refusals(REPO_ROOT, rows)
    assert not refusals, "\n".join(
        f"row {row['kind']} {row['name']} @{row['tip'][:12]} no longer exists nowhere else: {why}"
        for row, why in refusals
    ) + (
        "\n\nEach row above must be resolved (land it, or push it so it reaches a remote) and then "
        "removed from real-tree-quarantine.json in the same reviewed edit that records the "
        f"resolution — tracked by {lease.tracked_by}."
    )
