#!/usr/bin/env python3
"""cli.py — the PR runner's entrypoint (issue #1343, parent #1295).

---knowledge---
module_id: fleet.runner.cli
system: fleet
app: fleet
solution_class: pattern
patterns: []
derives_from: null
owner_sme: unassigned
tier: L1
interfaces: [runner_dir, Transports, holds_path, read_holds, write_holds, gh_ready, list_open_prs, read_status_evidence, (+21 more)]
invariants: ""
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---

    python3 fleet/runner/cli.py plan                 # the actions, nothing done
    python3 fleet/runner/cli.py run --once [--apply] # one cycle (dry-run by default)
    python3 fleet/runner/cli.py run --loop [--apply] [--interval 120]
    python3 fleet/runner/cli.py status               # what is verifying / merged / blocked and why
    python3 fleet/runner/cli.py hold <pr> --reason <text>
    python3 fleet/runner/cli.py unhold <pr>
    python3 fleet/runner/cli.py train [--apply]      # the merge train: ONE verify for the whole wave (#1411)
    python3 fleet/runner/cli.py train --plan         # fold locally, report, execute nothing (safe on the live board)
    python3 fleet/runner/cli.py train-status         # what the trains folded, refused, attributed and landed

ONE CYCLE (`run --once`)
  0. the host role: `AO_RUNNER_HOST_ROLE` must be `primary` (the env contract,
     infra/fleet/env_contract.py) — any other value is `host-role-not-primary`
     (CANNOT-ASSESS, rc 2) so a standby host installs the rung and does nothing.
  1. preconditions, by name (lesson 5): `gh` present and authenticated, else
     `gh-unauthenticated`; `gcloud` absent is a NOTE (cloud builds cannot be
     listed or cancelled; check-runs are still read through gh).
  2. `fleet/gatelock.py prune --apply` (lesson 7).
  3. one locked fetch of master with an explicit refspec (lesson 8) -> master tip.
  4. gather: open non-draft PRs + heads (gh), evidence per (pr, head) from
     check-runs + the gate-of-record status + local markers, live builds
     (gcloud), the hold set.
  5. `conclude` the heads that carry NO gate-of-record at all (#1506): the CI
     venue of record's own concluded verdict is published for them, from the
     poster, and read back into the evidence table — so this step runs BEFORE
     the plan is drawn. It is the half that `--apply` gates, because it exists
     only to change what a head carries (see verify.py's convergence pass).
  6. `plan()` (pure), printed and recorded.
  7. execute: cancel stale builds, verify up to `--capacity` heads in parallel
     (each in its own held worktree), then merge the greens through the
     merged-tree seam and `scripts/merge-pr.sh` — stopping on a non-compliant
     landed tip.

Every step writes `.fleet/runner/ledger.jsonl` (lesson 9); `status` answers
from it. Dry-run by default: `--apply` is what merges and what concludes (the
verify + post half is always real: a verify writes nothing to the repository
but a status).

Tri-state exit: 0 OK / 1 NOT-OK (a refusal or a red) / 2 CANNOT-ASSESS.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet.runner import evidence as ev  # noqa: E402
from fleet.runner.model import (  # noqa: E402
    AWAIT,
    CANCEL_STALE,
    DEFER,
    GATE_CONTEXT,
    HOLD,
    MERGE,
    MERGE_VERB,
    REFUSE,
    VERIFY,
    Action,
    LiveBuild,
    OpenPR,
    PruneResult,
)
from fleet.runner.plan import DEFAULT_CAPACITY, explain, plan  # noqa: E402
from fleet.runner import capacity as capacity_mod  # noqa: E402
from fleet.runner import merge as merge_mod  # noqa: E402
from fleet.runner import train as train_mod  # noqa: E402
from fleet.runner import verify as verify_mod  # noqa: E402
from fleet.runner.verify import Command, Ledger, Result  # noqa: E402

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2

ROLE_ENV = "AO_RUNNER_HOST_ROLE"
PRIMARY = "primary"
REPO_SLUG = "kushin77/agent-orchestrator"

#: How much of a red's own `verify: FAIL (...)` summary line `status` quotes. The
#: ledger keeps up to `verify.MAX_SUMMARY_CHARS`; `status` is a one-line-per-PR
#: view, so it quotes the head of it and the ledger (plus the kept transcript)
#: carries the rest (issue #1384).
STATUS_WHY_CHARS = 160


def runner_dir() -> Path:
    fleet_dir = Path(os.environ.get("AO_FLEET_DIR", ROOT / ".fleet"))
    return fleet_dir / "runner"


# --- transports (real) -----------------------------------------------------------
class Transports:
    """Every external seam, injectable. `real()` wires subprocesses."""

    def __init__(self, *, git: Command, gh: Command, gcloud: Command | None, sh: Command, env: dict[str, str]):
        self.git, self.gh, self.gcloud, self.sh, self.env = git, gh, gcloud, sh, env

    @classmethod
    def real(cls) -> "Transports":
        import shutil

        return cls(
            git=verify_mod.real_command("git"),
            gh=verify_mod.real_command("gh"),
            gcloud=verify_mod.real_command("gcloud") if shutil.which("gcloud") else None,
            sh=_real_sh(),
            env=dict(os.environ),
        )


def _real_sh() -> Command:
    """Run an argv whose first element is the program (bash, python3, ...)."""

    def run(argv: list[str], *, cwd=None, env=None, timeout=None) -> Result:
        return verify_mod.real_command(argv[0])(argv[1:], cwd=cwd, env=env, timeout=timeout)

    return run


# --- holds -----------------------------------------------------------------------
def holds_path(base: Path) -> Path:
    return base / "holds.json"


def read_holds(base: Path) -> dict[int, str]:
    path = holds_path(base)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError:
        return {}
    return {int(k): str(v) for k, v in data.items()}


def write_holds(base: Path, holds: dict[int, str]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    holds_path(base).write_text(json.dumps({str(k): v for k, v in sorted(holds.items())}, indent=2) + "\n", encoding="utf-8")


# --- gathering ---------------------------------------------------------------------
def gh_ready(gh: Command) -> str | None:
    """None when gh is present and authenticated, else the refusal name."""
    probe = gh(["auth", "status"])
    if probe.rc == 127:
        return "gh-missing"
    if probe.rc != 0:
        return "gh-unauthenticated"
    return None


def list_open_prs(gh: Command) -> list[OpenPR] | None:
    result = gh(
        ["pr", "list", "--repo", REPO_SLUG, "--state", "open", "--limit", "100", "--json", "number,headRefOid,mergeable,isDraft,title,baseRefName"]
    )
    if not result.ok:
        return None
    try:
        rows = json.loads(result.out or "[]")
    except ValueError:
        return None
    return [
        OpenPR(
            number=int(r["number"]),
            head_sha=str(r.get("headRefOid") or ""),
            base_ref=str(r.get("baseRefName") or "master"),
            mergeable=str(r.get("mergeable") or "UNKNOWN"),
            draft=bool(r.get("isDraft")),
            title=str(r.get("title") or ""),
        )
        for r in rows
        if str(r.get("baseRefName") or "master") == "master"
    ]


def read_status_evidence(table: ev.EvidenceTable, gh: Command, pr: int, sha: str) -> None:
    """Add this head's own `ao/gate-of-record` status to `table`, when it has one."""
    status = gh(["api", f"repos/{REPO_SLUG}/commits/{sha}/status"])
    if not status.ok:
        return
    try:
        for record in ev.from_commit_status(pr, sha, json.loads(status.out or "{}"), context=GATE_CONTEXT):
            table.add(record)
    except ValueError:
        pass


