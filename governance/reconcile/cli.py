#!/usr/bin/env python3
"""Reconcile command line — stamp sessions, sweep orphans, run the worker (#304).

    python3 governance/reconcile/cli.py stamp --session <id> --issue <n> --agent <a> --pid <pid>
    python3 governance/reconcile/cli.py status
    python3 governance/reconcile/cli.py status --disk                 # report-only (#628)
    python3 governance/reconcile/cli.py sweep --ttl-minutes 15            # dry run
    python3 governance/reconcile/cli.py sweep --ttl-minutes 15 --apply    # reconcile
    python3 governance/reconcile/cli.py watch --interval-seconds 60 --apply

`sweep` is the reconciliation worker; `watch` is the same pass as a daemon, for
the repo's cron-owned ops (code-native automation — no GitHub Actions, GR-15).

`status --disk` is the read-only half (issue #628): it adds the worktree/branch
audit to the status report — every `git worktree list` entry and every local
`issue-*` branch that no session beat, claim record or landing record explains,
reported by name — and removes nothing. It is an option on the existing `status`
verb rather than a verb of its own on purpose: a new CLI verb is a surface change
that the control-plane verb registry gates (`control-plane/control/verbs.yaml`,
contract-first), and that contract belongs to its own lane. The audit is a
read-only addition to a report that already exists, so it is declared as one.

Exit-code contract (repo tri-state convention): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
`status` and `sweep` exit 1 when an orphan is present and nothing was applied —
so a cron tick or a gate can tell "the fleet is clean" from "someone left a lane
behind" without parsing prose.

`status` exits 2 — never 0 — when `--disk` was asked for and the disk could not
be read (an unreadable `git`, a corrupt beat, claim record or journal). The defect
the audit fixes was a *wrong OK*: "nothing is there" and "I could not look" must
not share a verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "fleet") not in sys.path:
    sys.path.insert(0, str(ROOT / "fleet"))

import lease  # noqa: E402  (#977 — single-writer lease around `watch --once`)

from governance.lifecycle.report import (  # noqa: E402
    DEDUPED,
    FILED,
    BoardReporter,
    GhFiler,
)
from governance.reconcile.audit import audit, describe as describe_audit  # noqa: E402
from governance.reconcile.findings import GhCloser, recheck_findings  # noqa: E402
from governance.reconcile.live import describe as describe_live, project as project_live  # noqa: E402
from governance.reconcile.heartbeat import (  # noqa: E402
    DEFAULT_BEAT_SECONDS,
    DEFAULT_TTL_MINUTES,
    ORPHAN,
    SHELVED,
    clear,
    judge,
    list_sessions,
    read,
    stamp,
)
from governance.reconcile.sweep import (  # noqa: E402
    FAILED_OUTCOME,
    RepoOps,
    SHELVED_OUTCOME,
    describe,
    sweep,
)

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: The reconcile rung's single-writer lease (#977, issue #706 D5) — two
#: replicas of the fleet-cron pair must never sweep/reconcile orphans at the
#: same time (double-reclaiming a lane or double-releasing a claim is the
#: race this closes). `AO_FLEET_LOCK_BACKEND` (default `fcntl`, GR-28).
RECONCILE_LEASE_JOB = "reconcile"
RECONCILE_LEASE_TTL_SECONDS = 900.0  # 2x reconcile's own nominal execution budget (450s)


def _reporter(root: str) -> BoardReporter:
    """The board-reporting seam for this repo (issue #321), dry-run gated by
    the command's own ``--apply`` flag."""
    return BoardReporter(GhFiler(), ledger=Path(root) / ".fleet" / "board-reports.json")


def _print_board_reports(reports: list) -> None:
    for board_report in reports:
        if board_report.action == FILED:
            print(f"board: filed #{board_report.number} for {board_report.key}")
        elif board_report.action == DEDUPED:
            print(f"board: already filed (#{board_report.number}) for {board_report.key}")
        else:
            print(f"board: dry-run — would file for {board_report.key}")


def _recheck(args: argparse.Namespace):
    """The finding-recheck seam (#973): re-measure every *filed* finding.

    A finding is re-evaluated every pass exactly as a lane is, so a pass is where
    both halves of the rule live: the lane half in `sweep`'s sessions, the finding
    half here. It reads the board only when the ledger actually holds a lifecycle
    finding, so a repository with none (a scratch fixture, a fresh install) pays a
    local file read and nothing else.
    """
    return lambda reporter: recheck_findings(
        reporter, root=args.root, apply=args.apply, closer=GhCloser()
    )


def _print_finding_states(states: list) -> None:
    """Print every re-measured finding, whatever its outcome.

    Only a resolution is a board write, but an unresolved outcome is the reason
    to look: a `still-owed` or `unmeasured` finding must be as visible as a
    retired one, or the pass reads as clean because it printed nothing.
    """
    for state in states:
        print(f"finding: {state.outcome:<14} {state.key} — {state.detail}")


def cmd_stamp(args: argparse.Namespace) -> int:
    session = stamp(
        args.session,
        issue=args.issue,
        agent=args.agent,
        root=args.root,
        lane=args.lane,
        worktree=args.worktree,
        branch=args.branch,
        pid=args.pid,
        note=args.note,
    )
    print(json.dumps(session.to_json(), indent=2))
    return EXIT_OK


def cmd_clear(args: argparse.Namespace) -> int:
    removed = clear(args.session, args.root)
    print(f"clear: {'removed' if removed else 'no heartbeat for'} {args.session}")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    if getattr(args, "prune_stale", False):
        return _cmd_status_prune_stale(args)
    sessions = list_sessions(args.root)
    at = time.time()
    rows = []
    for session in sessions:
        verdict = judge(session, args.ttl_minutes, at=at)
        rows.append(
            {
                "session_id": session.session_id,
                "issue": session.issue,
                "agent": session.agent,
                "lane": session.lane,
                "branch": session.branch,
                "state": session.state,
                "pid": session.pid,
                "at": session.at,
                "age_seconds": int(at - session.at),
                "status": verdict.status,
                "reason": verdict.reason,
            }
        )
    disk = audit(args.root, ops=RepoOps(args.root)) if args.disk else None
    live_rows = project_live(args.root) if getattr(args, "live", False) else None
    orphans = [row for row in rows if row["status"] == ORPHAN]
    summary = f"reconcile-status: {len(rows)} session(s), {len(orphans)} orphan(s)"
    if args.json:
        # Measured while adding --disk: this summary line used to follow the JSON
        # document on stdout, so `status --json` was not parseable as JSON at all.
        # stdout is now the document alone; the human line goes to stderr and the
        # counts are in the payload, so no information is lost either way.
        payload: dict = {"sessions": rows, "session_count": len(rows), "orphan_count": len(orphans)}
        if disk is not None:
            payload["disk"] = disk.to_json()
        if live_rows is not None:
            payload["live"] = [row.to_json() for row in live_rows]
        print(json.dumps(payload, indent=2))
        print(summary, file=sys.stderr)
    else:
        for row in rows:
            print(f"  {row['status']:<8} #{row['issue']:<5} {row['session_id']} {row['agent']} ({row['age_seconds']}s) {row['reason']}")
        if disk is not None:
            print(describe_audit(disk))
        if live_rows is not None:
            print(describe_live(live_rows))
        print(summary)
    if disk is not None:
        # The disk audit reports; it never removes. Its refusal is named here in
        # the same words the audit uses, and its verdict is folded into the exit
        # code so a cron tick can act on it — including CANNOT-ASSESS, which must
        # never be mistaken for a clean fleet.
        if not disk.assessable:
            print(f"reconcile-status: CANNOT-ASSESS — the disk could not be audited: {disk.reason}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        for item in disk.unmatched:
            print(f"reconcile-audit: unmatched {item.artifact}", file=sys.stderr)
        if disk.unmatched:
            print(
                f"reconcile-audit: NOT-OK — {len(disk.unmatched)} artifact(s) no session beat, "
                "claim record or landing record explains (reported, not removed)",
                file=sys.stderr,
            )
    drifted = [row for row in (live_rows or []) if row.match != "matched"]
    if live_rows is not None and drifted:
        for row in drifted:
            print(f"reconcile-live: drift {row.session_id} — {row.detail}", file=sys.stderr)
        print(
            f"reconcile-live: NOT-OK — {len(drifted)} session(s) whose heartbeat and the real "
            "disk disagree",
            file=sys.stderr,
        )
    if orphans or (disk is not None and disk.unmatched) or drifted:
        return EXIT_NOT_OK
    return EXIT_OK


def _cmd_status_prune_stale(args: argparse.Namespace) -> int:
    """``status --disk --prune-stale`` — rewrite the real-tree baseline,
    dropping only the entries the audit itself just reported as no-longer-
    unmatched (§2 of the second #740 follow-up). One reviewed command instead
    of a hand-edit; it can never drop a still-live artifact, and it never
    touches ``new_violations`` or ``young``. Not a new registered verb — a
    flag on the existing ``status`` command, same as ``--disk`` itself.
    """
    from governance.reconcile.real_tree_baseline import check_real_tree, prune_stale

    baseline_path = args.real_tree_baseline
    verdict = check_real_tree(args.root, baseline_path)
    print(verdict.describe())
    if not verdict.assessable:
        print("reconcile-status: CANNOT-ASSESS — the real tree could not be audited", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    removed = prune_stale(baseline_path, verdict)
    if removed:
        print(f"real-tree-baseline: pruned {removed} stale entr{'y' if removed == 1 else 'ies'} from {baseline_path}")
    else:
        print("real-tree-baseline: nothing to prune")
    return EXIT_OK if verdict.ok else EXIT_NOT_OK


def cmd_sweep(args: argparse.Namespace) -> int:
    report = sweep(
        args.root,
        ttl_minutes=args.ttl_minutes,
        apply=args.apply,
        ops=RepoOps(args.root),
        reporter=_reporter(args.root),
        recheck=_recheck(args),
    )
    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        print(describe(report))
        _print_board_reports(report.board_reports)
        _print_finding_states(report.finding_states)
    if report.failed:
        print(f"reconcile: NOT-OK — {len(report.failed)} session(s) could not be reconciled", file=sys.stderr)
        return EXIT_NOT_OK
    if report.finding_failures:
        print(
            f"reconcile: NOT-OK — {len(report.finding_failures)} filed finding(s) could not be resolved",
            file=sys.stderr,
        )
        return EXIT_NOT_OK
    if report.shelved:
        print(f"reconcile: {len(report.shelved)} lane(s) shelved (unmerged work kept)", file=sys.stderr)
    if not args.apply and (report.reclaimed or report.parked or report.shelved):
        print("reconcile: NOT-OK — orphaned session(s) present; re-run with --apply", file=sys.stderr)
        return EXIT_NOT_OK
    print("reconcile: OK")
    return EXIT_OK


def cmd_watch(args: argparse.Namespace) -> int:
    """The reconciliation worker: one pass per interval, forever.

    A pass that raises does not end the worker — a reconcile daemon that dies on
    the first unreadable heartbeat is a daemon that stops cleaning up at exactly
    the moment the workspace got messy.
    """
    print(f"reconcile: watching every {args.interval_seconds:g}s (ttl {args.ttl_minutes:g}m, "
          f"{'apply' if args.apply else 'dry-run'})", flush=True)
    passes = 0
    while True:
        passes += 1
        # #977 (issue #706 D5): acquire the single-writer lease BEFORE a pass
        # that can WRITE does any work — two replicas racing to apply the
        # same reclaim is the defect the lease exists for. A dry-run pass
        # (`--apply` not given) never writes, so it must not need the lease
        # either: `fcntl_try_lock` (fleet/lease.py) does `os.open(...,
        # O_CREAT)` on the lease file unconditionally, which raises
        # `OSError: [Errno 30] Read-only file system` the moment `.fleet` is
        # a read-only mount (measured: infra/fleet/docker-compose.agent-cron.yml's
        # dev-run harness mounts it `read_only: true` by design — the state
        # roots this dev-first surface must not touch). That raised OUTSIDE
        # this loop's own `try`, so it was never the caught-and-logged "bad
        # pass" this function's docstring promises — it killed the process
        # (rc=1) before a single pass ran, on the UNMUTATED baseline, which
        # is issue #1034's `check-fleet-cron-dev-run.sh` red. Root cause:
        # fed4e7d (#977/#978/#984) took the lease unconditionally instead of
        # only when a pass can act.
        reconcile_lease = None
        if args.apply:
            reconcile_lease = lease.make_lease(
                job=RECONCILE_LEASE_JOB,
                path=Path(args.root) / ".fleet" / f"{RECONCILE_LEASE_JOB}.lease",
                ttl_seconds=RECONCILE_LEASE_TTL_SECONDS,
            )
            if not reconcile_lease.acquire():
                record = lease.skipped_log(
                    RECONCILE_LEASE_JOB,
                    backend=lease.backend_name(),
                    detail="reconcile lease held elsewhere",
                )
                print(f"reconcile[{passes}]: {json.dumps(record, sort_keys=True)}", flush=True)
                if args.once:
                    return EXIT_OK
                time.sleep(args.interval_seconds)
                continue
        try:
            report = sweep(
                args.root,
                ttl_minutes=args.ttl_minutes,
                apply=args.apply,
                ops=RepoOps(args.root),
                reporter=_reporter(args.root),
                recheck=_recheck(args),
            )
            counts = ", ".join(
                f"{name}={len(report.by_outcome(name))}"
                for name in ("reclaimed", "parked", "shelved", "reported", "failed")
            )
            if report.reclaimed or report.parked or report.shelved or report.failed:
                print(f"reconcile[{passes}]: {counts}", flush=True)
                for action in report.actions:
                    if action.outcome != "reported":
                        print(f"    {action}", flush=True)
                _print_board_reports(report.board_reports)
            # Outside the line above on purpose: on this box the pass that matters
            # is the one that touches no session at all and still retires a stale
            # finding — a print guarded on session outcomes would hide it.
            _print_finding_states(report.finding_states)
        except Exception as exc:  # noqa: BLE001 - the worker outlives a bad pass
            print(f"reconcile[{passes}]: pass failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        finally:
            if reconcile_lease is not None:
                reconcile_lease.release()
        if args.once:
            return EXIT_OK
        time.sleep(args.interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reconcile", description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(ROOT), help="the repository to reconcile")
    sub = parser.add_subparsers(dest="command", required=True)

    stamp_cmd = sub.add_parser("stamp", help="write or refresh a session heartbeat")
    stamp_cmd.add_argument("--session", required=True)
    stamp_cmd.add_argument("--issue", type=int, required=True)
    stamp_cmd.add_argument("--agent", required=True)
    stamp_cmd.add_argument("--lane", default="")
    stamp_cmd.add_argument("--worktree", default="")
    stamp_cmd.add_argument("--branch", default="")
    stamp_cmd.add_argument("--pid", type=int, default=None)
    stamp_cmd.add_argument("--note", default="")
    stamp_cmd.set_defaults(func=cmd_stamp)

    clear_cmd = sub.add_parser("clear", help="remove a session heartbeat")
    clear_cmd.add_argument("--session", required=True)
    clear_cmd.set_defaults(func=cmd_clear)

    status_cmd = sub.add_parser("status", help="every session and its verdict")
    status_cmd.add_argument("--ttl-minutes", type=float, default=DEFAULT_TTL_MINUTES)
    status_cmd.add_argument("--json", action="store_true")
    status_cmd.add_argument(
        "--disk",
        action="store_true",
        help="also audit the disk: report every worktree/branch no record explains (#628)",
    )
    status_cmd.add_argument(
        "--live",
        action="store_true",
        help=(
            "also project every session's heartbeat against the real disk and flag "
            "drift (#885) — a session whose recorded worktree is gone"
        ),
    )
    status_cmd.add_argument(
        "--real-tree-baseline",
        default=str(ROOT / "governance" / "reconcile" / "real-tree-baseline.json"),
        help="the reviewed baseline the real-tree audit is checked against (#740)",
    )
    status_cmd.add_argument(
        "--prune-stale",
        action="store_true",
        help=(
            "rewrite --real-tree-baseline, dropping entries the audit no longer reports "
            "unmatched (#740 follow-up) — the reap as one reviewed command"
        ),
    )
    status_cmd.set_defaults(func=cmd_status)

    sweep_cmd = sub.add_parser("sweep", help="one reconciliation pass")
    sweep_cmd.add_argument("--ttl-minutes", type=float, default=DEFAULT_TTL_MINUTES)
    sweep_cmd.add_argument("--apply", action="store_true", help="act instead of planning")
    sweep_cmd.add_argument("--json", action="store_true")
    sweep_cmd.set_defaults(func=cmd_sweep)

    watch_cmd = sub.add_parser("watch", help="the reconciliation worker (daemon)")
    watch_cmd.add_argument("--ttl-minutes", type=float, default=DEFAULT_TTL_MINUTES)
    watch_cmd.add_argument("--interval-seconds", type=float, default=DEFAULT_BEAT_SECONDS)
    watch_cmd.add_argument("--apply", action="store_true")
    watch_cmd.add_argument("--once", action="store_true", help="one pass, then exit (cron tick)")
    watch_cmd.set_defaults(func=cmd_watch)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"{args.command}: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS


if __name__ == "__main__":
    raise SystemExit(main())
