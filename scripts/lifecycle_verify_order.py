#!/usr/bin/env python3
"""Drive the close-out's verification ordering against a real repository (#786, #1098).

Invoked by ``scripts/check-lifecycle-verify-order.sh``; not a check itself. It exists
as a file rather than an inline heredoc because it is the largest body of logic in the
script family and a heredoc inside the gate is a syntax error waiting for a stray
word — the shared shell's own failure mode.

Two halves of one invariant are provoked here. #786 is the **gone lane**: the record
must be re-measured from the verified commit rather than mourned. #1098 is the **squash
merge**: the commit the merge landed as is not the commit the branch was verified at, so
a lane cut from the default branch can never be *at* the verified head — it may only be
admitted by containing the landing, and only when that landing carries the verified
tree. Both halves fail the same way if they regress, and both are measured from real
git in a real repository, so neither can be satisfied by a fixture that merely looks
like the incident.

Everything that decides the answer here is the shipping code:

* the repository is a real one, and its lane is a real ``git worktree``;
* the reclaim is the **real** ``governance/isolation/cli.py close`` — the command that
  removed the tree in the incident;
* the verification is the **real** ``GhOps.record_verification``, read through the
  real ``governance.lifecycle.gate`` vocabulary;
* the driver is the real ``closeout``.

Exactly two things are not: ``make`` on ``PATH`` (the repo's established seam — a gate
may not run the composite gate twice per case), and the board read, which a gate may not
make at all. Both are named on every line they affect, and neither can manufacture a
green: the stub's outcomes are read by the shipping consumer, and the negative controls
below — cases 2, 3, 5, 8b, 9 and the mutants — prove it.

#1149 adds the third half: **which commit the evidence is against**. For a branch that
received commits after the squash, GitHub's ``head_commit`` is a tree that never landed, so
``closeout`` resolves the subject to the commit the squash landed as and journals the
drifted head beside it. The positive case is the drifted branch closing out green on the
landed tree; the controls are the port refusing that drifted subject in its own words, and
a mutant that removes the resolution and must red the positive case.

---knowledge---
module_id: scripts.lifecycle_verify_order
system: scripts
app: scripts
solution_class: enterprise
patterns: [tri-state-exit, provoked-negative-control, offline-hermetic, lane-isolation]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
invariants: ""
gotchas: ""
related: ["#786", "#1098", "#1149"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(ROOT))

from importlib import import_module  # noqa: E402

from governance.lifecycle import cli as lifecycle_cli  # noqa: E402
from governance.lifecycle.cli import GhOps, journal_path  # noqa: E402
from governance.lifecycle.closeout import (  # noqa: E402
    CANNOT_ASSESS,
    FAILED,
    OK,
    REFUSED,
    closeout,
    describe,
)
from governance.isolation.identity import SessionIdentity  # noqa: E402
from governance.isolation.worktree import write_record  # noqa: E402

# ``from governance.lifecycle import closeout`` yields the *function*, because the
# package re-exports it under that name. The mutants need the module.
closeout_module = import_module("governance.lifecycle.closeout")

#: The invariant under test, and the outcome the fix must make unreachable for work
#: whose gate was green.
VERIFY_INVARIANT = "VERIFY_EVIDENCE_MISSING"

MAKE = WORK / "bin" / "make"
GATE_LOG = WORK / "gate-ran-in"
OUTCOME_FILE = WORK / "gate-outcome"

#: What the stub prints for each outcome, as **make really reports it**: GNU make exits
#: 2 for any failing recipe and merely prints the recipe's own code, so a stub that
#: exited 11 directly would not reproduce the measurement.
_STUB = """#!/usr/bin/env bash
printf '%s %s\\n' "$PWD" "$(git -C "$PWD" rev-parse HEAD 2>/dev/null)" >> "$STUB_GATE_LOG"
case "$(cat "$STUB_GATE_OUTCOME")" in
  passed) printf 'verify: PASS (120 of 120 checks)\\n'; exit 0 ;;
  failed)
    printf 'verify: FAIL (1 of 120 checks failed)\\n' >&2
    printf 'make: *** [Makefile:146: verify] Error 1\\n' >&2
    exit 2 ;;
  parked)
    printf '%s' "$STUB_GATE_PARKED" >&2
    printf 'make: *** [Makefile:146: verify] Error 11\\n' >&2
    exit 2 ;;
esac
printf 'the stub gate was asked for an unknown outcome\\n' >&2
exit 99
"""

_PARKED_STDERR = (
    "gate-lock: PARKED — the box-wide gate cap (4) is reached; holders: pid 4242\n"
    "verify: PARKED (rc 11, not a pass and not a failure) — the box-wide gate cap is "
    "reached; nothing was run and no attestation was touched\n"
)

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "lifecycle-gate",
    "GIT_AUTHOR_EMAIL": "lifecycle-gate@agents.invalid",
    "GIT_COMMITTER_NAME": "lifecycle-gate",
    "GIT_COMMITTER_EMAIL": "lifecycle-gate@agents.invalid",
}

FAILURES: list[str] = []
COUNT = 0


def ok(label: str, condition: bool, detail: str = "") -> bool:
    """One measured assertion, named either way.

    Flushed on both streams, so a FAIL line cannot be reordered above the header of the
    case that provoked it: the two streams are separate buffers, and a gate whose
    evidence is interleaved is a gate whose evidence is harder to trust.
    """
    global COUNT
    COUNT += 1
    sys.stdout.flush()
    if condition:
        print(f"  OK    {label}")
        sys.stdout.flush()
    else:
        print(f"  FAIL  {label}{'  — ' + detail if detail else ''}", file=sys.stderr)
        sys.stderr.flush()
        FAILURES.append(label)
    return bool(condition)


def stage(title: str) -> None:
    sys.stdout.flush()
    print(f"== {title} ==")
    sys.stdout.flush()


def git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, env=_GIT_ENV
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {result.stderr.strip()[-300:]}")
    return result.stdout.strip()


def git_rc(cwd: Path, *args: str) -> int:
    """A git exit code, for the questions whose *answer* is the code (ancestry, tree equality)."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, env=_GIT_ENV
    ).returncode


