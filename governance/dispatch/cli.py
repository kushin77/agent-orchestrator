#!/usr/bin/env python3
"""Claim-time issue-order enforcement — command line (issue #157, hardened #170).

Exit codes follow the repo's tri-state convention (guardrails/honesty):

* ``0`` — OK
* ``1`` — NOT-OK (a claim was refused, or the audit found a violation)
* ``2`` — CANNOT-ASSESS (no snapshot, a stale snapshot, or no ledger to audit
  against) — the snapshot's age is printed on every ``status``/``eligible``/
  ``claim`` run, and a snapshot past ``--stale-minutes`` is refused with
  ``snapshot-stale`` instead of a verdict.

Typical agent flow::

    python3 governance/dispatch/cli.py eligible --issue 157 --agent me   # check first
    python3 governance/dispatch/cli.py claim --issue 157 --agent me --lane governance
    ...do the work, open the PR...
    python3 governance/dispatch/cli.py release --issue 157 --agent me

The gate runs ``audit``, which always includes the self-control mutants: if the
audit cannot fail, ``audit`` fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import claims  # noqa: E402
import order  # noqa: E402
import snapshot as snapshot_mod  # noqa: E402

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2


def _load_snapshot(path: Path) -> snapshot_mod.Snapshot:
    return snapshot_mod.load(path)


def cmd_audit(args: argparse.Namespace) -> int:
    snapshot_path = Path(args.snapshot)
    ledger_path = Path(args.ledger)
    if not snapshot_path.exists():
        print(
            f"audit: CANNOT-ASSESS — {snapshot_path} is missing "
            "(refresh it with: python3 governance/dispatch/cli.py snapshot --from-github)",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS
    try:
        snapshot = _load_snapshot(snapshot_path)
    except ValueError as exc:
        print(f"audit: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    problems = claims.self_control()
    problems.extend(claims.audit_ledger(ledger_path, snapshot))

    if problems:
        print(f"issue-claims: FAIL ({len(problems)} problem(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_NOT_OK
    print("issue-claims: OK (no violations; self-control mutants all rejected)")
    return EXIT_OK


def cmd_eligible(args: argparse.Namespace) -> int:
    snapshot_path = Path(args.snapshot)
    if not snapshot_path.exists():
        print(f"eligible: CANNOT-ASSESS — {snapshot_path} is missing", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    snapshot = _load_snapshot(snapshot_path)
    age = snapshot_mod.age_minutes(snapshot)
    print(f"eligible: snapshot age {age:.1f}m (threshold {args.stale_minutes}m)", file=sys.stderr)
    if snapshot_mod.is_stale(snapshot, args.stale_minutes):
        print(
            f"eligible: CANNOT-ASSESS — snapshot-stale ({age:.1f}m > {args.stale_minutes}m) "
            "— refresh first: python3 governance/dispatch/cli.py snapshot --from-github",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS
    events = claims.read_ledger(args.ledger)
    live = claims.active_claims(events)
    held_by_self = frozenset(number for number, claim in live.items() if claim.agent == args.agent)
    history = frozenset(event.issue for event in events if event.is_claim and event.agent == args.agent)
    others = frozenset(number for number, claim in live.items() if claim.agent != args.agent)
    verdict = order.eligible(
        snapshot,
        args.issue,
        active_claims=held_by_self,
        agent_history=history,
        claimed_by_others=others,
    )
    print(json.dumps(verdict.to_json(), indent=2))
    return EXIT_OK if verdict.eligible else EXIT_NOT_OK


def cmd_claim(args: argparse.Namespace) -> int:
    snapshot_path = Path(args.snapshot)
    if not snapshot_path.exists():
        print(f"claim: CANNOT-ASSESS — {snapshot_path} is missing", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    snapshot = _load_snapshot(snapshot_path)
    age = snapshot_mod.age_minutes(snapshot)
    print(f"claim: snapshot age {age:.1f}m (threshold {args.stale_minutes}m)", file=sys.stderr)
    digest = snapshot_mod.content_sha256(snapshot_path)
    try:
        event = claims.claim(
            args.issue,
            args.agent,
            args.lane,
            snapshot,
            ledger=args.ledger,
            lock_dir=args.locks,
            base_commit=args.base_commit,
            snapshot_sha256=digest,
            ttl_hours=args.ttl_hours,
            directive_id=args.directive,
            stale_minutes=args.stale_minutes,
        )
    except claims.ClaimRefused as exc:
        print(f"claim REFUSED: {exc.reason} — {exc.detail}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS if exc.reason == "snapshot-stale" else EXIT_NOT_OK
    print(json.dumps(event.to_json(), indent=2))
    print(f"claim accepted: #{event.issue} as {event.agent} ({event.reason})")
    return EXIT_OK


def cmd_release(args: argparse.Namespace) -> int:
    try:
        event = claims.release(args.issue, args.agent, ledger=args.ledger, lock_dir=args.locks)
    except claims.ClaimRefused as exc:
        print(f"release REFUSED: {exc.reason} — {exc.detail}", file=sys.stderr)
        return EXIT_NOT_OK
    print(json.dumps(event.to_json(), indent=2))
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    snapshot_path = Path(args.snapshot)
    if not snapshot_path.exists():
        print(f"status: CANNOT-ASSESS — {snapshot_path} is missing", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    snapshot = _load_snapshot(snapshot_path)
    age = snapshot_mod.age_minutes(snapshot)
    print(f"snapshot: {snapshot.source} generated {snapshot.generated_at} ({len(snapshot.issues)} issues)")
    print(f"snapshot age: {age:.1f}m (threshold {args.stale_minutes}m)")
    if snapshot_mod.is_stale(snapshot, args.stale_minutes):
        print(
            f"status: CANNOT-ASSESS — snapshot-stale ({age:.1f}m > {args.stale_minutes}m) "
            "— refresh first: python3 governance/dispatch/cli.py snapshot --from-github",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS
    events = claims.read_ledger(args.ledger)
    live = claims.active_claims(events)
    milestone = order.active_milestone(snapshot, frozenset())
    frontier = order.frontier(snapshot, milestone) if milestone else None
    print(f"active milestone: {milestone or '<none>'}")
    print(f"frontier: #{frontier.number} {frontier.title}" if frontier else "frontier: <none>")
    print(f"live claims: {len(live)}")
    for issue, claim in sorted(live.items()):
        print(f"  #{issue} held by {claim.agent} ({claim.lane}) since {claim.at} reason={claim.reason}")
    return EXIT_OK


def cmd_reap(args: argparse.Namespace) -> int:
    """Recover claims wedged by dead agents so their issues can be dispatched again."""
    reaped = claims.reap(
        args.older_than_minutes,
        ledger=args.ledger,
        lock_dir=args.locks,
        issue=args.issue,
        reaper=args.reaper,
    )
    if not reaped:
        print("reap: nothing to reap (no live claim older than the threshold)")
        return EXIT_OK
    for event in reaped:
        print(f"reap: #{event.issue} released from {event.reaped_agent} (held since {event.lane or 'n/a'})")
    print(f"reap: {len(reaped)} claim(s) recovered")
    return EXIT_OK


def cmd_held(args: argparse.Namespace) -> int:
    """Exit 0 and print the holder when the issue has a live claim; exit 1 when it does not."""
    live = claims.active_claims(claims.read_ledger(args.ledger))
    holder = live.get(args.issue)
    if holder is None:
        print(json.dumps({"issue": args.issue, "agent": None}))
        return EXIT_NOT_OK
    print(json.dumps({"issue": args.issue, "agent": holder.agent, "at": holder.at, "reason": holder.reason}))
    return EXIT_OK


def cmd_snapshot(args: argparse.Namespace) -> int:
    if not args.from_github:
        print("snapshot: pass --from-github (this is the only network-touching path)", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    try:
        records = snapshot_mod.github_records(args.repo)
    except RuntimeError as exc:
        print(f"snapshot: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    built = snapshot_mod.build_snapshot(records, source=args.repo)
    target = snapshot_mod.save(built, args.out)
    edged = [issue for issue in built.issues.values() if issue.parent or issue.blocked_by]
    print(f"snapshot: wrote {target} ({len(built.issues)} issues, {len(edged)} with declared chain edges)")
    return EXIT_OK


def add_paths(parser: argparse.ArgumentParser) -> None:
    """Board artifacts every subcommand reads (after the subcommand, e.g. `audit --ledger x`)."""
    parser.add_argument("--snapshot", default=str(snapshot_mod.DEFAULT_PATH))
    parser.add_argument("--ledger", default=str(claims.DEFAULT_CLAIMS_DIR))
    parser.add_argument("--locks", default=str(claims.DEFAULT_LOCK_DIR))
    parser.add_argument("--stale-minutes", type=int, default=snapshot_mod.DEFAULT_STALENESS_MINUTES)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dispatch", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="audit the ledger + run the self-control mutants")
    add_paths(audit)
    audit.set_defaults(func=cmd_audit)

    eligible = sub.add_parser("eligible", help="is this issue the next step for this agent?")
    add_paths(eligible)
    eligible.add_argument("--issue", type=int, required=True)
    eligible.add_argument("--agent", default="agent")
    eligible.set_defaults(func=cmd_eligible)

    claim = sub.add_parser("claim", help="claim an issue (refuses out-of-order claims)")
    add_paths(claim)
    claim.add_argument("--issue", type=int, required=True)
    claim.add_argument("--agent", required=True)
    claim.add_argument("--lane", default="")
    claim.add_argument("--ttl-hours", type=int, default=claims.DEFAULT_TTL_HOURS)
    claim.add_argument("--base-commit", default="")
    claim.add_argument("--directive", default="", help="brain directive id authorizing this claim")
    claim.set_defaults(func=cmd_claim)

    release = sub.add_parser("release", help="release a claim")
    add_paths(release)
    release.add_argument("--issue", type=int, required=True)
    release.add_argument("--agent", required=True)
    release.set_defaults(func=cmd_release)

    status = sub.add_parser("status", help="show the active milestone, frontier and live claims")
    add_paths(status)
    status.set_defaults(func=cmd_status)

    held = sub.add_parser("held", help="print the live claim holder of an issue (exit 0 = held, 1 = free)")
    add_paths(held)
    held.add_argument("--issue", type=int, required=True)
    held.set_defaults(func=cmd_held)

    reap = sub.add_parser("reap", help="release claims wedged by dead agents past a threshold")
    add_paths(reap)
    reap.add_argument("--older-than-minutes", type=int, default=claims.DEFAULT_REAP_MINUTES)
    reap.add_argument("--issue", type=int, default=None)
    reap.add_argument("--reaper", default="brain")
    reap.set_defaults(func=cmd_reap)

    snap = sub.add_parser("snapshot", help="refresh .board/snapshot.json from GitHub")
    snap.add_argument("--from-github", action="store_true")
    snap.add_argument("--repo", default="kushin77/agent-orchestrator")
    snap.add_argument("--out", default=str(snapshot_mod.DEFAULT_PATH))
    snap.set_defaults(func=cmd_snapshot)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