def gather_evidence(gh: Command, prs: list[OpenPR], marker_dir: Path) -> ev.EvidenceTable:
    table = ev.EvidenceTable(ev.from_local_markers(marker_dir))
    for pr in prs:
        runs = gh(["api", f"repos/{REPO_SLUG}/commits/{pr.head_sha}/check-runs"])
        if runs.ok:
            try:
                for record in ev.from_check_runs(pr.number, pr.head_sha, json.loads(runs.out or "{}")):
                    table.add(record)
            except ValueError:
                pass
        read_status_evidence(table, gh, pr.number, pr.head_sha)
    return table


def list_live_builds(gcloud: Command | None) -> tuple[list[LiveBuild], str]:
    """Cloud builds for PR heads. (builds, note) — a missing gcloud is a note, not a red."""
    if gcloud is None:
        return [], "gcloud-missing: live builds cannot be listed or cancelled"
    result = gcloud(["builds", "list", "--ongoing", "--format=json", "--limit=100"])
    if not result.ok:
        return [], f"gcloud-builds-list-failed: {(result.err or '').strip()[:120]}"
    try:
        rows = json.loads(result.out or "[]")
    except ValueError:
        return [], "gcloud-builds-list-unparseable"
    out: list[LiveBuild] = []
    for row in rows:
        subs = row.get("substitutions") or {}
        pr_text = str(subs.get("_PR_NUMBER") or "")
        sha = str(subs.get("COMMIT_SHA") or "")
        if not pr_text.isdigit() or not sha:
            continue
        out.append(LiveBuild(id=str(row.get("id")), pr=int(pr_text), sha=sha, status=ev.state_of_cloud_build(row.get("status"))))
    return out, ""


def master_tip(git: Command, base: Path) -> str | None:
    with verify_mod.fetch_lock(base / "fetch.lock"):
        git(["fetch", "--quiet", "origin", verify_mod.MASTER_REFSPEC], cwd=ROOT)
    tip = git(["rev-parse", "refs/remotes/origin/master"], cwd=ROOT)
    return tip.out.strip() if tip.ok and tip.out.strip() else None


# --- the cycle ----------------------------------------------------------------------
def role_refusal(env: dict[str, str]) -> str | None:
    role = env.get(ROLE_ENV, "standby")
    return None if role == PRIMARY else f"host-role-not-primary:{role or 'unset'}"