def write_stub() -> None:
    MAKE.parent.mkdir(parents=True, exist_ok=True)
    MAKE.write_text(_STUB, encoding="utf-8")
    MAKE.chmod(0o755)
    os.environ["PATH"] = f"{MAKE.parent}{os.pathsep}{os.environ['PATH']}"
    os.environ["STUB_GATE_LOG"] = str(GATE_LOG)
    os.environ["STUB_GATE_OUTCOME"] = str(OUTCOME_FILE)
    os.environ["STUB_GATE_PARKED"] = _PARKED_STDERR
    # The bounded retry would wait for a real permit; a gate may not wait.
    os.environ["AO_LIFECYCLE_GATE_RETRIES"] = "0"
    os.environ["AO_LIFECYCLE_SCRATCH"] = str(WORK / "scratch")


def outcome(name: str) -> None:
    OUTCOME_FILE.write_text(name, encoding="utf-8")
    if GATE_LOG.exists():
        GATE_LOG.unlink()


def gate_ran() -> tuple[Path, str] | None:
    """Where the gate last ran, and at which commit — recorded from inside the run.

    From inside, because the re-measurement tree is removed on the way out (that is a
    property under test), so there is nothing left to interrogate afterwards.
    """
    if not GATE_LOG.exists():
        return None
    where, commit = GATE_LOG.read_text(encoding="utf-8").split()[-2:]
    return Path(where), commit


def world(name: str, issue: int, *, reclaim: bool) -> tuple[Path, Path, str]:
    """A real repository with a real lane worktree, optionally already reclaimed.

    The reclaim is performed by the shipping command — ``governance/isolation/cli.py
    close`` — so the state under test is produced by the code that produced it in the
    incident, not by a fixture that merely resembles it.
    """
    base = WORK / name
    repo = base / "repo"
    lane = base / "lane"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "master")
    (repo / "README.md").write_text("the repository\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")

    git(repo, "worktree", "add", "-b", f"issue-{issue}", str(lane), "HEAD")
    (lane / "verified.txt").write_text("the verified work\n", encoding="utf-8")
    git(lane, "add", "verified.txt")
    git(lane, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "the verified head")
    head = git(lane, "rev-parse", "HEAD")

    session = f"s-{issue}"
    # The record is written through the isolation module's own API, not by hand: the
    # reclaim command reads it with ``SessionIdentity.from_json``, and a hand-rolled
    # JSON body would make this harness a fiction about the very artifact under test.
    identity = write_record(
        SessionIdentity(
            session_id=session,
            issue=issue,
            agent_id=f"gate-{issue}",
            lane=f"lifecycle-{issue}",
            branch=f"issue-{issue}",
            worktree=lane,
        ),
        repo,
    )
    if not identity.exists():
        raise RuntimeError(f"the lane record was not written to {identity}")

    if reclaim:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "governance" / "isolation" / "cli.py"),
                "close",
                "--session",
                session,
                "--main",
                str(repo),
            ],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"the real reclaim command refused: {result.stdout.strip()} {result.stderr.strip()}"
            )
        # The record survives the tree, which is exactly the drift #834 measured: a
        # dead record left behind by a reaper. The port must read *that* shape.
        write_record(
            SessionIdentity(
                session_id=session,
                issue=issue,
                agent_id=f"gate-{issue}",
                lane=f"lifecycle-{issue}",
                branch=f"issue-{issue}",
                worktree=lane,
            ),
            repo,
        )
    return repo, lane, head


#: The three real squash-merge shapes #1098 is about, built by real git in ``world_squash``.
SQUASH_VARIANTS = ("descends", "pre_merge", "different_tree")


