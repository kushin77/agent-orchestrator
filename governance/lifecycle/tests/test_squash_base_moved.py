"""A squash merge whose base moved between the branch cut and the merge (#1298).

The #1098 arm admitted a lane that contains the commit the squash landed as **and** whose
landing *is* the verified tree. That second half was written for a squash merge composed
from the branch's own base -- ``test_lane_records.py``'s ``_squash_landing`` builds exactly
that -- and it is *unsatisfiable* the moment anything else lands on the default branch
between the branch cut and the merge, because a squash merge composes its landing from the
base **at merge time**: the landing carries a sibling's content the branch tip never had,
so ``trees_are_identical(verified, landing)`` is false however green the work was.

The arm therefore could not fire for the case it exists for -- a control that cannot fire,
the inverse of this repository's GR-12 -- and the remaining venue then measured a commit
that never landed. Measured on this repository's own history: branch tip ``17dc00a``'s
change, ``git patch-id --stable`` ``c36dcd3c223e23ac4afbd71616bfb3263e302190``, landed as
``870eb26`` with the same patch id and identical content at every one of the twelve paths
the tip changed, while ``git diff --quiet 17dc00a 870eb26`` is not clean and
``git merge-base --is-ancestor 17dc00a 870eb26`` exits 1.

What these tests pin, on a **real** repository with a **real** squash merge and a **real**
lane:

* the base-moved item is ADMITTED by the shipping predicate -- not by widening it into
  "anything goes";
* each clause of the restated predicate is shown to be the *sole* clause that could have
  answered its shape, so no clause is decoration;
* the arms still REFUSE what they exist for: an unmerged pull request, a lane that is not
  at and does not contain the landing, and a landing that does not carry the verified
  change. Every refusal is asserted on the ACTUAL line the port printed, printed beside
  the expectation so a mismatch is visible instead of inferred.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from governance.lifecycle.cli import (
    GhOps,
    change_relation,
    commit_is_contained,
    journal_path,
    landing_carries_change,
    trees_are_identical,
)

ISSUE = 1298
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The real-tree fixture from the issue: a branch tip whose change landed as another
#: commit, with the whole trees unequal and the tip not an ancestor of the landing.
REAL_VERIFIED = "17dc00a"
REAL_LANDING = "870eb26"


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "lifecycle-test",
        "GIT_AUTHOR_EMAIL": "lifecycle-test@agents.invalid",
        "GIT_COMMITTER_NAME": "lifecycle-test",
        "GIT_COMMITTER_EMAIL": "lifecycle-test@agents.invalid",
    }
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env, check=True
    )
    return result.stdout.strip()


def _git_rc(cwd: Path, *args: str) -> int:
    """A git exit code, for the questions whose *answer* is the code."""
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True).returncode


def _stub_make(tmp_path: Path, monkeypatch) -> Path:
    """A ``make`` on ``PATH`` that records the tree it ran in and passes.

    The repository's established seam (``test_lane_records.py``, ``test_verify_port.py``):
    a gate may not run the composite gate once per case. It records the resolved HEAD from
    *inside* the run, because the tree it runs in is the thing under test.
    """
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    log = tmp_path / "gate-ran-in"
    make = shim / "make"
    make.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s %s\\n\' "$PWD" "$(git -C "$PWD" rev-parse HEAD)" >> "$STUB_GATE_LOG"\n'
        "printf 'verify: PASS (120 of 120 checks)\\n'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    make.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(shim), "/usr/bin", "/bin"]))
    monkeypatch.setenv("STUB_GATE_LOG", str(log))
    monkeypatch.setenv("AO_LIFECYCLE_GATE_RETRIES", "0")
    return log


def _gate_ran_in(log: Path) -> tuple[str, str]:
    """``(tree, commit)`` the stub gate last ran in."""
    where, commit = log.read_text(encoding="utf-8").split()[-2:]
    return where, commit


def _world(root: Path, *, variant: str) -> dict:
    """A real repository, a real squash merge and a real lane, in one of four shapes.

    * ``base_moved`` -- a sibling lands on the default branch BETWEEN the branch cut and
      the merge, so the squash landing is composed from a base the branch tip never had.
      The tip's own change (an addition, a modification and a deletion) is in the landing
      byte for byte; the whole trees are not equal. This is the shape #1298 is about.
    * ``drift`` -- the same squash, but the branch then takes one commit the squash did not
      carry, so the landing stops carrying the tip's change: it must stay refused.
    * ``pre_merge`` -- the lane is a worktree of the default branch from BEFORE the
      landing, so it contains nothing of this item.
    * ``unmerged`` -- the pull request never merged: there is no landing to stand for.
    """
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
    _git(repo, "config", "user.name", "lifecycle-test")
    _git(repo, "config", "user.email", "lifecycle-test@agents.invalid")
    (repo / "README.md").write_text("the repository\n", encoding="utf-8")
    (repo / "touched.txt").write_text("before\n", encoding="utf-8")
    (repo / "doomed.txt").write_text("removed by the verified change\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "-b", f"issue-{ISSUE}")
    (repo / "verified.txt").write_text("the verified work\n", encoding="utf-8")
    (repo / "touched.txt").write_text("after\n", encoding="utf-8")
    _git(repo, "rm", "-q", "doomed.txt")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the verified head")
    verified = _git(repo, "rev-parse", "HEAD")

    landing = ""
    if variant != "unmerged":
        # A SIBLING lands while the pull request is open. Every later step composes on top
        # of it, which is exactly what a squash merge does at merge time.
        _git(repo, "checkout", "-q", "master")
        (repo / "other.txt").write_text("a sibling that landed while the pull request was open\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "a sibling landing that moves the base")
        _git(repo, "merge", "--squash", "-q", f"issue-{ISSUE}")
        _git(repo, "commit", "-q", "-m", f"the squash landing (#{ISSUE})")
        landing = _git(repo, "rev-parse", "HEAD")
        if variant == "drift":
            _git(repo, "checkout", "-q", f"issue-{ISSUE}")
            (repo / "unsquashed.txt").write_text("content the squash did not carry\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "a commit the squash did not carry")
            verified = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "master")

    lane = root / "lane"
    if variant == "pre_merge":
        # The default branch as it was BEFORE this item landed: it carries none of the work.
        _git(repo, "worktree", "add", "-q", "--detach", str(lane), base)
    else:
        _git(repo, "worktree", "add", "-q", "-b", f"lane-{ISSUE}", str(lane), "master")
    lane_head = _git(lane, "rev-parse", "HEAD")

    # The record the port reads (``_lane_records``): a real lane whose worktree is there.
    lanes = repo / ".fleet" / "lanes"
    lanes.mkdir(parents=True)
    (lanes / f"s-{ISSUE}.json").write_text(
        json.dumps({"session_id": f"s-{ISSUE}", "issue": ISSUE, "worktree": str(lane)}) + "\n",
        encoding="utf-8",
    )
    return {
        "repo": repo,
        "lane": lane,
        "base": base,
        "verified": verified,
        "landing": landing,
        "lane_head": lane_head,
    }


def _item(verified: str, landing: str) -> dict:
    """The lifecycle item this port reads its merge commit from."""
    return {
        "issue": ISSUE,
        "pr": {
            "number": ISSUE,
            "state": "merged" if landing else "open",
            "branch": f"issue-{ISSUE}",
            "head_commit": verified,
            "merge_commit": landing,
        },
    }


def _refusal(repo: Path, world: dict) -> str:
    """The ACTUAL refusal line, or a loud placeholder saying the port admitted it."""
    ops = GhOps(root=repo, record={"items": [_item(world["verified"], world["landing"])]})
    try:
        admitted = ops.record_verification(ISSUE, world["verified"], world["landing"])
    except RuntimeError as refused:
        return str(refused)
    return f"ADMITTED (no refusal at all): {admitted}"


# --- the clause controls: each clause is the sole cause of its answer ---------


def test_the_base_moved_shape_is_inadmissible_by_the_other_two_clauses(tmp_path: Path):
    """The changed-path clause is the ONLY clause that can answer the base-moved shape.

    Measured, not asserted: the whole-tree clause says no, the ancestry clause says no, and
    the path-by-path clause says yes. Without that measurement this case would prove nothing
    about *which* clause admitted it.
    """
    world = _world(tmp_path, variant="base_moved")
    repo, verified, landing = world["repo"], world["verified"], world["landing"]
    base = _git(repo, "merge-base", verified, landing)
    paths = _git(repo, "diff", "--name-only", base, verified).split()

    print(f"  measured: verified {verified[:12]}, landing {landing[:12]}, merge base {base[:12]}")
    print(f"  the tip changed {len(paths)} path(s): {' '.join(paths)}")
    print(f"  ancestry clause  commit_is_contained(verified, landing)={commit_is_contained(repo, verified, landing)}")
    print(f"  tree clause      trees_are_identical(verified, landing)={trees_are_identical(repo, verified, landing)}")
    print(f"  change clause    change_relation(verified, landing)={change_relation(repo, verified, landing)!r}")

    assert commit_is_contained(repo, verified, landing) is False, (
        "the fixture is not a base-moved squash: the verified commit IS in the landing's history"
    )
    assert trees_are_identical(repo, verified, landing) is False, (
        "the fixture is admitable by whole-tree equality, so it does not reproduce #1298"
    )
    assert len(paths) >= 3, f"the verified change must add, modify and delete, got {paths}"
    assert change_relation(repo, verified, landing) == "same", (
        "the change clause must be the clause that admits this shape"
    )


def test_the_ancestry_clause_answers_the_merge_shape_on_its_own(tmp_path: Path):
    """A landing that DESCENDS from the verified commit -- the merge-commit form.

    Here the merge base of the pair is the verified commit itself, so there is no change to
    compare path by path and the whole trees differ: the ancestry clause is the only one
    that can answer, and it must.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
    _git(repo, "config", "user.name", "lifecycle-test")
    _git(repo, "config", "user.email", "lifecycle-test@agents.invalid")
    (repo / "README.md").write_text("the repository\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "verified.txt").write_text("the verified work\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the verified head")
    verified = _git(repo, "rev-parse", "HEAD")
    (repo / "later.txt").write_text("later work on the default branch\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "later work on the default branch")
    landing = _git(repo, "rev-parse", "HEAD")

    pair_base = _git(repo, "merge-base", verified, landing)
    print(f"  measured: merge-base(verified, landing)={pair_base[:12]} == verified {verified[:12]}"
          if pair_base == verified else f"  measured: merge-base={pair_base[:12]}")
    print(f"  tree clause      trees_are_identical(verified, landing)={trees_are_identical(repo, verified, landing)}")
    print(f"  change clause    change_relation(verified, landing)={change_relation(repo, verified, landing)!r}")

    assert pair_base == verified, "the fixture is not the merge-commit form"
    assert trees_are_identical(repo, verified, landing) is False, (
        "the whole-tree clause must not be able to answer this shape either"
    )
    assert commit_is_contained(repo, verified, landing) is True
    assert change_relation(repo, verified, landing) == "same"


def test_the_whole_tree_clause_answers_when_no_merge_base_can_be_resolved(tmp_path: Path):
    """Two unrelated roots carrying identical trees: no ancestry is readable at all.

    ``git merge-base`` exits 1, so there is no change to compare path by path; the whole-tree
    question is the only one left, needs no history, and must still be answered -- otherwise
    a genuinely identical landing would be refused for a reason that is not about content.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
    _git(repo, "config", "user.name", "lifecycle-test")
    _git(repo, "config", "user.email", "lifecycle-test@agents.invalid")
    (repo / "f.txt").write_text("identical content\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "root one")
    first = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "--orphan", "second")
    _git(repo, "rm", "-q", "-r", "--cached", ".")
    (repo / "f.txt").write_text("identical content\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "root two")
    second = _git(repo, "rev-parse", "HEAD")

    rc = _git_rc(repo, "merge-base", first, second)
    print(f"  measured: git merge-base {first[:12]} {second[:12]} exited {rc}")
    print(f"  tree clause      trees_are_identical={trees_are_identical(repo, first, second)}")
    print(f"  change clause    change_relation={change_relation(repo, first, second)!r}")

    assert rc != 0, "the fixture has a merge base, so it does not exercise the fallback"
    assert commit_is_contained(repo, first, second) is False
    assert trees_are_identical(repo, first, second) is True
    assert change_relation(repo, first, second) == "same"


def test_the_change_clause_refuses_a_landing_that_does_not_carry_the_change(tmp_path: Path):
    """A landing built from other content: ``"different"``, never ``"same"``."""
    world = _world(tmp_path, variant="drift")
    repo, verified, landing = world["repo"], world["verified"], world["landing"]

    print(f"  measured: trees_are_identical={trees_are_identical(repo, verified, landing)}; "
          f"change_relation={change_relation(repo, verified, landing)!r}")

    assert trees_are_identical(repo, verified, landing) is False
    assert commit_is_contained(repo, verified, landing) is False
    assert change_relation(repo, verified, landing) == "different"
    assert landing_carries_change(repo, verified, landing) is False


def test_the_change_clause_fails_closed_on_a_commit_it_cannot_read(tmp_path: Path):
    """An unreadable commit is ``"unknown"`` -- never a pass.

    "The change is not there" and "the change could not be compared" are different facts, and
    only the first licenses a refusal that names the content as the cause. Both refuse.
    """
    world = _world(tmp_path, variant="base_moved")
    repo = world["repo"]

    assert change_relation(repo, "c" * 40, world["landing"]) == "unknown"
    assert change_relation(repo, world["verified"], "c" * 40) == "unknown"
    assert change_relation(repo, "", world["landing"]) == "unknown"
    assert change_relation(repo, world["verified"], "") == "unknown"
    assert landing_carries_change(repo, "c" * 40, world["landing"]) is False


# --- the port: the base-moved item is ADMITTED -------------------------------


def test_a_base_moved_squash_merge_is_admitted_by_the_real_predicate(tmp_path: Path, monkeypatch):
    """The whole point (#1298): the record is produced for the case the arm exists for.

    The gate is driven through the **shipping** ``GhOps.record_verification`` against a real
    squash merge whose base moved; the proof that the gate ran in the lane -- not in some
    obsolete tree -- is taken from inside the run, because the record alone would be an
    assertion.
    """
    log = _stub_make(tmp_path, monkeypatch)
    world = _world(tmp_path, variant="base_moved")
    repo, verified, landing, lane_head = world["repo"], world["verified"], world["landing"], world["lane_head"]

    ops = GhOps(root=repo, record={"items": [_item(verified, landing)]})
    detail = ops.record_verification(ISSUE, verified, landing)
    print(f"  ACTUAL: {detail}")

    assert "verify green" in detail, detail
    assert "the landing carries the change the verified commit introduced" in detail, detail
    journal = json.loads(journal_path(ISSUE, repo).read_text(encoding="utf-8"))
    print(f"  ACTUAL record: {json.dumps(journal['verify'], sort_keys=True)}")
    assert journal["verify"] == {
        "ok": True,
        "commit": verified,  # the verified commit the evidence is against -- unchanged
        "source": "lane",
        "landing": landing,  # the commit the squash landed as
        "measured": lane_head,  # the tree the gate actually ran in
        "via": "contains",
    }
    where, measured = _gate_ran_in(log)
    assert where == str(world["lane"]), f"the gate ran in {where}, not in the lane"
    assert measured == lane_head
    assert measured != verified, "the fixture gated the obsolete branch tip, not the lane"


# --- the port: the arms still REFUSE what they exist for ---------------------


def test_an_unmerged_pull_request_is_still_refused_by_name(tmp_path: Path, monkeypatch):
    """No landing, so there is nothing to stand for: equality still governs (#1098)."""
    log = _stub_make(tmp_path, monkeypatch)
    world = _world(tmp_path, variant="unmerged")

    actual = _refusal(world["repo"], world)
    print("  EXPECTED: a refusal naming that no landing is recorded for it")
    print(f"  ACTUAL:   {actual}")

    assert "is not the verified commit" in actual, actual
    assert "no landing is recorded for it" in actual, actual
    assert not log.exists(), "the gate ran for a lane that must not have been admitted"
    assert not journal_path(ISSUE, world["repo"]).exists(), "an attestation was written for an unmerged item"


def test_a_lane_that_does_not_contain_the_landing_is_still_refused_by_name(tmp_path: Path, monkeypatch):
    """The containment half is untouched: a lane carrying none of this item's work."""
    log = _stub_make(tmp_path, monkeypatch)
    world = _world(tmp_path, variant="pre_merge")

    actual = _refusal(world["repo"], world)
    print("  EXPECTED: a refusal naming that the lane does not contain the landing")
    print(f"  ACTUAL:   {actual}")

    assert f"lane head {world['lane_head'][:12]} is not the verified commit {world['verified'][:12]}" in actual, actual
    assert f"and does not contain the landing {world['landing'][:12]}" in actual, actual
    assert not log.exists(), "the gate ran for a lane that must not have been admitted"
    assert not journal_path(ISSUE, world["repo"]).exists(), "an attestation was written for a lane carrying no work"


def test_a_landing_that_does_not_carry_the_change_is_still_refused_by_name(tmp_path: Path, monkeypatch):
    """Containing *a* landing is not the claim; containing *the verified work* is.

    The negative control for the changed clause: a lane that DOES contain the landing, whose
    landing does not carry the verified change, is refused -- and the refusal names the true
    cause rather than the containment that did hold.
    """
    log = _stub_make(tmp_path, monkeypatch)
    world = _world(tmp_path, variant="drift")

    actual = _refusal(world["repo"], world)
    print(f"  EXPECTED: a refusal saying the lane DOES contain the landing {world['landing'][:12]}, "
          "and that the verified commit names a tree that never landed")
    print(f"  ACTUAL:   {actual}")

    assert f"it DOES contain the landing {world['landing'][:12]}" in actual, actual
    assert "names a tree that never landed" in actual, actual
    assert "does not contain" not in actual, (
        "the distinct cause may not be reported as the containment cause it is not"
    )
    assert not log.exists(), "the gate ran for a landing that does not carry the verified change"
    assert not journal_path(ISSUE, world["repo"]).exists(), "an attestation was written for un-landed content"


# --- the fixture from the issue, measured on the real tree -------------------


def test_the_real_tree_fixture_from_the_issue_is_admitted_by_the_change_clause():
    """The issue's own reproduction, measured on this repository's real history.

    Skipped -- by name, never silently passed -- when the commits are not in this object
    store (a shallow or fixture checkout). ``870eb26`` is on the default branch and
    ``17dc00a`` is the branch tip it replaced, so the shape is a genuine squash merge and
    not a fact typed into a fixture.
    """
    for ref in (REAL_VERIFIED, REAL_LANDING):
        if _git_rc(REPO_ROOT, "cat-file", "-e", f"{ref}^{{commit}}") != 0:
            pytest.skip(f"{ref} is not in this object store, so the real-tree fixture cannot be measured")

    verified_diff = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", f"{REAL_VERIFIED}^..{REAL_VERIFIED}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    landing_diff = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff-tree", "--root", "-p", "--no-color", "--no-commit-id", REAL_LANDING],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    verified_patch = subprocess.run(
        ["git", "patch-id", "--stable"], input=verified_diff, capture_output=True, text=True, check=True
    ).stdout.split()[0]
    landing_patch = subprocess.run(
        ["git", "patch-id", "--stable"], input=landing_diff, capture_output=True, text=True, check=True
    ).stdout.split()[0]
    tip_paths = _git(REPO_ROOT, "diff", "--name-only", f"{REAL_VERIFIED}^", REAL_VERIFIED).split()

    print(f"  measured: verified {REAL_VERIFIED} vs landing {REAL_LANDING}; the tip changed {len(tip_paths)} path(s)")
    print(f"  git merge-base --is-ancestor {REAL_VERIFIED} {REAL_LANDING} -> rc "
          f"{_git_rc(REPO_ROOT, 'merge-base', '--is-ancestor', REAL_VERIFIED, REAL_LANDING)}")
    print(f"  trees_are_identical={trees_are_identical(REPO_ROOT, REAL_VERIFIED, REAL_LANDING)}")
    print(f"  change_relation={change_relation(REPO_ROOT, REAL_VERIFIED, REAL_LANDING)!r}")
    print(f"  patch ids: verified={verified_patch} landing={landing_patch}")

    assert _git_rc(REPO_ROOT, "merge-base", "--is-ancestor", REAL_VERIFIED, REAL_LANDING) != 0
    assert trees_are_identical(REPO_ROOT, REAL_VERIFIED, REAL_LANDING) is False, (
        "the real-tree fixture is admitable by whole-tree equality, so it does not reproduce #1298"
    )
    assert verified_patch == landing_patch, "the change the tip carried is not the change that landed"
    assert change_relation(REPO_ROOT, REAL_VERIFIED, REAL_LANDING) == "same"
    assert landing_carries_change(REPO_ROOT, REAL_VERIFIED, REAL_LANDING) is True

    # Both halves of the admissibility arm, on the real repository, with the real default
    # branch standing in for the lane head a cut-from-master lane would hold -- the shape
    # that needs a lane at all. A namespace that does not carry origin/master is skipped by
    # name rather than quietly passing.
    if _git_rc(REPO_ROOT, "cat-file", "-e", "origin/master^{commit}") != 0:
        pytest.skip("origin/master is not in this object store, so the lane half cannot be measured")
    head = _git(REPO_ROOT, "rev-parse", "origin/master")
    if not commit_is_contained(REPO_ROOT, REAL_LANDING, head):
        pytest.skip(f"origin/master ({head[:12]}) does not contain the landing, so it is not the lane shape")
    print(f"  measured: the lane half holds for head={head[:12]} -- "
          f"commit_is_contained(landing, head)={commit_is_contained(REPO_ROOT, REAL_LANDING, head)}")
    assert commit_is_contained(REPO_ROOT, REAL_LANDING, head) is True
    assert landing_carries_change(REPO_ROOT, REAL_VERIFIED, REAL_LANDING) is True, (
        "the arm would still refuse the issue's own fixture, which is the defect #1298 records"
    )