def cycle(
    transports: Transports,
    *,
    base: Path,
    apply: bool,
    capacity: int | None = None,
    execute: bool = True,
    out=sys.stdout,
    host_probe=capacity_mod.probe_host,
) -> int:
    """One full cycle. Returns the tri-state rc.

    `capacity` is the `--capacity` override; otherwise the width is
    `AO_RUNNER_CAPACITY` (env contract) or min(8, nproc // 2). Either way it is
    backed off before the fan-out when the host is loaded (capacity.py) and the
    effective width is ledgered.
    """
    base.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(base / "ledger.jsonl")
    ledger.record("cycle-start", apply=apply, execute=execute, role=transports.env.get(ROLE_ENV, ""))
    load1, mem_gb, nproc = host_probe()
    declared = int(capacity) if capacity is not None else capacity_mod.declared_capacity(transports.env, nproc=nproc)
    width = capacity_mod.effective_capacity(
        declared, load1=load1, mem_available_gb=mem_gb, nproc=nproc, mem_floor_gb=capacity_mod.declared_mem_floor_gb(transports.env)
    )
    ledger.record("capacity", declared=width.declared, effective=width.effective, reason=width.reason or "none", load1=load1, mem_available_gb=mem_gb, nproc=nproc)
    if width.reason:
        print(f"  NOTE  {width.reason}: fan-out {width.declared} -> {width.effective}", file=out)
        ledger.record("note", detail=width.reason)
    capacity = width.effective

    if execute:
        refusal = role_refusal(transports.env)
        if refusal:
            ledger.record("cannot-assess", reason=refusal)
            print(f"runner: CANNOT-ASSESS — {refusal} (set {ROLE_ENV}={PRIMARY} on the shared-services pair only)", file=out)
            return CANNOT_ASSESS

    gh_problem = gh_ready(transports.gh)
    if gh_problem:
        ledger.record("cannot-assess", reason=gh_problem)
        print(f"runner: CANNOT-ASSESS — {gh_problem}: install gh and run 'gh auth login'", file=out)
        return CANNOT_ASSESS

    prune = PruneResult(True, "skipped:plan-only")
    if execute:
        ok, detail = verify_mod.gatelock_prune(transports.sh, repo=ROOT)
        prune = PruneResult(ok, detail)
        ledger.record("gatelock-prune", ok=ok, detail=detail)

    tip = master_tip(transports.git, base)
    prs = list_open_prs(transports.gh)
    if prs is None:
        ledger.record("cannot-assess", reason="gh-pr-list-failed")
        print("runner: CANNOT-ASSESS — gh-pr-list-failed", file=out)
        return CANNOT_ASSESS
    table = gather_evidence(transports.gh, prs, base / "local-green")
    builds, note = list_live_builds(transports.gcloud)
    if note:
        print(f"  NOTE  {note}", file=out)
        ledger.record("note", detail=note)
    holds = read_holds(base)

    # #1506: settle the context of every head BEFORE the plan is drawn from it.
    # `conclude` is the verb nothing scheduled until this pass existed, and the
    # filter is this cycle's own evidence table, so an already-converged head
    # costs no call (verify.py, "the verdict-convergence pass").
    if execute and apply:
        for number, sha in verify_mod.publish_concluded(
            [(pr.number, pr.head_sha) for pr in prs if not pr.draft],
            already_carrying=verify_mod.heads_carrying_the_context(table),
            conclude=verify_mod.real_conclude_status(transports.sh, ROOT),
            ledger=ledger,
            out=out,
        ):
            # the plan reads THIS table, so the verdicts just published are read
            # back into it rather than left for the next cycle to discover
            read_status_evidence(table, transports.gh, number, sha)

    actions = plan(prs, table, builds, holds, prune, tip, capacity)
    for line in explain(actions):
        print(f"  {line}", file=out)
    ledger.record("plan", master_tip=tip, prs=len(prs), actions=[a.as_dict() for a in actions])
    if not execute:
        return OK

    rc = OK
    # cancel stale (lesson 1)
    for action in (a for a in actions if a.kind == CANCEL_STALE):
        if transports.gcloud is None:
            ledger.record("refuse", pr=action.pr, sha=action.sha, reason="cancel-stale-needs-gcloud", build_id=action.build_id)
            continue
        result = transports.gcloud(["builds", "cancel", action.build_id, "--quiet"])
        ledger.record("cancel-stale", pr=action.pr, sha=action.sha, build_id=action.build_id, ok=result.ok, reason=action.reason)

    for action in (a for a in actions if a.kind in (HOLD, REFUSE, AWAIT, DEFER)):
        ledger.record(action.kind, pr=action.pr, sha=action.sha, reason=action.reason)
        if action.kind == REFUSE:
            rc = max(rc, NOT_OK)

    # verify (lessons 6-8, 10), up to `capacity` in parallel
    verifies = [a for a in actions if a.kind == VERIFY]
    if verifies:
        post = verify_mod.real_post_status(transports.sh, ROOT)

        def one(action: Action):
            return verify_mod.run_verify(
                action.pr, action.sha, repo=ROOT, runner_dir=base, git=transports.git, sh=transports.sh, post_status=post, ledger=ledger
            )

        with ThreadPoolExecutor(max_workers=max(1, capacity)) as pool:
            for outcome in pool.map(one, verifies):
                print(f"  verified     #{outcome.pr} {outcome.sha[:12]} rc={outcome.rc} {outcome.state} posted={outcome.posted}", file=out)
                if outcome.state != "green":
                    rc = max(rc, NOT_OK if outcome.state == "red" else rc)

    # merge (lessons 3, 4)
    classify = merge_mod.real_classify_tip(ROOT)
    for action in (a for a in actions if a.kind == MERGE):
        outcome = merge_mod.run_merge(
            action.pr,
            action.sha,
            repo=ROOT,
            sh=transports.sh,
            git=transports.git,
            ledger=ledger,
            apply=apply,
            runner_dir=base,
            classify_tip=classify,
        )
        print(f"  merge        #{outcome.pr} {outcome.sha[:12]} rc={outcome.rc} {outcome.reason}", file=out)
        if outcome.rc != OK:
            rc = max(rc, outcome.rc)
        if outcome.stop:
            print("runner: STOP — the landed tip is non-compliant; no further merges this cycle", file=out)
            rc = NOT_OK
            break
    ledger.record("cycle-end", rc=rc)
    return rc