def world_squash(name: str, issue: int, *, variant: str) -> tuple[Path, Path, str, str, str]:
    """A real **squash-merged** pull request, and a real lane, in one of three shapes.

    This is the shape ``world()`` cannot build and the incident was measured in
    (#714, #977, #978): the verified head commit is **not** an ancestor of anything on
    the default branch, because a squash merge creates a *new* commit. The branch tip
    the merge replaced is therefore unreachable from the default branch, while the
    commit the merge *landed as* is on it and carries the very same tree.

    Returns ``(repo, lane, verified, landing, lane_head)``.

    * ``descends`` — the lane is a worktree of the default branch *after* later work
      landed there. It contains the landing and the landing carries the verified tree:
      the shape that must be **admitted**.
    * ``pre_merge`` — the lane is a worktree of the default branch from **before** the
      landing. It contains nothing of this item, so it must be **refused** — the shape
      of the original complaint (a lane cut from ``origin/master``, rule 15, refused
      because it is not *at* the verified head).
    * ``different_tree`` — the branch got one more commit than the squash carried, so
      the landing is contained but its tree is **not** the verified one. Must be
      **refused**: containing *a* landing must never stand in for containing the
      verified work.
    """
    if variant not in SQUASH_VARIANTS:
        raise AssertionError(f"unknown squash variant {variant!r}")

    base = WORK / name
    repo = base / "repo"
    lane = base / "lane"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "master")
    (repo / "README.md").write_text("the repository\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")
    before = git(repo, "rev-parse", "HEAD")

    # The branch: the pull request's own head, which the squash merge will replace.
    git(repo, "checkout", "-q", "-b", f"issue-{issue}")
    (repo / "verified.txt").write_text("the verified work\n", encoding="utf-8")
    git(repo, "add", "verified.txt")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "the verified head")
    verified = git(repo, "rev-parse", "HEAD")

    # The squash merge: a NEW commit on the default branch carrying the SAME tree.
    git(repo, "checkout", "-q", "master")
    git(repo, "merge", "--squash", "-q", f"issue-{issue}")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", f"the squash landing (#{issue})")
    landing = git(repo, "rev-parse", "HEAD")

    if variant == "different_tree":
        # One commit the squash did not carry, so the landing's tree stops being the
        # verified tree while still being contained by the default branch.
        git(repo, "checkout", "-q", f"issue-{issue}")
        (repo / "unsquashed.txt").write_text("content the squash did not carry\n", encoding="utf-8")
        git(repo, "add", "unsquashed.txt")
        git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "a commit the squash did not carry")
        verified = git(repo, "rev-parse", "HEAD")
        git(repo, "checkout", "-q", "master")

    # Later work on the default branch, so a lane cut from it strictly *contains* the
    # landing rather than equalling it — the containment arm, not the equality arm.
    (repo / "later.txt").write_text("later work on the default branch\n", encoding="utf-8")
    git(repo, "add", "later.txt")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "later work on the default branch")

    # The shape itself is asserted here, so a builder that stopped producing a squash
    # merge would fail loudly instead of quietly passing the cases below.
    if git_rc(repo, "merge-base", "--is-ancestor", verified, landing) == 0:
        raise RuntimeError("the fixture is not a squash merge: the verified head IS an ancestor of the landing")
    if git_rc(repo, "merge-base", "--is-ancestor", verified, "master") == 0:
        raise RuntimeError("the fixture is not a squash merge: the verified head IS an ancestor of master")
    same_tree = git_rc(repo, "diff", "--quiet", verified, landing) == 0
    if same_tree and variant == "different_tree":
        raise RuntimeError("the different_tree fixture's landing carries the verified tree after all")
    if not same_tree and variant != "different_tree":
        raise RuntimeError(f"the {variant} fixture's landing does not carry the verified tree")

    if variant == "pre_merge":
        # A lane of the default branch as it was *before* this item landed: it is not at
        # the verified head, and it contains no landing of this item's.
        git(repo, "worktree", "add", "-q", "--detach", str(lane), before)
    else:
        git(repo, "worktree", "add", "-q", "-b", f"lane-{issue}", str(lane), "master")
    lane_head = git(lane, "rev-parse", "HEAD")
    if variant == "pre_merge" and git_rc(repo, "merge-base", "--is-ancestor", landing, lane_head) == 0:
        raise RuntimeError("the pre_merge lane unexpectedly contains the landing")

    session = f"s-{issue}"
    identity = write_record(
        SessionIdentity(
            session_id=session,
            issue=issue,
            agent_id=f"gate-{issue}",
            lane=f"lifecycle-{issue}",
            branch=f"lane-{issue}",
            worktree=lane,
        ),
        repo,
    )
    if not identity.exists():
        raise RuntimeError(f"the lane record was not written to {identity}")
    return repo, lane, verified, landing, lane_head


def item_for(issue: int, head: str, *, lane_present: bool, session: str = "", merge_commit: str = "") -> dict:
    """The lifecycle item, with every GitHub-derived fact already terminal.

    A merge, a deleted branch, a released claim and a consumed order are *not* the
    subject here, and leaving them terminal means the driver skips them — so a gate
    never reaches the network. The two facts that are the subject, the lane and the
    verification record, are the ones left open.

    ``merge_commit`` is the commit a **squash merge** landed as, and it differs from
    ``head`` in exactly the case the squash half of this invariant exists for (#1098).
    """
    return {
        "issue": issue,
        "title": "fixture",
        "state": "closed",
        "milestone": "M26 - Session Fleet Operating Model",
        "labels": ["class:elite", "pillar:autonomous-ops"],
        "pr": {
            "number": issue,
            "state": "merged",
            "branch": f"issue-{issue}",
            "head_commit": head,
            "merge_commit": merge_commit or head,
        },
        "branch_deleted": True,
        "claim": {"agent": None, "live": False},
        "directive": {"id": f"d-{issue}", "state": "done"},
        "lane": {"session_id": session or f"s-{issue}", "present": lane_present},
        "verify": {},
        "closing_evidence": True,
    }


class OfflineOps(GhOps):
    """The real port, with only its network-facing steps replaced.

    ``record_verification`` and ``reclaim_lane`` are **not** overridden: those are the
    two halves of the defect. The steps below need ``gh``, so they are recorded instead
    of performed, and the gate asserts that none of them was reached while running the
    cases that must not need them. ``refresh`` re-reads the world the same way the real
    one does, except that the world here is the scratch repository: the lane's absence
    is read off the filesystem, never asserted.
    """

    NETWORK_STEPS = (
        "merge-pull-request",
        "delete-branch",
        "consume-directive",
        "release-claim",
        "close-issue",
    )

    def __init__(self, root: Path, item: dict, *, fail: tuple[str, ...] = ()) -> None:
        super().__init__(root=root, record={"items": [item]})
        self.item = item
        self.fail = set(fail)
        self.calls: list[str] = []

    def _net(self, action: str) -> str:
        self.calls.append(action)
        if action in self.fail:
            raise RuntimeError(f"{action} refused by the fixture")
        return f"{action} performed"

    def merge_pull_request(self, number: int) -> str:
        return self._net("merge-pull-request")

    def delete_branch(self, branch: str) -> str:
        return self._net("delete-branch")

    def consume_directive(self, directive_id: str) -> str:
        return self._net("consume-directive")

    def release_claim(self, issue: int, agent: str) -> str:
        return self._net("release-claim")

    def close_issue(self, issue: int, evidence: str) -> str:
        return self._net("close-issue")

    def record_closing_evidence(self, issue: int, evidence: str) -> str:
        self._net("record-closing-evidence")
        # ``closeout`` writes the evidence through the caller's own seam, and the
        # retry path is a *second* close-out on the same item.
        return "evidence journalled"

    def reclaim_lane(self, session_id: str) -> str:
        """The real thing: the shipping isolation command, on a real worktree.

        The module is invoked from the *repository under test's* checkout, because
        ``GhOps`` resolves it relative to ``self.root`` — which in this harness is the
        scratch world, and holds no ``governance/`` package. The command, its
        arguments and the worktree it acts on are the shipping ones; only the path to
        the script's own source is repointed, and it is named here so the substitution
        is visible rather than implied.
        """
        self.calls.append("reclaim-lane")
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "governance" / "isolation" / "cli.py"),
                "close",
                "--session",
                session_id,
                "--main",
                str(self.root),
            ],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                (result.stderr or result.stdout).strip()[-200:] or "the reclaim command refused"
            )
        return result.stdout.strip()

    def refresh(self, refreshed: dict) -> dict:
        """The post-close world, read the way the live collector reads it.

        The lane comes from ``_lane_records`` — the **shipping** reader, over the
        repository on disk — and the verification from the journal, so the final audit
        sees facts read off the world rather than facts this harness typed.
        """
        fresh = dict(refreshed)
        lanes = lifecycle_cli._lane_records(self.root)
        # The audit reads the resolved *view*, never the raw record set (#834):
        # ``_lane_records`` returns one entry per record and the choice between them
        # is ``lane_view``'s, so handing the audit the list would ask it to make a
        # decision it does not own (and, before this line, crash on it).
        fresh["lane"] = lifecycle_cli.lane_view(lanes.get(int(refreshed["issue"])))
        path = journal_path(int(refreshed["issue"]), self.root)
        fresh["verify"] = (
            json.loads(path.read_text(encoding="utf-8")).get("verify") or {}
            if path.exists()
            else {}
        )
        return fresh


