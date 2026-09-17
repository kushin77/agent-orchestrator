"""A frozen lane head that is red for a reason the landed tree does not contain (#1003).

The defect, measured on #955: ``record-verification`` runs the gate **in the lane
worktree, at the lane's branch head**, and the invariant demands an attestation naming
that head. A commit is immutable, so when the branch head predates a commit its own
squash was composed on, its tree is red **permanently** — seven close-out attempts over
~2 hours refused identically with ``REMAINS VERIFY_EVIDENCE_MISSING``, for work whose
*merged* tree was green: the head ``4ed3fc0`` was committed at 16:18:30 without the
declaration ``72dca6c`` had landed at 16:16:48, and the squash ``494ff91`` was composed
at 16:19:38 with ``72dca6c`` as its parent.

So the question the invariant really asks — *did the change that landed reach a green
gate?* — is answered for an already-merged item at the **merge commit**, and only there:
for a squash no other commit in the object store holds the landed tree.

Both halves are pinned here, and the second is the one that matters:

* (a) an item whose gate WAS green — at the tree that landed — reaches hygiene instead
  of owing ``VERIFY_EVIDENCE_MISSING``, and the record says which tree it measured;
* (b) an item whose evidence is genuinely absent **still owes it**, by name: a change
  that is red at the landed tree too, a park that measured nothing, and an attestation
  that names a tree the item's own record does not carry.

The harness is ``test_reclaimed_lane_evidence.py``'s: a **real** repository, a **real**
``git worktree``, the **carrying** port (``GhOps.record_verification``), and exactly one
fiction — ``make`` on ``PATH``, because a test may not run the composite gate. The stub's
verdict depends on the *tree*, not on the caller, which is the whole asymmetry under test:
it fails exactly when the tree it runs in lacks ``declaration.txt``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from governance.lifecycle import gate
from governance.lifecycle.audit import audit_item
from governance.lifecycle.cli import SCRATCH_ENV, GhOps, journal_path, lane_view, read_journal
from governance.lifecycle.cli import _lane_records
from governance.lifecycle.closeout import OK, PERFORMED, REFUSED, closeout

ISSUE = 1003
SESSION = "s-1003"

#: The stub's rule, and the #955 mechanism in one line: the check fails when the tree it
#: runs in does not declare the surface. The frozen branch head cannot have it (it was
#: committed before the declaration landed); the squash's own tree has it, because the
#: declaration landed on master *before* the squash was composed.
_STUB = """#!/usr/bin/env bash
printf '%s %s\\n' "$PWD" "$(git -C "$PWD" rev-parse HEAD)" >> "$STUB_GATE_LOG"
if [ -f "$PWD/broken.txt" ] || [ ! -f "$PWD/declaration.txt" ]; then
  printf '== e2e ==\\n  FAIL  go-live-plan.yaml does not declare the surface\\n' >&2
  printf 'verify: FAIL (1 of 120 checks failed)\\n' >&2
  printf 'make: *** [Makefile:146: verify] Error 1\\n' >&2
  exit 2
