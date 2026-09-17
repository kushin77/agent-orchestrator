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
    python3 governance/dispatch/cli.py dispatch --issue 157 --agent me --lane governance
    python3 governance/dispatch/cli.py claim --issue 157 --agent me --lane governance
    ...do the work, open the PR...
    python3 governance/dispatch/cli.py release --issue 157 --agent me

``dispatch`` is the A2A arbitration seam (issue #726): it is read-only and proves
issue -> epic -> lane ownership — the issue is open, its epic is open, the lane
owns it, no other lane already holds it — naming the evidence it checked. ``claim``
runs the same arbitration before it writes, so the refusal cannot be sidestepped
by calling the mutation directly.

The gate runs ``audit``, which always includes the self-control mutants (ledger
and A2A dispatch alike): if the audit cannot fail, ``audit`` fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import claims  # noqa: E402
import focus as focus_mod  # noqa: E402
import order  # noqa: E402
import pool as pool_mod  # noqa: E402
import owner_queue as queue_mod  # noqa: E402
import snapshot as snapshot_mod  # noqa: E402
from model import parse_file_claims  # noqa: E402

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: The capacity gate lives with the loop it bounds (``fleet/capacity.py``, #718).
#: Imported LAZILY and by path: `focus` is the verb that reports the fan-out, but
#: no other dispatch verb should fail to start because the loop's module moved.
FLEET_DIR = Path(__file__).resolve().parents[2] / "fleet"


def _capacity_module():
    """Import ``fleet/capacity.py``; raise ``ImportError`` when it is missing."""
    if str(FLEET_DIR) not in sys.path:
        sys.path.insert(0, str(FLEET_DIR))
    import capacity  # noqa: PLC0415 — deliberate: lazy, so a missing module cannot
    # break the verbs that do not report the fan-out.

    return capacity


def _load_snapshot(path: Path) -> snapshot_mod.Snapshot:
    """Load the board snapshot. ``snapshot_mod.load`` overlays the owner's
    committed queue (#928) by default — this wrapper exists only so every
    verb reads the board through one seam.
    """
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
    # The arbitration controls run here too: the ledger audit and the dispatch
    # refusals are both gates of record, so either one going quiet fails the gate.
    problems.extend(claims.arbitration_self_control())
    problems.extend(claims.audit_ledger(ledger_path, snapshot))

    if problems:
        print(f"issue-claims: FAIL ({len(problems)} problem(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_NOT_OK
    print(
        "issue-claims: OK (no violations; self-control mutants all rejected; "
        "A2A dispatch refusals all provoked)"
    )
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
        files = parse_file_claims(json.loads(args.files), where="--files") if args.files else ()
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"claim: CANNOT-ASSESS — --files is not valid: {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
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
            files=files,
        )
    except claims.ClaimRefused as exc:
        print(f"claim REFUSED: {exc.reason} — {exc.detail}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS if exc.reason == claims.REASON_SNAPSHOT_STALE else EXIT_NOT_OK
    print(json.dumps(event.to_json(), indent=2))
    print(f"claim accepted: #{event.issue} as {event.agent} ({event.reason})")
    return EXIT_OK


def cmd_dispatch(args: argparse.Namespace) -> int:
    """Arbitrate a directive: prove issue -> epic -> lane ownership before routing.

    Read-only: it takes no claim and writes no ledger entry. A refusal names the
    evidence checked, so the caller learns *why* the unit is unowned rather than
    discovering it when the work is already half-done.
    """
    snapshot_path = Path(args.snapshot)
    if not snapshot_path.exists():
        print(
            f"dispatch: CANNOT-ASSESS — {snapshot_path} is missing "
            "(refresh it with: python3 governance/dispatch/cli.py snapshot --from-github)",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS
    try:
        snapshot = _load_snapshot(snapshot_path)
    except ValueError as exc:
        print(f"dispatch: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    age = snapshot_mod.age_minutes(snapshot)
    print(f"dispatch: snapshot age {age:.1f}m (threshold {args.stale_minutes}m)", file=sys.stderr)
    try:
        arbitration = claims.arbitrate(
            args.issue,
            args.agent,
            args.lane,
            snapshot,
            ledger=args.ledger,
            lock_dir=args.locks,
            directive_id=args.directive,
            require_lane=True,
            snapshot_path=str(snapshot_path),
            snapshot_sha256=snapshot_mod.content_sha256(snapshot_path),
            stale_minutes=args.stale_minutes,
        )
    except claims.ClaimRefused as exc:
        print(f"dispatch REFUSED: {exc.reason} — {exc.detail}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS if exc.reason == claims.REASON_SNAPSHOT_STALE else EXIT_NOT_OK
    print(json.dumps(arbitration.to_json(), indent=2))
    print(
        f"dispatch granted: #{arbitration.issue} -> epic {arbitration.epic or '<none>'} "
        f"-> lane {arbitration.lane}"
    )
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


def cmd_focus(args: argparse.Namespace) -> int:
    """Print the active epic, its open children and the pooled set (epic #707, F1).

    With ``--self-control`` it first runs the resolver/schema mutants: a resolver
    that cannot fail is a formality, so the gate drives this mode.
    """
    if args.self_control:
        problems = focus_mod.self_control()
        # The capacity gate is the fan-out half of the focus contract (#718):
        # max-agents is ON and bounded only if the bounds can genuinely bind, so
        # the epic-focus check drives its mutants here rather than asserting it.
        try:
            problems.extend(_capacity_module().self_control())
        except ImportError as exc:
            problems.append(f"fleet/capacity.py is missing — the fan-out is unbounded ({exc})")
        if problems:
            print(f"focus: FAIL ({len(problems)} self-control problem(s))", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return EXIT_NOT_OK
        print("focus: OK (self-control mutants all rejected)")

    snapshot_path = Path(args.snapshot)
    if not snapshot_path.exists():
        print(f"focus: CANNOT-ASSESS — {snapshot_path} is missing", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    snapshot = _load_snapshot(snapshot_path)
    try:
        focus = focus_mod.load(args.focus)
    except focus_mod.FocusInvalid as exc:
        print(f"focus: NOT-OK — {exc}", file=sys.stderr)
        return EXIT_NOT_OK
    epic = focus_mod.active(snapshot, args.focus)
    if epic is None:
        print("active_epic: <none> — the board has no workable epic")
    else:
        print(f"active_epic: #{epic.number} {epic.title}")
    epic_number = epic.number if epic is not None else None
    children = focus_mod.open_children(snapshot, epic_number)
    listed = " ".join(f"#{issue.number}" for issue in children)
    print(f"open children ({len(children)}): {listed or '<none>'}")
    pool = focus_mod.pooled(snapshot, epic_number)
    head = " ".join(f"#{issue.number}" for issue in pool[:20])
    if len(pool) > 20:
        head += " ..."
    print(f"pooled ({len(pool)}): {head or '<none>'}")
    # The pool drains when the focus does (#707, F6). `focus` is a read-only verb,
    # so this REPORTS what a drain would take — the truncation itself happens on
    # the claim path and in `board.drain`, which are the writers.
    if epic_number is None:
        print(
            f"drain: focus is <none> — {len(pool)} pooled issue(s) would be drained "
            "(the brain calls board.drain to execute it)"
        )
    else:
        print(f"drain: none — focus #{epic_number} is active")
    if focus is not None:
        print(
            f"wave_cap: {focus.wave_cap}  max_agents: {focus.max_agents}  "
            f"activated_at: {focus.activated_at}"
        )
    # The resolved fan-out — what the loop will actually run at (#718). The
    # focus's `max_agents` above is the raw pin (0 = "the pool"); this is the
    # number the three bounds produce. Reported even with no focus, because the
    # env/pool default applies regardless of whether anything is pinned.
    try:
        print(
            _capacity_module().headline(
                focus_max_agents=focus.max_agents if focus is not None else None
            )
        )
    except ImportError as exc:
        print(f"capacity: CANNOT-ASSESS — fleet/capacity.py is missing ({exc})", file=sys.stderr)
        return EXIT_NOT_OK
    return EXIT_OK


def cmd_pool(args: argparse.Namespace) -> int:
    """Print the out-of-epic pool, or prove it can fail (epic #707, lane F6/#721).

    The pool is where an `out-of-epic-pooled` refusal parks work so it is not
    silently dropped. With ``--self-control`` it first drives ``pool.self_control``:
    a reader that skips a malformed line turns a corrupt rail into an empty one,
    so the gate requires the mutants to be rejected. With ``--snapshot`` it also
    reports what a focus of ``None`` would drain (read-only: it never truncates).
    """
    if args.self_control:
        problems = pool_mod.self_control()
        if problems:
            print(f"pool: FAIL ({len(problems)} self-control problem(s))", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return EXIT_NOT_OK
        print("pool: OK (self-control mutants all rejected)")

    try:
        records = pool_mod.read(args.pool)
    except pool_mod.PoolInvalid as exc:
        print(f"pool: NOT-OK — {exc}", file=sys.stderr)
        return EXIT_NOT_OK
    pooled = pool_mod.numbers(args.pool)
    listed = " ".join(f"#{number}" for number in pooled)
    print(f"pooled ({len(pooled)}): {listed or '<none>'}")
    for record in records:
        print(f"  #{record.issue} reason={record.reason} at={record.at}")

    if args.snapshot:
        snapshot_path = Path(args.snapshot)
        if not snapshot_path.exists():
            print(f"pool: CANNOT-ASSESS — {snapshot_path} is missing", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        snapshot = _load_snapshot(snapshot_path)
        try:
            focus = focus_mod.load(args.focus)
        except focus_mod.FocusInvalid as exc:
            print(f"pool: NOT-OK — {exc}", file=sys.stderr)
            return EXIT_NOT_OK
        epic = focus_mod.active(snapshot, args.focus)
        if epic is None:
            # Read-only: report what a drain WOULD take, never truncate here.
            print(f"drain: focus is <none> — {len(pooled)} pooled issue(s) would be drained: {listed or '<none>'}")
        else:
            print(f"drain: none — focus #{epic.number} is active")
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
    payload = {
        "issue": args.issue,
        "agent": holder.agent,
        "lane": holder.lane,
        "at": holder.at,
        "reason": holder.reason,
    }
    if holder.provenance is not None:
        payload["epic"] = holder.provenance.epic
    print(json.dumps(payload))
    return EXIT_OK


def cmd_snapshot(args: argparse.Namespace) -> int:
    if not args.from_github:
        print("snapshot: pass --from-github (this is the only network-touching path)", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    # The refresh itself lives in the trigger's own verb (`refresh_or_park`, #727),
    # so the network path and its bounded window are declared in exactly one place.
    refreshed, detail = snapshot_mod.refresh(args.out, repo=args.repo)
    if not refreshed:
        print(f"snapshot: CANNOT-ASSESS — {detail}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    built = snapshot_mod.load(args.out)
    edged = [issue for issue in built.issues.values() if issue.parent or issue.blocked_by]
    print(f"snapshot: wrote {args.out} ({len(built.issues)} issues, {len(edged)} with declared chain edges)")
    return EXIT_OK


def cmd_trigger(args: argparse.Namespace) -> int:
    """The consumer's trigger for a ``snapshot-stale`` refusal (issue #727).

    A refusal that names its own remedy must also invoke it: this performs the
    ONE bounded refresh, and when that does not clear the staleness it PARKS the
    directive in the deferred queue instead of leaving it to be re-dispatched
    every cycle. Exit 0 = the directive is dispatchable, 1 = the park holds it,
    2 = CANNOT-ASSESS (the snapshot's age could not be established at all).
    """
    trigger = snapshot_mod.refresh_or_park(
        args.directive,
        snapshot_path=Path(args.snapshot),
        base=Path(args.fleet_dir) if args.fleet_dir else None,
        repo=args.repo,
        threshold_minutes=args.stale_minutes,
    )
    print(json.dumps(trigger.to_json(), indent=2))
    print(f"trigger: {trigger.action} — {trigger.reason}")
    if not trigger.dispatchable:
        return EXIT_NOT_OK
    return EXIT_OK


def cmd_queue(args: argparse.Namespace) -> int:
    """Report/validate the owner's committed queue (#928)."""
    queue_path = Path(args.queue)
    try:
        data = queue_mod.load(queue_path)
    except queue_mod.QueueError as exc:
        print(f"queue: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    if data is None:
        print(f"queue: CANNOT-ASSESS — {queue_path} is missing", file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    if args.check:
        snapshot_path = Path(args.snapshot)
        snapshot = None
        if snapshot_path.exists():
            snapshot = snapshot_mod.load(snapshot_path)
            if snapshot_mod.is_stale(snapshot, args.stale_minutes):
                age = snapshot_mod.age_minutes(snapshot)
                print(
                    f"queue --check: CANNOT-ASSESS — snapshot-stale ({age:.1f}m > {args.stale_minutes}m); "
                    "'exists/open' checks need a fresh board — refresh first: "
                    "python3 governance/dispatch/cli.py snapshot --from-github "
                    "(structural checks — duplicates, cycles — do not need the board and still ran)",
                    file=sys.stderr,
                )
                structural = queue_mod.validate(data, snapshot=None)
                if structural:
                    print(f"queue: FAIL ({len(structural)} structural problem(s))", file=sys.stderr)
                    for problem in structural:
                        print(f"  - {problem}", file=sys.stderr)
                    return EXIT_NOT_OK
                return EXIT_CANNOT_ASSESS
        else:
            print(f"queue --check: {snapshot_path} is missing — validating structure only", file=sys.stderr)
        problems = queue_mod.validate(data, snapshot)
        if problems:
            print(f"queue: FAIL ({len(problems)} problem(s))", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return EXIT_NOT_OK
        print("queue: OK (no duplicates, all numbers known, no cycles)")
        return EXIT_OK

    if args.next:
        snapshot_path = Path(args.snapshot)
        if not snapshot_path.exists():
            print(f"queue --next: CANNOT-ASSESS — {snapshot_path} is missing", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        snapshot = snapshot_mod.load(snapshot_path)
        held = claims.active_claims(claims.read_ledger(args.ledger))
        ready = [n for n in queue_mod.next_claimable(snapshot, data) if n not in held]
        if not ready:
            print("queue --next: <none> (every queued issue is closed, live-claimed, or blocked)")
            return EXIT_OK
        for number in ready:
            issue = snapshot.get(number)
            title = issue.title if issue else ""
            print(f"#{number} {title}")
        return EXIT_OK

    print(f"queue: {args.queue} not validated — pass --next or --check")
    return EXIT_OK


def add_paths(parser: argparse.ArgumentParser) -> None:
    """Board artifacts every subcommand reads (after the subcommand, e.g. `audit --ledger x`)."""
    parser.add_argument("--snapshot", default=str(snapshot_mod.DEFAULT_PATH))
    parser.add_argument("--ledger", default=str(claims.DEFAULT_CLAIMS_DIR))
    parser.add_argument("--locks", default=str(claims.DEFAULT_LOCK_DIR))
    parser.add_argument("--stale-minutes", type=int, default=snapshot_mod.DEFAULT_STALENESS_MINUTES)
    parser.add_argument("--pool", default=str(pool_mod.POOL_PATH))


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
    claim.add_argument(
        "--files",
        default="",
        help='JSON list of per-file leases, e.g. \'[{"path":"a.py","regions":[[1,10]]}]\' (#702)',
    )
    claim.set_defaults(func=cmd_claim)

    dispatch = sub.add_parser(
        "dispatch", help="arbitrate a directive: prove issue -> epic -> lane before routing (read-only)"
    )
    add_paths(dispatch)
    dispatch.add_argument("--issue", type=int, required=True)
    dispatch.add_argument("--agent", required=True)
    dispatch.add_argument(
        "--lane", default="", help="the lane that owns the unit (required unless the directive declares one)"
    )
    dispatch.add_argument("--directive", default="", help="brain directive id that would authorize the dispatch")
    dispatch.set_defaults(func=cmd_dispatch)

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

    focus_cmd = sub.add_parser("focus", help="show the active epic, its children and the pooled set")
    add_paths(focus_cmd)
    focus_cmd.add_argument("--focus", default=str(focus_mod.DEFAULT_PATH))
    focus_cmd.add_argument(
        "--self-control", action="store_true", help="also prove the resolver and schema can fail"
    )
    focus_cmd.set_defaults(func=cmd_focus)

    pool_cmd = sub.add_parser("pool", help="show the out-of-epic pool (or prove it can fail)")
    add_paths(pool_cmd)
    pool_cmd.add_argument("--focus", default=str(focus_mod.DEFAULT_PATH))
    pool_cmd.add_argument("--self-control", action="store_true", help="also prove the pool reader can fail")
    pool_cmd.set_defaults(func=cmd_pool)

    reap = sub.add_parser("reap", help="release claims wedged by dead agents past a threshold")
    add_paths(reap)
    reap.add_argument("--older-than-minutes", type=int, default=claims.DEFAULT_REAP_MINUTES)
    reap.add_argument("--issue", type=int, default=None)
    reap.add_argument("--reaper", default="brain")
    reap.set_defaults(func=cmd_reap)

    snap = sub.add_parser("snapshot", help="refresh .board/snapshot.json from GitHub")
    snap.add_argument("--from-github", action="store_true")
    snap.add_argument("--repo", default=snapshot_mod.DEFAULT_REPO)
    snap.add_argument("--out", default=str(snapshot_mod.DEFAULT_PATH))
    snap.set_defaults(func=cmd_snapshot)

    trigger = sub.add_parser(
        "trigger",
        help="on a stale snapshot: refresh ONCE, else PARK the directive (#727)",
    )
    add_paths(trigger)
    trigger.add_argument("--directive", required=True, help="the deferred directive's id")
    trigger.add_argument("--repo", default=snapshot_mod.DEFAULT_REPO)
    trigger.add_argument(
        "--fleet-dir",
        default="",
        help="the fleet runtime dir holding parked/ (default AO_FLEET_DIR, else <repo>/.fleet)",
    )
    trigger.set_defaults(func=cmd_trigger)

    queue_cmd = sub.add_parser(
        "queue", help="the owner's committed dispatch queue (#928): next claimable / validate"
    )
    queue_cmd.add_argument("--queue", default=str(queue_mod.DEFAULT_PATH))
    queue_cmd.add_argument("--snapshot", default=str(snapshot_mod.DEFAULT_PATH))
    queue_cmd.add_argument("--ledger", default=str(claims.DEFAULT_CLAIMS_DIR))
    queue_cmd.add_argument("--stale-minutes", type=int, default=snapshot_mod.DEFAULT_STALENESS_MINUTES)
    queue_cmd.add_argument("--next", action="store_true", help="print the next claimable issue(s)")
    queue_cmd.add_argument(
        "--check", action="store_true", help="validate the file: no duplicates, all numbers known, no cycles"
    )
    queue_cmd.set_defaults(func=cmd_queue)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