def journal_verify(repo: Path, issue: int) -> dict:
    path = journal_path(issue, repo)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("verify") or {}


def seed_journal(repo: Path, issue: int, head: str) -> None:
    """Put a green record on the record, so an item that owns one really owns it.

    ``refresh`` reads the journal, exactly as the live collector does. A case that
    wants an item whose verification is already recorded therefore has to *write* the
    record first — otherwise it would be asserting a fact the repository does not
    hold, which is the substitution the audit exists to refuse.
    """
    path = journal_path(issue, repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"verify": {"ok": True, "commit": head, "source": "lane"}}, indent=2) + "\n",
        encoding="utf-8",
    )


def step(result, action: str):
    return next((entry for entry in result.steps if entry.action == action), None)


# ---------------------------------------------------------------------------
# 1. a lane reclaimed before close-out, gate green: the record is PRODUCED
# ---------------------------------------------------------------------------


def case_reclaimed_lane_green() -> None:
    stage("a lane reclaimed before close-out is re-measured, not mourned")
    repo, lane, head = world("green", 786, reclaim=True)
    outcome("passed")
    print(f"  measured: origin worktree {lane} removed before close-out; verified head {head[:12]}")

    # ``present`` means a session *record* remains (#834), not that the worktree does.
    # This world is exactly the dead-record state: the reaper removed the tree and the
    # record stayed. Reporting it honestly is what makes step 8 reclaim it, which is
    # what lets the item reach a terminal verdict rather than be reported forever —
    # the item's *other* half of the promise is that the record is not merely mourned.
    item = item_for(786, head, lane_present=True)
    ops = OfflineOps(repo, item)
    result = closeout(item, ops)

    print(describe(result))
    recorded = journal_verify(repo, 786)
    ok(
        "the invariant is SATISFIED — no REMAINS VERIFY_EVIDENCE_MISSING for a green tree",
        VERIFY_INVARIANT not in {finding.code for finding in result.remaining},
        f"remaining={[f.code for f in result.remaining]}",
    )
    ok("the item reaches a terminal verdict", result.verdict == OK, f"verdict={result.verdict}")
    ok(
        "the record names the VERIFIED HEAD commit",
        recorded.get("ok") is True and recorded.get("commit") == head,
        f"recorded={recorded}",
    )
    ok(
        "the record's provenance names the reclaimed lane",
        recorded.get("source") == "reclaimed-lane",
        f"source={recorded.get('source')!r}",
    )
    ran = gate_ran()
    ok("the gate really ran, at that commit, in a detached tree", ran is not None, "no gate run recorded")
    if ran is not None:
        where, measured = ran
        ok("the re-measurement tree is not the repository", where != repo, str(where))
        ok("the re-measurement was taken at the verified head", measured == head, measured[:12])
        ok("the re-measurement tree was thrown away", not where.exists(), str(where))
    ok(
        "no network-facing step was reached",
        not [call for call in ops.calls if call in OfflineOps.NETWORK_STEPS],
        f"calls={ops.calls}",
    )


# ---------------------------------------------------------------------------
# 2/3. negative controls: the recovery is not a blanket pass
# ---------------------------------------------------------------------------


def case_reclaimed_lane_red() -> None:
    stage("negative control — a red tree at the reclaimed lane stays red")
    repo, _lane, head = world("red", 787, reclaim=True)
    outcome("failed")

    item = item_for(787, head, lane_present=False)
    result = closeout(item, OfflineOps(repo, item))

    ok(
        "the invariant is still broken and still NAMED",
        VERIFY_INVARIANT in {finding.code for finding in result.remaining},
        f"remaining={[f.code for f in result.remaining]}",
    )
    ok("no record was written for a red tree", journal_verify(repo, 787) == {}, "a journal exists")
    verification = step(result, "record-verification")
    ok(
        "the failure names the gate and the tree",
        verification is not None and "reported a failure" in verification.detail,
        verification.detail if verification else "no step",
    )