fi
printf 'verify: PASS (120 of 120 checks)\\n'
exit 0
"""

_PARKED = (
    "gate-lock: PARKED — the box-wide gate cap (4) is reached; holders: pid 4242\n"
    "verify: PARKED (rc 11, not a pass and not a failure) — the box-wide gate cap is "
    "reached; nothing was run and no attestation was touched\n"
    "make: *** [Makefile:146: verify] Error 11\n"
)


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


def _commit(cwd: Path, message: str) -> str:
    _git(cwd, "add", "-A")
    _git(cwd, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


def _world(root: Path, *, broken_lane: bool = False) -> dict:
    """A real repo whose frozen lane head is red only because it predates its own squash.

    Returns the commits that matter: ``head`` (the frozen branch head, ``4ed3fc0``),
    ``sibling`` (the commit that landed before the squash, ``72dca6c``) and ``merged``
    (the squash, ``494ff91``). ``broken_lane`` adds a defect the lane's own change
    carries, so it is red in the landed tree too — the negative control that must NOT be
    cleared by measuring there.
    """
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    (repo / "README.md").write_text("repo\n", encoding="utf-8")
    _commit(repo, "base")
    base = _git(repo, "rev-parse", "HEAD")

    lane = root / "lane"
    _git(repo, "worktree", "add", "-b", f"issue-{ISSUE}", str(lane), "HEAD")
    (lane / "verified.txt").write_text("the verified work\n", encoding="utf-8")
    if broken_lane:
        (lane / "broken.txt").write_text("a defect the lane's own diff carries\n", encoding="utf-8")
    head = _commit(lane, "the frozen verified head")

    # The sibling lands on master AFTER the branch was cut and BEFORE the squash is
    # composed: the lane never sees it, the squash's tree always does.
    (repo / "declaration.txt").write_text("surfaces.fleet_projection\n", encoding="utf-8")
    sibling = _commit(repo, "the declaration lands on master")

    # The squash: the lane's diff applied on top of the sibling, exactly as a squash merge
    # composes it (a new commit whose parent is the branch's tip at merge time).
    _git(repo, "-c", "commit.gpgsign=false", "cherry-pick", head)
    merged = _git(repo, "rev-parse", "HEAD")

    # The sibling really is the squash's parent, which is what makes the landed tree carry
    # what the frozen head cannot.
    assert _git(repo, "rev-parse", f"{merged}^") == sibling
    assert base != sibling

    lanes = repo / ".fleet" / "lanes"
    lanes.mkdir(parents=True)
    (lanes / f"{SESSION}.json").write_text(
        json.dumps({"session_id": SESSION, "issue": ISSUE, "worktree": str(lane)}) + "\n",
        encoding="utf-8",
    )
    return {"repo": repo, "lane": lane, "base": base, "head": head, "sibling": sibling, "merged": merged}


def _stub_make(root: Path, monkeypatch, *, outcome: str = "tree") -> Path:
    """A ``make`` on ``PATH`` whose verdict is a function of the tree it runs in."""
    binary_dir = root / "bin"
    binary_dir.mkdir(exist_ok=True)
    log = root / "gate-ran"
    stub = binary_dir / "make"
    if outcome == "tree":
        body = _STUB
    elif outcome == "parked":
        body = (
            "#!/usr/bin/env bash\n"
            'printf \'%s %s\\n\' "$PWD" "$(git -C "$PWD" rev-parse HEAD)" >> "$STUB_GATE_LOG"\n'
            'printf "%s" "$STUB_GATE_PARKED" >&2\n'
            'printf "make: *** [Makefile:146: verify] Error 11\\n" >&2\n'
            "exit 2\n"
        )
    else:
        raise AssertionError(f"unknown stub outcome {outcome!r}")
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_GATE_LOG", str(log))
    monkeypatch.setenv("STUB_GATE_PARKED", _PARKED)
    monkeypatch.setenv("AO_LIFECYCLE_GATE_RETRIES", "0")
    monkeypatch.setenv("AO_LIFECYCLE_GATE_RETRY_WAIT", "0")
    scratch = root / "scratch"
    scratch.mkdir(exist_ok=True)
    monkeypatch.setenv(SCRATCH_ENV, str(scratch))
    return log


def gate_runs(log: Path) -> list[tuple[Path, str]]:
    """``(tree, commit)`` for every gate run, read from inside the runs themselves.

    From inside, because the measurement tree is removed on the way out — that is a
    property under test, so there is nothing left to interrogate afterwards.
    """
    if not log.exists():
        return []
    return [
        (Path(line.split()[0]), line.split()[1])
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def item_for(world: dict, **overrides) -> dict:
    """The lifecycle item: every GitHub-derived fact already terminal, the lane live.

    A merged PR whose verified head is the frozen branch head and whose merge commit is
    the squash — the state #955 sat in for two hours.
    """
    item = {
        "issue": ISSUE,
        "title": "fixture",
        "state": "closed",
        "milestone": "M26 - Session Fleet Operating Model",
        "labels": ["class:elite", "pillar:autonomous-ops"],
        "pr": {
            "number": 964,
            "state": "merged",
            "branch": f"issue-{ISSUE}",
            "head_commit": world["head"],
            "merge_commit": world["merged"],
        },
        "branch_deleted": True,
        "claim": {"agent": None, "live": False},
        "directive": {"id": f"d-{ISSUE}", "state": "done"},
        "lane": {"session_id": SESSION, "present": True, "worktree": str(world["lane"]), "worktree_exists": True},
        "verify": {},
        "closing_evidence": True,
    }
    item.update(overrides)
    return item


class OfflineOps(GhOps):
    """The real port, with only the steps that need ``gh`` replaced.

    ``record_verification`` is **not** overridden: it is the half under test. ``refresh``
    re-reads the world the way the live collector does — the lane from the shipping
    ``_lane_records``, the verification from the journal — so the final audit sees facts
    read off the repository rather than facts this harness typed.
    """

    def __init__(self, world: dict, item: dict) -> None:
        super().__init__(root=world["repo"], record={"items": [item]})
        self.world = world
        self.item = item
        self.calls: list[str] = []

    def merge_pull_request(self, number: int) -> str:
        self.calls.append("merge-pull-request")
        return self.world["merged"]

    def delete_branch(self, branch: str) -> str:
        self.calls.append("delete-branch")
        return f"deleted {branch}"

    def consume_directive(self, directive_id: str) -> str:
        self.calls.append("consume-directive")
        return f"consumed {directive_id}"

    def release_claim(self, issue: int, agent: str) -> str:
        self.calls.append("release-claim")
        return "released"

    def record_closing_evidence(self, issue: int, evidence: str) -> str:
        self.calls.append("record-closing-evidence")
        return "evidence journalled"

    def close_issue(self, issue: int, evidence: str) -> str:
        self.calls.append("close-issue")
        return "closed"

    def reclaim_lane(self, session_id: str) -> str:
        """The real effect: the tree goes, and so does the record."""
        self.calls.append("reclaim-lane")
        subprocess.run(
            ["git", "-C", str(self.world["repo"]), "worktree", "remove", "--force", str(self.world["lane"])],
            check=True,
            capture_output=True,
        )
        (self.world["repo"] / ".fleet" / "lanes" / f"{session_id}.json").unlink()
        return f"reclaimed {session_id}"

    def refresh(self, refreshed: dict) -> dict:
        fresh = dict(refreshed)
        fresh["lane"] = lane_view(_lane_records(self.world["repo"]).get(ISSUE))
        fresh["verify"] = read_journal(ISSUE, self.world["repo"]).get("verify") or {}
        return fresh


# --- (a) the half the fix must clear ----------------------------------------


def test_a_frozen_head_red_only_because_it_predates_its_squash_reaches_hygiene(tmp_path, monkeypatch):
    """The item #955 was: the invariant is SATISFIED, from the tree that landed.

    Measured on the real thing, seven attempts over two hours could not converge,
    because the tree the invariant was measured at is immutable. Here the same shape is
    reproduced with real git: the frozen head lacks ``declaration.txt``, the squash's
    parent brought it, and the gate's verdict is a function of the tree.
    """
    world = _world(tmp_path)
    log = _stub_make(tmp_path, monkeypatch)
    item = item_for(world)
    ops = OfflineOps(world, item)

    result = closeout(item, ops)

    assert result.verdict == OK, f"verdict={result.verdict} remaining={[f.code for f in result.remaining]}"
    assert "VERIFY_EVIDENCE_MISSING" not in {finding.code for finding in result.remaining}
    assert [finding.code for finding in result.remaining] == []

    recorded = read_journal(ISSUE, world["repo"])["verify"]
    assert recorded == {
        "ok": True,
        "commit": world["head"],
        "source": "merged-tree",
        "measured": world["merged"],
    }, recorded
    # The invariant's subject is unchanged — the evidence still names the verified head —
    # and the tree it was measured in is named beside it.
    assert recorded["commit"] == item["pr"]["head_commit"]
    assert recorded["measured"] == item["pr"]["merge_commit"]

    # The lane is reclaimed rather than refused: the item no longer owes the record.
    verification = next(step for step in result.steps if step.action == "record-verification")
    assert verification.outcome == PERFORMED
    assert "measured in the tree that landed" in verification.detail
    assert world["merged"][:12] in verification.detail
    assert not world["lane"].exists()
    assert "reclaim-lane" in ops.calls


def test_the_gate_really_ran_at_both_trees_and_the_scratch_tree_was_thrown_away(tmp_path, monkeypatch):
    """The record is evidence because a gate ran at the named commit — not because we said so."""
    world = _world(tmp_path)
    log = _stub_make(tmp_path, monkeypatch)

    GhOps(root=world["repo"], record={"items": [item_for(world)]}).record_verification(ISSUE, world["head"])

    runs = gate_runs(log)
    assert [commit for _tree, commit in runs] == [world["head"], world["merged"]], runs
    where = runs[0][0]
    assert where == world["lane"], "the frozen head is measured in its own lane first — the strongest evidence there is"
    landed_tree = runs[1][0]
    assert landed_tree != world["repo"] and landed_tree != world["lane"]
    assert str(landed_tree).startswith(str(tmp_path / "scratch"))
    assert not landed_tree.exists(), "the measurement tree must not be able to become a lane"


def test_the_record_is_read_without_a_github_round_trip_when_it_already_carries_the_merge(
    tmp_path, monkeypatch
):
    """The merge commit is read from the item's own record when the record has it.

    A live ``gh`` read is the fallback for close-out's *designed* order (it collects the
    item before it merges), and it is only reached when the record cannot answer. The
    proof is the absence of ``gh`` from ``PATH``: this run has no ``gh`` at all.
    """
    world = _world(tmp_path)
    _stub_make(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}/usr/bin:/bin")

    detail = GhOps(root=world["repo"], record={"items": [item_for(world)]}).record_verification(
        ISSUE, world["head"]
    )

    assert "measured in the tree that landed" in detail
    assert read_journal(ISSUE, world["repo"])["verify"]["measured"] == world["merged"]


def test_a_merge_composed_in_this_run_is_found_live(tmp_path, monkeypatch):
    """The designed order: the item was collected before step 1 merged it.

    Then the record still says the pull request is open, and only a live read can say
    whether the tree that landed is green — which is why :meth:`_merged_commit` falls
    back to ``gh`` at all.
    """
    world = _world(tmp_path)
    _stub_make(tmp_path, monkeypatch)
    gh = tmp_path / "bin" / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'{"state":"MERGED","mergeCommit":{"oid":"%s"}}\\n\' "$STUB_MERGE_COMMIT"\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)
    monkeypatch.setenv("STUB_MERGE_COMMIT", world["merged"])
    # Exactly the record close-out holds when it has just merged: open, no merge commit.
    stale = item_for(world, pr={**item_for(world)["pr"], "state": "open", "merge_commit": ""})

    detail = GhOps(root=world["repo"], record={"items": [stale]}).record_verification(ISSUE, world["head"])

    assert "measured in the tree that landed" in detail
    assert read_journal(ISSUE, world["repo"])["verify"]["measured"] == world["merged"]


# --- (b) the half that must NOT be cleared ----------------------------------


def test_a_change_that_is_red_at_the_landed_tree_too_still_owes_the_evidence(tmp_path, monkeypatch):
    """The negative control that matters: measuring the landed tree is not a blanket pass.

    The merge commit contains the pull request's own diff, so a defect the lane carries is
    red there as well. The refusal then names **both** trees, no journal is written, and
    the invariant stands — so the fix cannot launder a red lane.
    """
    world = _world(tmp_path, broken_lane=True)
    log = _stub_make(tmp_path, monkeypatch)
    item = item_for(world)
    ops = OfflineOps(world, item)

    # The refusal itself, before close-out truncates it into a step detail.
    try:
        GhOps(root=world["repo"], record={"items": [item]}).record_verification(ISSUE, world["head"])
    except RuntimeError as exc:
        refusal = str(exc)
    else:  # pragma: no cover - a pass here would be the defect
        raise AssertionError("a change that is red at the landed tree too was recorded as green")
    assert "reported a failure" in refusal
    assert world["head"][:12] in refusal, refusal
    assert world["merged"][:12] in refusal, refusal
    assert not journal_path(ISSUE, world["repo"]).exists()

    result = closeout(item, ops)

    assert result.verdict != OK
    assert "VERIFY_EVIDENCE_MISSING" in {finding.code for finding in result.remaining}
    assert not journal_path(ISSUE, world["repo"]).exists(), "a red change must not be recorded as green"
    assert world["lane"].exists(), "the lane is kept: it is the only tree the evidence can come from"
    verification = next(step for step in result.steps if step.action == "record-verification")
    # Both trees are named in the step's own detail, which ``closeout`` caps at 300
    # characters — so a refusal that named its evidence only in its tail would be cut off
    # exactly where the new fact is. The cap is the reason the message is composed from
    # each run's sentence rather than from their whole transcripts.
    assert verification.detail == f"RuntimeError: {refusal}", verification.detail
    assert world["merged"][:12] in verification.detail
    assert next(step for step in result.steps if step.action == "reclaim-lane").outcome == REFUSED
    # Both trees really were measured — twice over, once for the direct call above and once
    # inside close-out, each measuring the frozen head first and the landed tree second. The
    # negative control is measured, not assumed.
    assert [commit for _tree, commit in gate_runs(log)] == [world["head"], world["merged"]] * 2


def test_the_landed_tree_is_not_measured_when_the_head_and_the_merge_are_one_tree(tmp_path, monkeypatch):
    """The common case costs nothing: no sibling landed, so there is no second tree.

    A squash whose base did not move has a tree equal to the head's — measuring it again
    would ask the same question twice and pay a full gate run for the answer.
    """
    world = _world(tmp_path)
    world["merged"] = world["head"]  # one tree, presented as both facts
    log = _stub_make(tmp_path, monkeypatch)
    item = item_for(world)
    item["pr"]["merge_commit"] = world["head"]

    result = closeout(item, OfflineOps(world, item))

    assert "VERIFY_EVIDENCE_MISSING" in {finding.code for finding in result.remaining}
    assert len(gate_runs(log)) == 1, gate_runs(log)


def test_a_park_measures_nothing_so_it_is_never_answered_by_measuring_elsewhere(tmp_path, monkeypatch):
    """A capacity condition is not a red tree (#840 must not regress).

    The gate ran nothing, so whether the frozen head is green is *unmeasured* — and
    measuring a different tree in its place would turn "we could not measure" into a
    result nobody produced. The bounded retry is the answer to a park, not a substitution.
    """
    world = _world(tmp_path)
    log = _stub_make(tmp_path, monkeypatch, outcome="parked")
    item = item_for(world)
    ops = OfflineOps(world, item)

    result = closeout(item, ops)

    assert result.verdict == "cannot-assess"
    assert not journal_path(ISSUE, world["repo"]).exists()
    verification = next(step for step in result.steps if step.action == "record-verification")
    assert verification.outcome == "parked"
    assert "PARKED" in verification.detail
    runs = gate_runs(log)
    assert [tree for tree, _commit in runs] == [world["lane"]], "the landed tree must not be visited for a park"
    assert world["lane"].exists()


def test_an_attestation_that_names_a_tree_the_item_does_not_carry_does_not_count():
    """The audit clause is a strengthening, and it can fail.

    A record that says it was measured at a commit that is neither the verified head nor
    the merged tree is refused where it used to be believed. Without this the new clause
    would be a formality: the port only ever writes those two, so only a provocation can
    show the rule is load-bearing.
    """
    item = {
        "issue": 1,
        "state": "closed",
        "labels": ["class:elite"],
        "milestone": "M26",
        "pr": {"number": 1, "state": "merged", "branch": "issue-1", "head_commit": "a" * 40, "merge_commit": "b" * 40},
        "branch_deleted": True,
        "claim": {"agent": None, "live": False},
        "directive": {},
        "lane": {},
        "closing_evidence": True,
    }
    clean = {**item, "verify": {"ok": True, "commit": "a" * 40}}
    assert audit_item(clean) == []
    landed = {**item, "verify": {"ok": True, "commit": "a" * 40, "measured": "b" * 40, "source": "merged-tree"}}
    assert audit_item(landed) == [], "the merged tree is a legitimate venue"

    foreign = {**item, "verify": {"ok": True, "commit": "a" * 40, "measured": "c" * 40, "source": "somewhere-else"}}
    findings = audit_item(foreign)
    assert [finding.code for finding in findings] == ["VERIFY_EVIDENCE_MISSING"]
    assert "was measured at" in findings[0].detail
    assert "neither the verified head commit" in findings[0].detail


def test_the_evidence_subject_is_still_the_verified_head_not_the_merge_commit():
    """The invariant is unchanged where it matters: naming the merge commit is not evidence.

    A squash merge composes a new commit, so demanding the merge commit would fail every
    correctly-merged item; and accepting it *as the subject* would let an attestation
    stand for a tree nobody verified. Only the venue may be the merged tree, never the
    subject.
    """
    item = {
        "issue": 1,
        "state": "closed",
        "labels": ["class:elite"],
        "milestone": "M26",
        "pr": {"number": 1, "state": "merged", "branch": "issue-1", "head_commit": "a" * 40, "merge_commit": "b" * 40},
        "branch_deleted": True,
        "claim": {"agent": None, "live": False},
        "directive": {},
        "lane": {},
        "closing_evidence": True,
        "verify": {"ok": True, "commit": "b" * 40},
    }
    findings = audit_item(item)
    assert [finding.code for finding in findings] == ["VERIFY_EVIDENCE_MISSING"]
    assert "not the verified head commit" in findings[0].detail


def test_a_red_frozen_head_still_reports_the_gate_failure_it_measured(tmp_path, monkeypatch):
    """Vacuity control for the fallback: when nothing can be measured, the red is the red.

    No pull request is recorded for this item, so there is no landed tree to try — and the
    step must report the gate failure it actually measured, not silence.
    """
    world = _world(tmp_path)
    _stub_make(tmp_path, monkeypatch)

    try:
        GhOps(root=world["repo"]).record_verification(ISSUE, world["head"])
    except RuntimeError as exc:
        assert not isinstance(exc, gate.CannotAssess)
        assert "reported a failure" in str(exc)
    else:  # pragma: no cover - a pass here would be the defect
        raise AssertionError("a red frozen head with no landed tree was recorded as green")
    assert not journal_path(ISSUE, world["repo"]).exists()
