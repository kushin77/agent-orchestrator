#!/usr/bin/env python3
"""Reconcile command line — stamp sessions, sweep orphans, run the worker (#304).

    python3 governance/reconcile/cli.py stamp --session <id> --issue <n> --agent <a> --pid <pid>
    python3 governance/reconcile/cli.py status
    python3 governance/reconcile/cli.py sweep --ttl-minutes 15            # dry run
    python3 governance/reconcile/cli.py sweep --ttl-minutes 15 --apply    # reconcile
    python3 governance/reconcile/cli.py watch --interval-seconds 60 --apply

`sweep` is the reconciliation worker; `watch` is the same pass as a daemon, for
the repo's cron-owned ops (code-native automation — no GitHub Actions, GR-15).

Exit-code contract (repo tri-state convention): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
`status` and `sweep` exit 1 when an orphan is present and nothing was applied —
so a cron tick or a gate can tell "the fleet is clean" from "someone left a lane
behind" without parsing prose.
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
    if args.json:
        print(json.dumps({"sessions": rows}, indent=2))
    else:
        for row in rows:
            print(f"  {row['status']:<8} #{row['issue']:<5} {row['session_id']} {row['agent']} ({row['age_seconds']}s) {row['reason']}")
    orphans = [row for row in rows if row["status"] == ORPHAN]
    print(f"reconcile-status: {len(rows)} session(s), {len(orphans)} orphan(s)")
    return EXIT_NOT_OK if orphans else EXIT_OK


def cmd_sweep(args: argparse.Namespace) -> int:
    report = sweep(
        args.root,
        ttl_minutes=args.ttl_minutes,
        apply=args.apply,
        ops=RepoOps(args.root),
    )
    if args.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        print(describe(report))
    if report.failed:
        print(f"reconcile: NOT-OK — {len(report.failed)} session(s) could not be reconciled", file=sys.stderr)
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
        try:
            report = sweep(
                args.root,
                ttl_minutes=args.ttl_minutes,
                apply=args.apply,
                ops=RepoOps(args.root),
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
        except Exception as exc:  # noqa: BLE001 - the worker outlives a bad pass
            print(f"reconcile[{passes}]: pass failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
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