def case_reclaimed_lane_unreachable() -> None:
    stage("negative control — a commit the repository does not hold is refused BY NAME")
    repo, _lane, _head = world("unreachable", 788, reclaim=True)
    outcome("passed")

    item = item_for(788, "c" * 40, lane_present=False)
    result = closeout(item, OfflineOps(repo, item))

    verification = step(result, "record-verification")
    detail = verification.detail if verification else ""
    ok(
        "the invariant is still broken",
        VERIFY_INVARIANT in {finding.code for finding in result.remaining},
        f"remaining={[f.code for f in result.remaining]}",
    )
    ok("the refusal names the ordering instead of the old dead end", "close-out BEFORE lane teardown" in detail, detail[:200])
    ok("the old silent message is gone", "the verified tree no longer exists" not in detail, detail[:200])
    ok("no record was written", journal_verify(repo, 788) == {}, "a journal exists")


# ---------------------------------------------------------------------------
# 4. the order guard: the lane is kept while the record is owed
# ---------------------------------------------------------------------------


def case_parked_keeps_the_lane() -> None:
    stage("a parked verification REFUSES the reclaim, and the retry then finishes the job")
    repo, lane, head = world("parked", 789, reclaim=False)
    outcome("parked")

    item = item_for(789, head, lane_present=True)
    ops = OfflineOps(repo, item)
    first = closeout(item, ops)

    print(describe(first))
    reclaim = step(first, "reclaim-lane")
    ok(
        "the reclaim is REFUSED, by name",
        reclaim is not None and reclaim.outcome == REFUSED,
        reclaim.outcome if reclaim else "no step",
    )
    ok("the worktree is still on disk", lane.exists(), f"{lane} is gone")
    ok("the reclaim was never performed", "reclaim-lane" not in ops.calls, f"calls={ops.calls}")
    ok(
        "a park is CANNOT-ASSESS, not a broken invariant (#840 must not regress)",
        first.verdict == CANNOT_ASSESS,
        f"verdict={first.verdict} remaining={[f.code for f in first.remaining]}",
    )
    ok(
        "the withheld step is reported rather than hidden",
        bool(first.withheld) and "reclaim-lane" in " ".join(first.withheld),
        f"withheld={first.withheld}",
    )

    # The retry the eight-step design always assumed — reachable exactly because the
    # first pass kept what the second needs.
    outcome("passed")
    retry_item = dict(item)
    retry = OfflineOps(repo, retry_item)
    second = closeout(retry_item, retry)
    print(describe(second))
    ok("the retry reaches a terminal verdict", second.verdict == OK, f"verdict={second.verdict}")
    ok("the retry reclaimed the lane", "reclaim-lane" in retry.calls, f"calls={retry.calls}")
    ok("the worktree is finally gone", not lane.exists(), f"{lane} survives")
    ok(
        "the retry recorded the verified head",
        journal_verify(repo, 789).get("commit") == head,
        f"recorded={journal_verify(repo, 789)}",
    )


def case_unrelated_failure_still_reclaims() -> None:
    stage("negative control — the guard is the verification, not 'any failure keeps every lane'")
    repo, lane, head = world("unrelated", 790, reclaim=False)
    outcome("passed")

    item = item_for(790, head, lane_present=True)
    item["verify"] = {"ok": True, "commit": head}
    item["branch_deleted"] = False
    seed_journal(repo, 790, head)
    ops = OfflineOps(repo, item, fail=("delete-branch",))
    result = closeout(item, ops)

    ok("an unrelated step really failed", "delete-branch" in ops.calls, f"calls={ops.calls}")
    ok(
        "the lane IS reclaimed when nothing depends on it",
        "reclaim-lane" in ops.calls and not lane.exists(),
        f"calls={ops.calls} lane_exists={lane.exists()}",
    )
    ok(
        "the unrelated finding is still charged",
        "BRANCH_NOT_DELETED" in {finding.code for finding in result.remaining},
        f"remaining={[f.code for f in result.remaining]}",
    )


# ---------------------------------------------------------------------------
# 8. the SQUASH half of the same invariant (#1098)
# ---------------------------------------------------------------------------


def case_squash_lane_from_the_default_branch() -> None:
    """A squash merge creates a new commit; a lane cut from the branch must still count."""
    stage("a lane cut from the default branch after a SQUASH merge is measured, not refused (#1098)")
    repo, lane, verified, landing, lane_head = world_squash("squash-descends", 793, variant="descends")
    outcome("passed")
    print(
        f"  measured: verified head {verified[:12]} is NOT an ancestor of master; the squash landed as "
        f"{landing[:12]} carrying the same tree; the lane is at {lane_head[:12]}, which contains it"
    )

    item = item_for(793, verified, lane_present=True, merge_commit=landing)
    ops = OfflineOps(repo, item)
    result = closeout(item, ops)
    print(describe(result))

    recorded = journal_verify(repo, 793)
    ok(
        "the invariant is SATISFIED — no REMAINS VERIFY_EVIDENCE_MISSING for a squash-merged item",
        VERIFY_INVARIANT not in {finding.code for finding in result.remaining},
        f"remaining={[f.code for f in result.remaining]}",
    )
    ok("the item reaches a terminal verdict", result.verdict == OK, f"verdict={result.verdict}")
    ok(
        "the record names the VERIFIED head commit the evidence is against",
        recorded.get("commit") == verified,
        f"recorded={recorded}",
    )
    ok("the record names the LANDING it stands for", recorded.get("landing") == landing, f"recorded={recorded}")
    ok(
        "the record names the tree the gate actually ran in",
        recorded.get("measured") == lane_head,
        f"recorded={recorded}",
    )
    ok("the record says HOW it stands for it", recorded.get("via") == "contains", f"recorded={recorded}")
    ok("the record still names its provenance", recorded.get("source") == "lane", f"recorded={recorded}")
    ran = gate_ran()
    ok("the gate really ran", ran is not None, "no gate run recorded")
    if ran is not None:
        _where, measured = ran
        ok(
            "the gate ran at the LANE's head — the current tree — not at the obsolete head",
            measured == lane_head,
            f"measured {measured[:12]} vs lane head {lane_head[:12]}",
        )
        ok("the gate did NOT run at the replaced branch tip", measured != verified, measured[:12])
    ok(
        "no network-facing step was reached",
        not [call for call in ops.calls if call in OfflineOps.NETWORK_STEPS],
        f"calls={ops.calls}",
    )
    ok(
        "the lane was reclaimed once its record existed",
        "reclaim-lane" in ops.calls and not lane.exists(),
        f"calls={ops.calls} lane_exists={lane.exists()}",
    )