# --- status (lesson 9) --------------------------------------------------------------
def status_lines(rows: list[dict], holds: dict[int, str]) -> list[str]:
    """One line per PR the ledger knows: last verify, last post, last merge/refusal."""
    latest: dict[int, dict[str, dict]] = {}
    for row in rows:
        pr = row.get("pr")
        if pr is None:
            continue
        latest.setdefault(int(pr), {})[str(row.get("event"))] = row
    lines: list[str] = []
    for pr in sorted(set(latest) | set(holds)):
        events = latest.get(pr, {})
        bits = [f"#{pr}"]
        if pr in holds:
            bits.append(f"HELD:{holds[pr]}")
        if "merge" in events:
            bits.append(f"merged->{str(events['merge'].get('new_tip') or '')[:12]} at {events['merge'].get('at')}")
        elif "merge-dry-run" in events:
            bits.append(f"would-merge (dry-run) at {events['merge-dry-run'].get('at')}")
        if "verify" in events:
            v = events["verify"]
            bits.append(f"verify={v.get('state')} rc={v.get('rc')} sha={str(v.get('sha') or '')[:12]} at {v.get('at')}")
            # A red must be diagnosable from `status` alone (lesson 11, #1384):
            # WHICH check failed, and the gate's own summary line. `none-named`
            # is printed rather than omitted — a run that named no failing check
            # is a fact worth reading, not a blank.
            if str(v.get("state")) == "red":
                failing = [str(name) for name in (v.get("failing_checks") or [])]
                bits.append(f"failing:{','.join(failing) if failing else 'none-named'}")
                summary = str(v.get("verify_summary") or "").strip()
                if summary:
                    bits.append(f"why:{summary[:STATUS_WHY_CHARS]}")
                if v.get("evidence_log"):
                    bits.append(f"log:{v['evidence_log']}")
            note = str(v.get("evidence_note") or "")
            if note:
                bits.append(f"evidence:{note}")
        if "post" in events:
            p = events["post"]
            bits.append(f"posted={'ok' if p.get('ok') else 'FAILED'} rc={p.get('rc')}")
        if "await" in events:
            bits.append(f"awaiting:{events['await'].get('reason')}")
        if "defer" in events:
            # A head the width could not take is a PR the runner owes an
            # explanation for: it is DEFERred by name, never dropped in silence.
            bits.append(f"deferred:{events['defer'].get('reason')}")
        if "refuse" in events:
            bits.append(f"blocked:{events['refuse'].get('reason')}")
        if "stop" in events:
            bits.append(f"STOP:{events['stop'].get('reason')}")
        if "cancel-stale" in events:
            bits.append(f"cancelled-stale:{events['cancel-stale'].get('build_id')}")
        lines.append("  " + " | ".join(bits))
    return lines


def cmd_status(args: argparse.Namespace) -> int:
    base = runner_dir()
    ledger = Ledger(base / "ledger.jsonl")
    rows = ledger.rows()
    holds = read_holds(base)
    if not rows and not holds:
        print(f"runner: status — no ledger at {ledger.path} (nothing has run here)")
        return OK
    cycles = [r for r in rows if r.get("event") == "cycle-end"]
    starts = [r for r in rows if r.get("event") == "cycle-start"]
    last = cycles[-1] if cycles else None
    print(f"runner: status — {len(starts)} cycle(s) started, last rc {last.get('rc') if last else 'n/a'} at {last.get('at') if last else 'n/a'}")
    caps = [r for r in rows if r.get("event") == "capacity"]
    if caps:
        cap = caps[-1]
        print(f"  capacity: declared {cap.get('declared')} effective {cap.get('effective')} ({cap.get('reason')}) at {cap.get('at')}")
    for row in [r for r in rows if r.get("event") == "cannot-assess"][-3:]:
        print(f"  CANNOT-ASSESS {row.get('reason')} at {row.get('at')}")
    for line in status_lines(rows, holds):
        print(line)
    return OK


def cmd_hold(args: argparse.Namespace) -> int:
    base = runner_dir()
    holds = read_holds(base)
    holds[int(args.pr)] = args.reason
    write_holds(base, holds)
    Ledger(base / "ledger.jsonl").record("hold", pr=int(args.pr), reason=args.reason)
    print(f"runner: held #{args.pr}: {args.reason}")
    return OK


def cmd_unhold(args: argparse.Namespace) -> int:
    base = runner_dir()
    holds = read_holds(base)
    if int(args.pr) not in holds:
        print(f"runner: #{args.pr} is not held")
        return NOT_OK
    del holds[int(args.pr)]
    write_holds(base, holds)
    Ledger(base / "ledger.jsonl").record("unhold", pr=int(args.pr))
    print(f"runner: released #{args.pr}")
    return OK


