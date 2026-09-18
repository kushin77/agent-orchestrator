#!/usr/bin/env python3
"""cli.py — the PR runner's entrypoint (issue #1343, parent #1295).

    python3 fleet/runner/cli.py plan                 # the actions, nothing done
    python3 fleet/runner/cli.py run --once [--apply] # one cycle (dry-run by default)
    python3 fleet/runner/cli.py run --loop [--apply] [--interval 120]
    python3 fleet/runner/cli.py status               # what is verifying / merged / blocked and why
    python3 fleet/runner/cli.py hold <pr> --reason <text>
    python3 fleet/runner/cli.py unhold <pr>

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
  5. `plan()` (pure), printed and recorded.
  6. execute: cancel stale builds, verify up to `--capacity` heads in parallel
     (each in its own held worktree), then merge the greens through the
     merged-tree seam and `scripts/merge-pr.sh` — stopping on a non-compliant
     landed tip.

Every step writes `.fleet/runner/ledger.jsonl` (lesson 9); `status` answers
from it. Dry-run by default: `--apply` is what merges (the verify + post
half is always real: a verify writes nothing to the repository but a status).

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
    REFUSE,
    VERIFY,
    Action,
    LiveBuild,
    OpenPR,
    PruneResult,
)
from fleet.runner.plan import DEFAULT_CAPACITY, explain, plan  # noqa: E402
from fleet.runner import merge as merge_mod  # noqa: E402
from fleet.runner import verify as verify_mod  # noqa: E402
from fleet.runner.verify import Command, Ledger, Result  # noqa: E402

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2

ROLE_ENV = "AO_RUNNER_HOST_ROLE"
PRIMARY = "primary"
REPO_SLUG = "kushin77/agent-orchestrator"


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
        status = gh(["api", f"repos/{REPO_SLUG}/commits/{pr.head_sha}/status"])
        if status.ok:
            try:
                for record in ev.from_commit_status(pr.number, pr.head_sha, json.loads(status.out or "{}"), context=GATE_CONTEXT):
                    table.add(record)
            except ValueError:
                pass
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
    capacity: int = DEFAULT_CAPACITY,
    execute: bool = True,
    out=sys.stdout,
) -> int:
    """One full cycle. Returns the tri-state rc."""
    base.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(base / "ledger.jsonl")
    ledger.record("cycle-start", apply=apply, execute=execute, role=transports.env.get(ROLE_ENV, ""))

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
        if "post" in events:
            p = events["post"]
            bits.append(f"posted={'ok' if p.get('ok') else 'FAILED'} rc={p.get('rc')}")
        if "await" in events:
            bits.append(f"awaiting:{events['await'].get('reason')}")
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
            "evidence": [{pr, sha, source, state, base_tip?}],
            "builds": [{id, pr, sha, status, kind?}], "holds": {"<pr>": reason},
            "prune": {"ok": bool, "detail": str}, "capacity": int}
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    prs = [OpenPR(int(r["number"]), str(r["head_sha"]), mergeable=str(r.get("mergeable", "MERGEABLE")), draft=bool(r.get("draft", False))) for r in data.get("prs", [])]
    table = ev.EvidenceTable(
        [ev.Evidence(int(r["pr"]), str(r["sha"]), str(r["source"]), str(r["state"]), base_tip=r.get("base_tip")) for r in data.get("evidence", [])]
    )
    builds = [LiveBuild(str(r["id"]), int(r["pr"]), str(r["sha"]), str(r["status"]), kind=str(r.get("kind", "cloud-build"))) for r in data.get("builds", [])]
    holds = {int(k): str(v) for k, v in (data.get("holds") or {}).items()}
    prune_data = data.get("prune") or {"ok": True, "detail": "fixture"}
    prune = PruneResult(bool(prune_data.get("ok", True)), str(prune_data.get("detail", "")))
    return plan(prs, table, builds, holds, prune, data.get("master_tip"), int(data.get("capacity", capacity)))


def cmd_plan(args: argparse.Namespace) -> int:
    if args.fixture:
        for line in explain(plan_from_fixture(Path(args.fixture), args.capacity)):
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runner", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="print the planned actions; nothing is executed")
    p.add_argument("--capacity", type=int, default=DEFAULT_CAPACITY)
    p.add_argument("--fixture", help="plan from a JSON fixture instead of GitHub (offline; the gate uses this)")
    p.set_defaults(func=cmd_plan)

    r = sub.add_parser("run", help="one cycle (--once) or forever (--loop); dry-run merges unless --apply")
    mode = r.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", default=True)
    mode.add_argument("--loop", action="store_true")
    r.add_argument("--apply", action="store_true", help="really merge (default: the merge verb runs in dry-run)")
    r.add_argument("--capacity", type=int, default=DEFAULT_CAPACITY, help="parallel verifies (default 3)")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