def case_squash_lane_predating_the_landing_is_refused() -> None:
    """The original complaint: a lane of the default branch that carries none of this item."""
    stage("negative control — a lane that does not contain the landing is still REFUSED, by name (#1098)")
    repo, _lane, verified, landing, lane_head = world_squash("squash-premerge", 794, variant="pre_merge")
    outcome("passed")
    print(
        f"  measured: the lane is at {lane_head[:12]} (the default branch before this item landed); "
        f"the verified head {verified[:12]} and the landing {landing[:12]} are both absent from it"
    )

    item = item_for(794, verified, lane_present=True, merge_commit=landing)
    ops = OfflineOps(repo, item)
    result = closeout(item, ops)

    verification = step(result, "record-verification")
    detail = verification.detail if verification else ""
    ok(
        "the invariant is still broken and still NAMED",
        VERIFY_INVARIANT in {finding.code for finding in result.remaining},
        f"remaining={[f.code for f in result.remaining]}",
    )
    ok(
        "the record-verification step FAILED — nothing green was recorded",
        verification is not None and verification.outcome == FAILED,
        f"outcome={verification.outcome if verification else 'no step'} detail={detail[:200]}",
    )
    # The exact refusal clause — not a substring over a message that *contains* the
    # lane head's sha, which a green message would satisfy too. That vacuity is the
    # failure mode this control exists to avoid.
    ok(
        "the refusal is the refusal, naming the lane head and the commit it would have to be",
        f"lane head {lane_head[:12]} is not the verified commit {verified[:12]} and does not contain" in detail,
        detail[:300],
    )
    ok("nothing green was reported for it", "verify green" not in detail, detail[:300])
    ok(
        "the refusal names the landing that could not license the lane",
        f"landing {landing[:12]}" in detail,
        detail[:300],
    )
    ok("the gate never ran in that lane", gate_ran() is None, "a gate run was recorded")
    ok("no record was written for a tree that carries none of this item's work", journal_verify(repo, 794) == {}, "a journal exists")
    ok("the lane is kept, not reclaimed over the missing evidence", "reclaim-lane" not in ops.calls, f"calls={ops.calls}")


def case_squash_drifted_head_repoints_the_subject() -> None:
    """#1149: a branch that advanced after the squash is closed out on the LANDED tree.

    GitHub's live ``head_commit`` for this shape is a tree that **never landed** — measured
    on #977/#978 through PR #984: 54 files and 4731 insertions away from the commit the
    squash landed as — so it cannot be the subject of evidence for work that landed. The
    landing carries the landed tree by construction, becomes the subject, and the drifted
    head is journalled beside it rather than silently substituted. Without this the item is
    **structurally unclosable**: the port refuses the live head ("contains the landing, but
    the landing carries a different tree") and there is no other subject to offer.
    """
    stage("a branch that advanced after the SQUASH is closed out on the tree that LANDED (#1149)")
    repo, lane, verified, landing, lane_head = world_squash("squash-drifted", 798, variant="different_tree")
    outcome("passed")
    print(
        f"  measured: the live head {verified[:12]} carries a commit the squash did not, so its tree "
        f"never landed; the landing {landing[:12]} carries the tree that did; the lane is at {lane_head[:12]}"
    )

    item = item_for(798, verified, lane_present=True, merge_commit=landing)
    ops = OfflineOps(repo, item)
    result = closeout(item, ops)
    print(describe(result))

    recorded = journal_verify(repo, 798)
    ok(
        "the invariant is SATISFIED — no REMAINS VERIFY_EVIDENCE_MISSING for a drifted branch",
        VERIFY_INVARIANT not in {finding.code for finding in result.remaining},
        f"remaining={[f.code for f in result.remaining]}",
    )
    ok("the item reaches a terminal verdict", result.verdict == OK, f"verdict={result.verdict}")
    ok(
        "the record's SUBJECT is the landed tree's commit, not the un-landed live head",
        recorded.get("commit") == landing,
        f"recorded={recorded}",
    )
    ok(
        "the record never names the un-landed live head as the verified commit",
        recorded.get("commit") != verified,
        f"recorded={recorded}",
    )
    ok(
        "the record names the live head it drifted from — disclosed, never hidden",
        recorded.get("drifted_head") == verified,
        f"recorded={recorded}",
    )
    ok("the record names the landing it stands for", recorded.get("landing") == landing, f"recorded={recorded}")
    ok(
        "the record names the tree the gate actually ran in",
        recorded.get("measured") == lane_head,
        f"recorded={recorded}",
    )
    verification = step(result, "record-verification")
    detail = verification.detail if verification else ""
    ok(
        "the step's detail says which tree the evidence is against, and why",
        "advanced past the squash" in detail and "the evidence is against the tree that landed" in detail,
        detail[:300],
    )
    ran = gate_ran()
    ok("the gate really ran", ran is not None, "no gate run recorded")
    if ran is not None:
        _where, measured = ran
        ok(
            "the gate ran at the LANE's head — the current tree",
            measured == lane_head,
            f"measured {measured[:12]} vs lane head {lane_head[:12]}",
        )
    ok(
        "no network-facing step was reached",
        not [call for call in ops.calls if call in OfflineOps.NETWORK_STEPS],
        f"calls={ops.calls}",
    )
    ok(
        "the lane was reclaimed once its record existed",
        "reclaim-lane" in ops.calls and not lane.exists(),
        f"calls={ops.calls} lane_exists={lane.exists()}",
    )