def plan_from_fixture(path: Path, capacity: int = DEFAULT_CAPACITY) -> list[Action]:
    """Plan from a JSON fixture (offline): the gate and the docs drive this.

    Shape: {"master_tip": sha|null, "prs": [{number, head_sha, mergeable?, draft?}],
            "evidence": [{pr, sha, source, state, base_tip?, failing_checks?}],
            "builds": [{id, pr, sha, status, kind?}], "holds": {"<pr>": reason},
            "prune": {"ok": bool, "detail": str}, "capacity": int}
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    prs = [OpenPR(int(r["number"]), str(r["head_sha"]), mergeable=str(r.get("mergeable", "MERGEABLE")), draft=bool(r.get("draft", False))) for r in data.get("prs", [])]
    table = ev.EvidenceTable(
        [
            ev.Evidence(
                int(r["pr"]),
                str(r["sha"]),
                str(r["source"]),
                str(r["state"]),
                base_tip=r.get("base_tip"),
                # #1384: the names a local verify recorded, so a fixture can drive
                # the red's reason (`verify-red:<pr>:local-marker:<check>`) through
                # the REAL planner the gate runs.
                failing_checks=tuple(str(name) for name in (r.get("failing_checks") or [])),
            )
            for r in data.get("evidence", [])
        ]
    )
    builds = [LiveBuild(str(r["id"]), int(r["pr"]), str(r["sha"]), str(r["status"]), kind=str(r.get("kind", "cloud-build"))) for r in data.get("builds", [])]
    holds = {int(k): str(v) for k, v in (data.get("holds") or {}).items()}
    prune_data = data.get("prune") or {"ok": True, "detail": "fixture"}
    prune = PruneResult(bool(prune_data.get("ok", True)), str(prune_data.get("detail", "")))
    return plan(prs, table, builds, holds, prune, data.get("master_tip"), int(data.get("capacity", capacity)))


def cmd_plan(args: argparse.Namespace) -> int:
    if args.fixture:
        for line in explain(plan_from_fixture(Path(args.fixture), args.capacity if args.capacity is not None else DEFAULT_CAPACITY)):
            print(line)
        return OK
    return cycle(Transports.real(), base=runner_dir(), apply=False, capacity=args.capacity, execute=False)


def cmd_run(args: argparse.Namespace) -> int:
    base = runner_dir()
    base.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(base / "run.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("runner: PARKED — another runner cycle holds .fleet/runner/run.lock; nothing was run")
        return CANNOT_ASSESS
    try:
        if not args.loop:
            return cycle(Transports.real(), base=base, apply=args.apply, capacity=args.capacity)
        rc = OK
        while True:
            rc = cycle(Transports.real(), base=base, apply=args.apply, capacity=args.capacity)
            time.sleep(max(15, int(args.interval)))
        return rc  # pragma: no cover
    finally:
        os.close(lock_fd)


# --- the merge train (issue #1411, parent #1295) -------------------------------------
def bodies_by_pr(gh: Command, slug: str, prs: list[OpenPR]) -> dict[int, str | None]:
    """Each PR's OWN body; `None` when it could not be read.

    This is the ONLY source of a folded PR's `Closes` lines. Deriving them from a
    branch name is the #1266 defect (27 squash merges, 0 issues closed), and a
    body that could not be read is `None` — never `""`, which would read as "this
    PR closes nothing" and quietly refuse a PR for an API error.
    """
    out: dict[int, str | None] = {}
    for pr in prs:
        result = gh(["pr", "view", str(pr.number), "--repo", slug, "--json", "body"])
        if not result.ok:
            out[pr.number] = None
            continue
        try:
            out[pr.number] = str(json.loads(result.out or "{}").get("body") or "")
        except ValueError:
            out[pr.number] = None
    return out


def pr_state(gh: Command, slug: str, pr: int) -> dict:
    """`{state, mergeCommit}` for one PR, or `{}` when it could not be read."""
    result = gh(["pr", "view", str(pr), "--repo", slug, "--json", "state,mergeCommit"])
    if not result.ok:
        return {}
    try:
        data = json.loads(result.out or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def open_pr_for_branch(gh: Command, slug: str, branch: str) -> int | None:
    """The open PR whose head is `branch` — the number the round is keyed by."""
    result = gh(["pr", "list", "--repo", slug, "--head", branch, "--state", "open", "--json", "number", "--limit", "1"])
    if not result.ok:
        return None
    try:
        rows = json.loads(result.out or "[]")
    except ValueError:
        return None
    if not isinstance(rows, list) or not rows:
        return None
    try:
        return int(rows[0]["number"])
    except (KeyError, TypeError, ValueError):
        return None


def close_folded(gh: Command, slug: str, folded, *, train_id: str, train_sha: str, ledger: Ledger) -> list[int]:
    """Close the PRs the train landed, naming the train sha that landed them.

    A folded PR left open is the board lying about what happened: its commits are
    on master and its PR still reads as outstanding work.
    """
    closed: list[int] = []
    for candidate in folded:
        result = gh(
            [
                "pr",
                "close",
                str(candidate.number),
                "--repo",
                slug,
                "--comment",
                f"Landed in merge train `{train_id}` as {train_sha} — folded onto `master` with `--no-ff` and verified once as the train head.",
            ]
        )
        ledger.record("train-close", train_id=train_id, pr=candidate.number, ok=result.ok, train_sha=train_sha)
        if result.ok:
            closed.append(candidate.number)
    return closed


def train_cycle(
    transports: Transports,
    *,
    base: Path,
    apply: bool,
    plan_only: bool = False,
    slug: str = REPO_SLUG,
    out=sys.stdout,
    now: Callable[[], str] = verify_mod.now_iso,
    sleep: Callable[[float], None] = time.sleep,
    max_rounds: int = train_mod.MAX_ROUNDS,
    park_attempts: int = train_mod.PARK_ATTEMPTS,
    runtime: str = "Copilot agent (flash/train)",
) -> int:
    """One merge train: fold -> verify ONCE -> attribute -> land (issue #1411).

    Tri-state rc: 0 the wave was folded and is landed (or ready to land); 1 a red
    or a refusal; 2 nothing could be assessed (gh, the base tip, the fetch, the
    worktree, the PR). Every step writes a `train-*` row to the one ledger, so
    `train-status` answers from the record and not from memory (lesson 9).

    WHAT EACH MODE EXECUTES. `--plan` stops after the LOCAL fold: the fold is
    computed in a throwaway worktree, so it changes nothing, and nothing is
    pushed, verified or posted — which is what makes it safe to point at the live
    board. Without `--apply` the train is a DRY RUN in the sense `run` already
    uses: the branch is pushed and its PR opened (the train head has to exist
    somewhere for the record it posts and the landing it would do), the verify
    and the post are REAL — they are the half that makes a landing safe — and
    `scripts/merge-pr.sh` runs in ITS OWN dry-run mode, so nothing merges.
    `--apply` adds `AO_MERGE_APPLY=1` and the folded-PR closure.

    The gate-of-record this posts for the train head is not decoration: it is the
    merged-tree evidence the landing path reads back (`scripts/pr-queue.sh
    --check-merged-tree` looks for a green record for the head oid), which is how
    one verify serves a whole wave.
    """
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(base / "ledger.jsonl")
    train_id = "train/" + now().replace("-", "").replace(":", "")
    mode = "apply" if apply else "dry-run"
    print(f"runner: train {train_id} ({mode}{', plan only' if plan_only else ''})", file=out)
    ledger.record("train-start", train_id=train_id, apply=apply, plan_only=plan_only)

    gh_problem = gh_ready(transports.gh)
    if gh_problem:
        ledger.record("cannot-assess", reason=gh_problem, train_id=train_id)
        print(f"runner: CANNOT-ASSESS — {gh_problem}: install gh and run 'gh auth login'", file=out)
        return CANNOT_ASSESS

    tip = master_tip(transports.git, base)
    if tip is None:
        ledger.record("cannot-assess", reason="master-tip-unreadable", train_id=train_id)
        print("runner: CANNOT-ASSESS — master-tip-unreadable", file=out)
        return CANNOT_ASSESS

    prs = list_open_prs(transports.gh)
    if prs is None:
        ledger.record("cannot-assess", reason="gh-pr-list-failed", train_id=train_id)
        print("runner: CANNOT-ASSESS — gh-pr-list-failed", file=out)
        return CANNOT_ASSESS

    holds = read_holds(base)
    bodies = bodies_by_pr(transports.gh, slug, prs)
    candidates, skipped = train_mod.fold_candidates(prs, bodies, train_mod.labels_reader(transports.gh, slug), holds)
    print(f"  base {tip[:12]} | open {len(prs)} | foldable {len(candidates)} | skipped {len(skipped)}", file=out)
    for skip in skipped:
        print(f"  skipped      {skip.reason}", file=out)
        ledger.record("train-skip", train_id=train_id, pr=skip.pr, reason=skip.reason)
    ledger.record("train-plan", train_id=train_id, base_tip=tip, open_prs=len(prs), foldable=[c.number for c in candidates])

    if not candidates:
        print("runner: nothing to train — every open PR was skipped by name above", file=out)
        ledger.record("train-end", train_id=train_id, rc=OK, state="nothing-to-fold")
        return OK

    wave = list(candidates)
    rc = OK
    state = "no-round"
    last_detail = ""
    for round_no in range(1, max_rounds + 1):
        if not wave:
            state = "wave-exhausted"
            break
        branch = train_mod.round_branch(train_id, round_no)
        ledger.record("train-round", train_id=train_id, round=round_no, branch=branch, wave=[c.number for c in wave])
        print(f"  round {round_no}: {len(wave)} PR(s) -> {branch}", file=out)

        fetched = train_mod.fetch_candidates(transports.git, repo=ROOT, prs=[c.number for c in wave], lock_path=base / "fetch.lock")
        if not fetched.ok:
            ledger.record("cannot-assess", reason="train-fetch-failed", train_id=train_id, rc=fetched.rc)
            print(f"runner: CANNOT-ASSESS — train-fetch-failed: {(fetched.err or '').strip()[:160]}", file=out)
            ledger.record("train-end", train_id=train_id, rc=CANNOT_ASSESS, state="fetch-failed")
            return CANNOT_ASSESS

        tree = base / train_mod.TRAIN_DIR / f"{train_id.rsplit('/', 1)[-1]}-r{round_no}" / "tree"
        held = verify_mod.HeldWorktree(transports.git, repo=ROOT, path=tree, sha=tip)
        try:
            held.__enter__()
        except RuntimeError as exc:
            ledger.record("cannot-assess", reason=str(exc)[:200], train_id=train_id)
            print(f"runner: CANNOT-ASSESS — {exc}", file=out)
            ledger.record("train-end", train_id=train_id, rc=CANNOT_ASSESS, state="worktree-add-failed")
            return CANNOT_ASSESS

        try:
            fold = train_mod.fold(wave, repo=ROOT, git=transports.git, worktree=held.path, base_tip=tip, ledger=ledger, train_id=train_id)
            for skip in fold.skipped:
                print(f"  skipped      {skip.reason}", file=out)
                ledger.record("train-skip", train_id=train_id, pr=skip.pr, reason=skip.reason, round=round_no)
            print(f"  folded       {len(fold.folded)}/{len(wave)} -> {fold.head[:12] or '(none)'}", file=out)
            if not fold.folded:
                state = "nothing-folded"
                last_detail = "; ".join(skip.reason for skip in fold.skipped) or "no candidate merged"
                print(f"runner: nothing folded in round {round_no} — {last_detail}", file=out)
                break

            touched = train_mod.changed_paths(transports.git, repo=ROOT, base_tip=tip, sha=fold.head) or ()
            globs = train_mod.gate_paths(ROOT)
            body_path = base / train_mod.TRAIN_DIR / f"{train_id.rsplit('/', 1)[-1]}-r{round_no}.md"
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body = train_mod.compose_body(slug=slug, train_id=train_id, fold=fold, verify=None, attributions=(), touched=touched, globs=globs, runtime=runtime)
            body_path.write_text(body, encoding="utf-8")
            ledger.record(
                "train-body",
                train_id=train_id,
                round=round_no,
                path=str(body_path),
                bytes=len(body),
                closes=list(fold.closes),
                issues=list(fold.issues),
                touched=len(touched),
            )
            for issue in fold.issues:
                print(f"  trailer      Refs {slug}#{issue}", file=out)

            if plan_only:
                print(f"  plan         would push refs/heads/{branch}, open a PR, verify ONCE and land through {MERGE_VERB} (--apply)", file=out)
                print(f"  plan         the composed train body was written to {body_path}", file=out)
                print(body, file=out)
                state = "planned"
                break

            pushed = transports.git(["push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"], cwd=held.path)
            if not pushed.ok:
                ledger.record("train-refuse", train_id=train_id, round=round_no, reason="push-failed", detail=(pushed.err or "").strip()[:200])
                print(f"runner: NOT-OK — push-failed: {(pushed.err or '').strip()[:160]}", file=out)
                rc, state = max(rc, NOT_OK), "push-failed"
                break

            title = f"Merge train {train_id.rsplit('/', 1)[-1]}: {len(fold.folded)} pull request(s), one verified landing"
            created = transports.gh(["pr", "create", "--repo", slug, "--base", "master", "--head", branch, "--title", title, "--body-file", str(body_path)])
            if not created.ok:
                ledger.record("train-refuse", train_id=train_id, round=round_no, reason="pr-create-failed", detail=(created.err or "").strip()[:200])
                print(f"runner: CANNOT-ASSESS — pr-create-failed: {(created.err or '').strip()[:160]}", file=out)
                ledger.record("train-end", train_id=train_id, rc=CANNOT_ASSESS, state="pr-create-failed")
                return CANNOT_ASSESS
            pr_number = open_pr_for_branch(transports.gh, slug, branch)
            if pr_number is None:
                ledger.record("train-refuse", train_id=train_id, round=round_no, reason="pr-create-unconfirmed", branch=branch)
                print(f"runner: CANNOT-ASSESS — pr-create-unconfirmed:{branch}", file=out)
                ledger.record("train-end", train_id=train_id, rc=CANNOT_ASSESS, state="pr-create-unconfirmed")
                return CANNOT_ASSESS
            print(f"  pr           #{pr_number} {branch} head {fold.head[:12]}", file=out)
            ledger.record("train-pr", train_id=train_id, round=round_no, pr=pr_number, branch=branch, head=fold.head)

            def guard() -> str | None:
                moved = train_mod.base_moved(transports.git, repo=ROOT, base_tip=tip, lock_path=base / "fetch.lock")
                return f"base-moved:{tip[:12]}->{moved[:12]}" if moved else None

            record = train_mod.verify_once(
                git=transports.git,
                sh=transports.sh,
                post_status=verify_mod.real_post_status(transports.sh, ROOT),
                repo=ROOT,
                runner_dir=base,
                worktree=held.path,
                pr=pr_number,
                head=fold.head,
                ledger=ledger,
                train_id=train_id,
                attempts=park_attempts,
                sleep=sleep,
                pre_post=guard,
            )
            print(f"  verify       rc={record.rc} {record.state} attempts={record.attempts} posted={record.posted} {record.summary}", file=out)
            if record.note:
                print(f"  evidence     {record.note}", file=out)

            attributions: list[train_mod.Attribution] = []
            if record.state == "red":
                attributions = train_mod.attribute_red(
                    record.failing,
                    list(fold.folded),
                    repo=ROOT,
                    git=transports.git,
                    sh=transports.sh,
                    runner_dir=base,
                    base_tip=tip,
                    ledger=ledger,
                    train_id=train_id,
                )
                if not attributions:
                    print("  red          no failing check was named by the run (none-named)", file=out)
                for attribution in attributions:
                    suffix = f" — {attribution.detail}" if attribution.detail else ""
                    print(f"  red          {attribution.line()}{suffix}", file=out)
                culprits = train_mod.held_prs(attributions)
                for culprit, reason in culprits:
                    if apply:
                        holds[culprit] = reason
                        ledger.record("train-hold", train_id=train_id, pr=culprit, reason=reason)
                    else:
                        print(f"  would-hold   #{culprit}: {reason}", file=out)
                if apply and culprits:
                    write_holds(base, holds)
                wave = [candidate for candidate in fold.folded if all(candidate.number != culprit for culprit, _ in culprits)]

            body = train_mod.compose_body(
                slug=slug, train_id=train_id, fold=fold, verify=record, attributions=attributions, touched=touched, globs=globs, runtime=runtime
            )
            body_path.write_text(body, encoding="utf-8")
            edited = transports.gh(["pr", "edit", str(pr_number), "--repo", slug, "--body-file", str(body_path)])
            ledger.record("train-body", train_id=train_id, round=round_no, pr=pr_number, stage="evidence", edited=edited.ok, bytes=len(body))

            if record.state == "green" and record.posted:
                landed = transports.sh(["bash", MERGE_VERB, "--pr", str(pr_number)], cwd=ROOT, env={"AO_MERGE_APPLY": "1" if apply else "0"})
                print(f"  land         {MERGE_VERB} --pr {pr_number} rc={landed.rc} {'(applied)' if apply else '(dry-run)'}", file=out)
                if landed.rc != 0:
                    ledger.record(
                        "train-refuse",
                        train_id=train_id,
                        round=round_no,
                        pr=pr_number,
                        reason=f"merge-refused:{landed.rc}",
                        detail=(landed.err or landed.out).strip()[-200:],
                    )
                    print(f"runner: NOT-OK — merge-refused rc={landed.rc}: {(landed.err or landed.out).strip()[:200]}", file=out)
                    rc, state = max(rc, NOT_OK if landed.rc == 1 else CANNOT_ASSESS), "merge-refused"
                    break
                if not apply:
                    ledger.record("train-land-dry-run", train_id=train_id, round=round_no, pr=pr_number, head=fold.head)
                    print(f"runner: DRY RUN — {len(fold.folded)} PR(s) folded, verified once at {fold.head[:12]}; --apply is the only difference (nothing merged)", file=out)
                    state = "would-land"
                    break
                readback = pr_state(transports.gh, slug, pr_number)
                if str(readback.get("state") or "") != "MERGED":
                    ledger.record("train-refuse", train_id=train_id, round=round_no, pr=pr_number, reason="land-unconfirmed", readback=readback)
                    print(f"runner: NOT-OK — land-unconfirmed: {MERGE_VERB} returned 0 but #{pr_number} reads {(readback.get('state') or 'unreadable')}", file=out)
                    rc, state = max(rc, NOT_OK), "land-unconfirmed"
                    break
                train_sha = str(readback.get("mergeCommit") or "")
                ledger.record("train-land", train_id=train_id, round=round_no, pr=pr_number, train_sha=train_sha, head=fold.head)
                print(f"runner: LANDED {len(fold.folded)} PR(s) as {train_sha[:12]} (#{pr_number})", file=out)
                closed = close_folded(transports.gh, slug, fold.folded, train_id=train_id, train_sha=train_sha, ledger=ledger)
                print(f"  closed       {len(closed)}/{len(fold.folded)} folded PR(s) as landed", file=out)
                if len(closed) != len(fold.folded):
                    rc = max(rc, NOT_OK)
                state = "landed"
                break

            # Not a landing: a red (re-train the rest), or a green the base moved
            # out from under (re-fold — never land stale evidence), or no verdict.
            last_detail = record.note or record.summary or record.state
            if record.state == "red":
                rc = max(rc, NOT_OK)
                state = "red"
                abandoned = open_pr_for_branch(transports.gh, slug, branch)
                if abandoned is not None:
                    train_mod.abandon_round(transports.gh, slug, abandoned, reason=f"red:{record.summary or 'see log'}", ledger=ledger, train_id=train_id)
                continue
            if record.state == "green":
                # Green and unposted: `pre_post` refused it (the base moved), so the
                # verified tree no longer stands. Re-fold; never land this one.
                # This is NOT a terminal failure — the re-fold IS the remedy, and
                # the rc follows the state the train finally reached (it is raised
                # after the loop only if the rounds run out here).
                ledger.record("train-base-moved", train_id=train_id, round=round_no, pr=pr_number, detail=record.note)
                print(f"runner: RE-VERIFY — {record.note or 'green-not-posted'}: the wave is re-folded on the new base, nothing stale is landed", file=out)
                state = "base-moved"
                abandoned = open_pr_for_branch(transports.gh, slug, branch)
                if abandoned is not None:
                    train_mod.abandon_round(transports.gh, slug, abandoned, reason=record.note or "base-moved", ledger=ledger, train_id=train_id)
                wave = list(fold.folded)
                continue
            state = record.state
            print(f"runner: CANNOT-ASSESS — the train verify reached no verdict (rc {record.rc})", file=out)
            return CANNOT_ASSESS
        finally:
            held.__exit__(None, None, None)

    if state == "base-moved":
        # The rounds ran out on a base that keeps moving: every verify was
        # invalidated before it could be posted, so nothing landed and the wave is
        # still owed a stable tree. That is a refusal, and it is named.
        rc = max(rc, NOT_OK)
        last_detail = last_detail or "the base moved under every round"
    ledger.record("train-end", train_id=train_id, rc=rc, state=state, detail=last_detail)
    return rc


def cmd_train(args: argparse.Namespace) -> int:
    base = runner_dir()
    base.mkdir(parents=True, exist_ok=True)
    if args.plan and args.apply:
        print("runner: CANNOT-ASSESS — plan-with-apply: --plan executes nothing, so --apply contradicts it")
        return CANNOT_ASSESS
    lock_fd = os.open(str(base / "run.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("runner: PARKED — another runner cycle holds .fleet/runner/run.lock; no train was folded")
        return CANNOT_ASSESS
    try:
        return train_cycle(Transports.real(), base=base, apply=args.apply, plan_only=args.plan)
    finally:
        os.close(lock_fd)


def cmd_train_status(args: argparse.Namespace) -> int:
    base = runner_dir()
    ledger = Ledger(base / "ledger.jsonl")
    lines = train_mod.train_lines(ledger.rows())
    if not lines:
        print(f"runner: train-status — no train has run here ({ledger.path})")
        return OK
    print(f"runner: train-status — {len(lines)} train(s) on record")
    for line in lines:
        print(line)
    return OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runner", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="print the planned actions; nothing is executed")
    p.add_argument("--capacity", type=int, default=None, help="override the declared width (AO_RUNNER_CAPACITY)")
    p.add_argument("--fixture", help="plan from a JSON fixture instead of GitHub (offline; the gate uses this)")
    p.set_defaults(func=cmd_plan)

    r = sub.add_parser("run", help="one cycle (--once) or forever (--loop); dry-run merges unless --apply")
    mode = r.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", default=True)
    mode.add_argument("--loop", action="store_true")
    r.add_argument("--apply", action="store_true", help="really merge (default: the merge verb runs in dry-run)")
    r.add_argument("--capacity", type=int, default=None, help="override the declared width (AO_RUNNER_CAPACITY, default min(8, nproc//2)); backed off under load either way")
    r.add_argument("--interval", type=int, default=120, help="seconds between --loop cycles")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("status", help="what is verifying, what merged, what is blocked and why (from the ledger)")
    s.set_defaults(func=cmd_status)

    h = sub.add_parser("hold", help="keep a PR out of verify and merge")
    h.add_argument("pr", type=int)
    h.add_argument("--reason", required=True)
    h.set_defaults(func=cmd_hold)

    u = sub.add_parser("unhold", help="release a held PR")
    u.add_argument("pr", type=int)
    u.set_defaults(func=cmd_unhold)

    t = sub.add_parser("train", help="the merge train: fold the open MERGEABLE wave, verify ONCE, attribute a red, land (--apply)")
    t.add_argument("--apply", action="store_true", help="really land the fold (default: the guarded verb runs in its own dry-run)")
    t.add_argument("--plan", action="store_true", help="fold locally and report; push, verify, post and land NOTHING (safe against the live board)")
    t.set_defaults(func=cmd_train)

    ts = sub.add_parser("train-status", help="what the trains folded, refused, attributed and landed (from the ledger)")
    ts.set_defaults(func=cmd_train_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
