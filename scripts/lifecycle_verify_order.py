#!/usr/bin/env python3
"""Drive the close-out's verification ordering against a real repository (#786).

Invoked by ``scripts/check-lifecycle-verify-order.sh``; not a check itself. It exists
as a file rather than an inline heredoc because it is the largest body of logic in the
script family and a heredoc inside the gate is a syntax error waiting for a stray
word — the shared shell's own failure mode.

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
green: the stub's outcomes are read by the shipping consumer, and cases 2 and 3 below
are the negative controls that prove it.
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


def item_for(issue: int, head: str, *, lane_present: bool, session: str = "") -> dict:
    """The lifecycle item, with every GitHub-derived fact already terminal.

    A merge, a deleted branch, a released claim and a consumed order are *not* the
    subject here, and leaving them terminal means the driver skips them — so a gate
    never reaches the network. The two facts that are the subject, the lane and the
    verification record, are the ones left open.
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
            "merge_commit": head,
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
# 6/7. the fix is load-bearing: disable each half and watch its case go red
# ---------------------------------------------------------------------------


def case_mutants() -> None:
    stage("MUTANT — disable each half of the fix and require its case to go red")

    original_remeasure = GhOps._remeasure
    original_owed = closeout_module._verification_owed
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
        closeout_module._verification_owed = lambda item, result: ""  # type: ignore[assignment]
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
    finally:
        GhOps._remeasure = original_remeasure  # type: ignore[method-assign]
        closeout_module._verification_owed = original_owed  # type: ignore[assignment]


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
    print("== the close-out's verification ordering, provoked (#786) ==")
    case_reclaimed_lane_green()
    case_reclaimed_lane_red()
    case_reclaimed_lane_unreachable()
    case_parked_keeps_the_lane()
    case_unrelated_failure_still_reclaims()
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