def case_a_drifted_subject_is_refused_at_the_port() -> None:
    """Negative control: the tree half of #1098's rule is still load-bearing (#1149).

    The subject resolution moves the evidence onto the landing *because* a drifted live head
    is not the landed content. This drives the port directly with that very pair, so the
    layer that must refuse it is provoked on its own — and so the refusal is required to name
    the **true** cause ("it DOES contain the landing, and the landing carries a different
    tree") rather than the cause #1098's single message named for both ("and does not contain
    it"), which diagnosed #977/#978 as the one thing they were not.
    """
    stage("negative control — a drifted subject handed to the port is REFUSED, in its own words (#1149)")
    repo, _lane, verified, landing, lane_head = world_squash(
        "squash-drifted-port", 799, variant="different_tree"
    )
    outcome("passed")
    print(
        f"  measured: the lane at {lane_head[:12]} contains the landing {landing[:12]}; the subject "
        f"offered is the un-landed live head {verified[:12]}"
    )

    item = item_for(799, verified, lane_present=True, merge_commit=landing)
    ops = OfflineOps(repo, item)
    try:
        ops.record_verification(799, verified, landing)
    except RuntimeError as refused:
        message = str(refused)
    else:
        message = ""
    ok("the port REFUSED the drifted subject", bool(message), "the port accepted an un-landed live head")
    ok("nothing green was recorded for it", journal_verify(repo, 799) == {}, "a journal exists")
    ok("the gate never ran in that lane", gate_ran() is None, "a gate run was recorded")
    ok(
        "the refusal opens by naming the lane head and the commit it would have to be",
        f"lane head {lane_head[:12]} is not the verified commit {verified[:12]}" in message,
        message[:300],
    )
    ok(
        "the refusal names the TRUE cause — it DOES contain the landing, and the tree differs",
        f"it DOES contain the landing {landing[:12]}" in message and "names a tree that never landed" in message,
        message[:300],
    )
    ok(
        "and it no longer reports the cause that was not the cause",
        "and does not contain it" not in message,
        message[:300],
    )


# ---------------------------------------------------------------------------
# 6/7. the fix is load-bearing: disable each half and watch its case go red
# ---------------------------------------------------------------------------


def case_mutants() -> None:
    stage("MUTANT — disable each half of the fix and require its case to go red")

    original_remeasure = GhOps._remeasure
    original_owed = closeout_module._verification_owed
    original_admissible = GhOps._admissible
    original_subject = closeout_module.evidence_subject
    try:
        # MUTANT 1 — the port's re-measurement, i.e. the pre-#786 code exactly.
        # The signature tracks the shipping one: #834 added ``dead`` (so the refusal can
        # keep naming ``worktree-missing``), and a stub that does not accept it would
        # fail for an unexpected keyword argument instead of for the reason under test.
        def no_remeasure(self, issue: int, commit: str, dead: dict | None = None) -> str:
            raise RuntimeError(f"no lane worktree for #{issue}; the verified tree no longer exists")

        GhOps._remeasure = no_remeasure  # type: ignore[method-assign]
        repo, _lane, head = world("mutant-1", 791, reclaim=True)
        outcome("passed")
        item = item_for(791, head, lane_present=False)
        result = closeout(item, OfflineOps(repo, item))
        ok(
            "with the re-measurement disabled the wedge RETURNS",
            VERIFY_INVARIANT in {finding.code for finding in result.remaining},
            "the mutant produced a satisfied invariant, so the port fix is not load-bearing",
        )
        ok(
            "and it returns as the OLD message",
            "the verified tree no longer exists"
            in (step(result, "record-verification").detail if step(result, "record-verification") else ""),
            "the mutant did not reproduce the measured message",
        )
        GhOps._remeasure = original_remeasure  # type: ignore[method-assign]

        # MUTANT 2 — the driver's order guard, i.e. the pre-#786 driver exactly.
        closeout_module._verification_owed = lambda item, result, subject: ""  # type: ignore[assignment]
        repo, lane, head = world("mutant-2", 792, reclaim=False)
        outcome("parked")
        item = item_for(792, head, lane_present=True)
        ops = OfflineOps(repo, item)
        result = closeout(item, ops)
        ok(
            "with the order guard disabled the lane is DESTROYED by a park",
            "reclaim-lane" in ops.calls and not lane.exists(),
            f"calls={ops.calls} lane_exists={lane.exists()}",
        )
        ok(
            "and the driver recorded no refusal to report",
            result.withheld == [],
            f"withheld={result.withheld}",
        )
        closeout_module._verification_owed = original_owed  # type: ignore[assignment]

        # MUTANT 3 — the containment arm removed, i.e. the pre-#1098 port exactly. The
        # equality arm is left intact, so the ONLY thing this disables is the squash half.
        def equality_only(self, worktree: Path, head: str, commit: str, landing: str) -> str:
            if not commit or head == commit:
                return "equals"
            return ""

        GhOps._admissible = equality_only  # type: ignore[method-assign]
        repo, _lane, verified, landing, _lane_head = world_squash("mutant-3", 796, variant="descends")
        outcome("passed")
        item = item_for(796, verified, lane_present=True, merge_commit=landing)
        result = closeout(item, OfflineOps(repo, item))
        ok(
            "with the containment arm removed the SQUASH wedge RETURNS, so its case is load-bearing",
            VERIFY_INVARIANT in {finding.code for finding in result.remaining},
            "the mutant still satisfied the invariant, so the containment arm proves nothing",
        )
        ok(
            "and the wedge returns as the recorded pre-#1098 message",
            "is not the verified commit" in (step(result, "record-verification").detail if step(result, "record-verification") else ""),
            "the mutant did not reproduce the measured message",
        )
        GhOps._admissible = original_admissible  # type: ignore[method-assign]

        # MUTANT 4 — containment without the tree check: the arm is *nearly* right, and a
        # landing built from other content is admitted. Without this the tree half of the
        # rule would be a formality nobody had provoked.
        #
        # Driven at the PORT, not through ``closeout``: since #1149 the driver no longer
        # offers a drifted pair as a subject (it resolves the subject to the landing first),
        # so a closeout-level case here would pass under the mutant and under the fix alike
        # — a vacuous control. The port is the layer that owns the tree half, so the port is
        # where it is provoked.
        def containment_without_the_tree_check(self, worktree: Path, head: str, commit: str, landing: str) -> str:
            if not commit or head == commit:
                return "equals"
            if landing and lifecycle_cli.commit_is_contained(worktree, landing, head):
                return "contains"
            return ""

        GhOps._admissible = containment_without_the_tree_check  # type: ignore[method-assign]
        repo, _lane, verified, landing, _lane_head = world_squash("mutant-4", 797, variant="different_tree")
        outcome("passed")
        item = item_for(797, verified, lane_present=True, merge_commit=landing)
        ops = OfflineOps(repo, item)
        try:
            admitted = ops.record_verification(797, verified, landing)
        except RuntimeError as refused:
            admitted = f"REFUSED: {refused}"
        ok(
            "with the TREE check removed the un-landed live head IS admitted, so its case is load-bearing",
            "verify green" in admitted,
            admitted[:300],
        )
        ok(
            "and an attestation is written for a tree the item was never verified in",
            journal_verify(repo, 797).get("commit") == verified,
            f"recorded={journal_verify(repo, 797)}",
        )
        GhOps._admissible = original_admissible  # type: ignore[method-assign]

        # MUTANT 5 — the subject resolution removed, i.e. the pre-#1149 driver exactly: the
        # evidence is put on the pull request's LIVE head, which for a branch that advanced
        # after the squash is a tree that never landed. The positive case must go red, or the
        # remedy is decoration.
        closeout_module.evidence_subject = lambda item, ops: (  # type: ignore[assignment]
            str((item.get("pr") or {}).get("head_commit") or ""),
            str((item.get("pr") or {}).get("merge_commit") or ""),
            "",
        )
        repo, _lane, verified, landing, _lane_head = world_squash("mutant-5", 800, variant="different_tree")
        outcome("passed")
        item = item_for(800, verified, lane_present=True, merge_commit=landing)
        result = closeout(item, OfflineOps(repo, item))
        verification = step(result, "record-verification")
        detail = verification.detail if verification else ""
        ok(
            "with the subject resolution disabled the drifted branch is UNCLOSABLE again",
            VERIFY_INVARIANT in {finding.code for finding in result.remaining},
            "the mutant still satisfied the invariant, so the resolution proves nothing",
        )
        ok(
            "and the wedge returns naming the un-landed live head as the subject",
            f"is not the verified commit {verified[:12]}" in detail
            and f"it DOES contain the landing {landing[:12]}" in detail,
            detail[:300],
        )
        ok(
            "no record is written against the un-landed head",
            journal_verify(repo, 800) == {},
            "a journal exists",
        )
        closeout_module.evidence_subject = original_subject  # type: ignore[assignment]
    finally:
        GhOps._remeasure = original_remeasure  # type: ignore[method-assign]
        closeout_module._verification_owed = original_owed  # type: ignore[assignment]
        GhOps._admissible = original_admissible  # type: ignore[method-assign]
        closeout_module.evidence_subject = original_subject  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# the contract: the order is declared where an executor reads it
# ---------------------------------------------------------------------------


def case_declarations() -> None:
    stage("the order is declared where an executor reads it")
    declarations = (
        ("AGENTS.md", ("close-out", "lane teardown"), ("governance/lifecycle", "reclaim")),
        (
            "governance/lifecycle/README.md",
            ("record-verification", "reclaim-lane"),
            ("close-out", "lane"),
        ),
        (
            "governance/lifecycle/closeout.py",
            ("order", "reclaim"),
            ("record-verification",),
        ),
        # #1149: the rule the cases above provoke must be declared where the model is read,
        # or it is a behaviour nobody reviewing the invariant can see — and this gate would
        # be enforcing a rule the vocabulary does not carry.
        (
            "governance/lifecycle/model.py",
            ("the commit whose tree is the tree that landed",),
            ("#1149",),
        ),
        (
            "governance/lifecycle/README.md",
            ("the tree that landed",),
            ("#1149",),
        ),
    )
    for path, required, alternatives in declarations:
        text = (ROOT / path).read_text(encoding="utf-8")
        missing = [marker for marker in required if marker not in text]
        matched = [marker for marker in alternatives if marker in text]
        ok(
            f"{path} states the ordering",
            not missing and bool(matched),
            f"missing={missing} alternatives_found={matched}",
        )

    # A vacuity control on the check above: a file with none of the markers must be
    # reported missing, or the loop could be passing on an empty read.
    probe = (WORK / "vacuity.md")
    probe.write_text("nothing of the sort here\n", encoding="utf-8")
    text = probe.read_text(encoding="utf-8")
    ok(
        "vacuity control — a file without the markers is detected as missing",
        [marker for marker in ("close-out", "lane teardown") if marker not in text] != [],
        "the declaration probe matched text it should not have",
    )


def main() -> int:
    write_stub()
    print("== the close-out's verification ordering, provoked (#786, #1098, #1149) ==")
    case_reclaimed_lane_green()
    case_reclaimed_lane_red()
    case_reclaimed_lane_unreachable()
    case_parked_keeps_the_lane()
    case_unrelated_failure_still_reclaims()
    case_squash_lane_from_the_default_branch()
    case_squash_lane_predating_the_landing_is_refused()
    case_squash_drifted_head_repoints_the_subject()
    case_a_drifted_subject_is_refused_at_the_port()
    case_mutants()
    case_declarations()

    if FAILURES:
        print(
            f"check-lifecycle-verify-order: FAIL — {len(FAILURES)} of {COUNT} check(s) failed",
            file=sys.stderr,
        )
        return 1
    print(f"check-lifecycle-verify-order: OK — {COUNT} check(s) measured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
