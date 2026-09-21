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
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import board_selfheal  # noqa: E402
import claims  # noqa: E402
import focus as focus_mod  # noqa: E402
import live as live_mod  # noqa: E402
import liveness as liveness_mod  # noqa: E402
import order  # noqa: E402
import pool as pool_mod  # noqa: E402
import owner_queue as queue_mod  # noqa: E402
import queue_freshness  # noqa: E402
import snapshot as snapshot_mod  # noqa: E402
import tiered  # noqa: E402
from model import parse_file_claims  # noqa: E402

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: The remedy every staleness refusal names — and, since issue #1179, also runs.
REFRESH_COMMAND = "python3 governance/dispatch/cli.py snapshot --from-github"

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


def _refresh_board(args: argparse.Namespace) -> str:
    """Run the ONE bounded board refresh; return a one-line account of it.

    This is the SAME seam the fleet loop runs (`fleet/terminal.board_trigger` ->
    `snapshot.refresh_or_park`) and the same one every staleness refusal names —
    there is exactly one path that touches the network and exactly one place the
    bounded window is applied.

    It is **opt-in** (issue #1179). It was briefly the default, and that was
    measured to be wrong: it made a READ verb perform a network call and rewrite
    the tracked `.board/snapshot.json`, so a pytest suite (`fleet/tests`) driving
    the loop rewrote the repository's board mid-`make verify` — measured as
    `pytest fleet/tests` changing the snapshot's sha256 on this branch while
    pristine `origin/master` leaves it byte-identical. A gate that reaches the
    network, and a read verb that writes a tracked artifact, are both defects; the
    entry point now names the remedy instead of performing it unasked.
    """
    if not getattr(args, "refresh", False):
        return (
            "no refresh attempted — run this verb with --refresh to run the one bounded "
            f"board refresh in band, or: {REFRESH_COMMAND}"
        )
    if not args.repo:
        return "no refresh attempted — no board repo was named (--repo)"
    refreshed, detail = snapshot_mod.refresh(
        Path(args.snapshot),
        repo=args.repo,
        window_seconds=getattr(args, "refresh_window", None),
    )
    return f"one bounded refresh {'succeeded' if refreshed else 'failed'}: {detail}"


def _stale_board(
    verb: str, args: argparse.Namespace, snapshot: snapshot_mod.Snapshot, age: float
) -> tuple[snapshot_mod.Snapshot, float] | None:
    """The staleness verdict for ``verb``: a usable board, or a named refusal.

    Returns the (possibly refreshed) board and its age on success. On failure it
    prints the refusal and returns ``None``: the refusal names the age, the
    threshold, the refresh ATTEMPT and its outcome, and whether any board producer
    is installed at all — so a dead control reads as a dead control rather than as
    a bare age. Tri-state: an unassessable board is exit 2, never a pass.
    """
    if not snapshot_mod.is_stale(snapshot, args.stale_minutes):
        return snapshot, age

    attempt = _refresh_board(args)
    try:
        refreshed = _load_snapshot(Path(args.snapshot))
    except (OSError, ValueError) as exc:
        print(
            f"{verb}: CANNOT-ASSESS — snapshot-stale ({age:.1f}m > {args.stale_minutes}m); "
            f"{attempt}; the refreshed snapshot is unreadable ({exc})",
            file=sys.stderr,
        )
        return None
    refreshed_age = snapshot_mod.age_minutes(refreshed)
    if not snapshot_mod.is_stale(refreshed, args.stale_minutes):
        print(f"{verb}: snapshot was stale ({age:.1f}m); {attempt}", file=sys.stderr)
        return refreshed, refreshed_age

    print(
        f"{verb}: CANNOT-ASSESS — snapshot-stale ({refreshed_age:.1f}m > {args.stale_minutes}m); "
        f"{attempt} — refresh first: {REFRESH_COMMAND}",
        file=sys.stderr,
    )
    # Name the producer's state too, so "the board is stale" cannot be read as a
    # transient hiccup when in fact nothing on this host can refresh it.
    board_liveness = liveness_mod.assess()
    if board_liveness.finding:
        print(f"{verb}: {board_liveness.finding}", file=sys.stderr)
    elif board_liveness.detail:
        print(f"{verb}: board refresher — {board_liveness.detail}", file=sys.stderr)
    return None


def _board_verdict(verb: str, args: argparse.Namespace) -> tuple[snapshot_mod.Snapshot, float] | None:
    """Load the board and clear staleness for ``verb``, or print the refusal."""
    snapshot_path = Path(args.snapshot)
    if not snapshot_path.exists():
        print(f"{verb}: CANNOT-ASSESS — {snapshot_path} is missing (refresh it with: {REFRESH_COMMAND})", file=sys.stderr)
        return None
    try:
        snapshot = _load_snapshot(snapshot_path)
    except (OSError, ValueError) as exc:
        print(f"{verb}: CANNOT-ASSESS — {snapshot_path} is unreadable ({exc})", file=sys.stderr)
        return None
    age = snapshot_mod.age_minutes(snapshot)
    print(f"{verb}: snapshot age {age:.1f}m (threshold {args.stale_minutes}m)", file=sys.stderr)
    return _stale_board(verb, args, snapshot, age)


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
    assessed = _board_verdict("eligible", args)
    if assessed is None:
        return EXIT_CANNOT_ASSESS
    snapshot, _age = assessed
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
    assessed = _board_verdict("claim", args)
    if assessed is None:
        return EXIT_CANNOT_ASSESS
    snapshot, _age = assessed
    snapshot_path = Path(args.snapshot)
    # The digest is taken AFTER the staleness handling: a successful in-band
    # refresh (#1179) rewrites the file, and a digest of the pre-refresh bytes
    # would record evidence for a board the claim was not judged against.
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
            speculative_base=args.base,
            main=Path(args.main) if args.main else claims.ROOT,
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
    assessed = _board_verdict("dispatch", args)
    if assessed is None:
        return EXIT_CANNOT_ASSESS
    snapshot, _age = assessed
    snapshot_path = Path(args.snapshot)
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
    assessed = _stale_board("status", args, snapshot, age)
    if assessed is None:
        return EXIT_CANNOT_ASSESS
    snapshot, age = assessed
    events = claims.read_ledger(args.ledger)
    live = claims.active_claims(events)
    held = frozenset(live)
    focus_path = Path(getattr(args, "focus", focus_mod.DEFAULT_PATH))
    milestone = order.active_milestone(snapshot, frozenset())
    print(f"active milestone: {milestone or '<none>'}")
    if milestone:
        # Advertise the CLAIMABLE frontier (issue #1168): the milestone frontier
        # alone named work `claim` refuses, which is how `status` came to promise
        # a reader an issue the claim path would not let them take.
        advertised = order.claimable_frontier(snapshot, milestone, held, focus_path)
        if advertised is not None:
            print(f"frontier: #{advertised.number} {advertised.title}")
        else:
            print(f"frontier: <none> — no candidate in {milestone!r} is claimable")
            # ...which is normally because an epic focus is active and pooled that
            # milestone. A bare `<none>` would read as "nothing to do"; name the
            # frontier the fleet IS driving, still only if it is claimable.
            active_epic = focus_mod.active(snapshot, focus_path)
            if active_epic is not None:
                print(f"active epic: #{active_epic.number} {active_epic.title}")
                ready = next(
                    (
                        child
                        for child in focus_mod.open_children(snapshot, active_epic.number)
                        if order.eligible(
                            snapshot,
                            child.number,
                            claimed_by_others=held,
                            focus_path=focus_path,
                        ).eligible
                    ),
                    None,
                )
                if ready is not None:
                    print(f"active-epic frontier: #{ready.number} {ready.title}")
        disagreement = order.unclaimable_frontier(snapshot, milestone, held, focus_path)
        if disagreement:
            print(f"  {disagreement} (the milestone frontier is not claimable)", file=sys.stderr)
    else:
        print("frontier: <none>")
    # Is there anything that can actually keep this board live? (issue #1179)
    board_liveness = liveness_mod.assess()
    print(
        f"board refresher: {board_liveness.verdict}"
        + (f" — {board_liveness.detail}" if board_liveness.detail else "")
    )
    if board_liveness.finding:
        print(f"  {board_liveness.finding}", file=sys.stderr)
    # Dangling-on-a-closed-epic issues are refused `epic-closed` — correctly — but
    # were named by nothing, so they were invisible AND permanently unclaimable
    # (issue #1179). Report them with their remedy.
    dangling = order.dangling_epic_findings(snapshot)
    if dangling:
        print(
            f"dangling-epic: {len(dangling)} open issue(s) declare a Parent that is closed",
            file=sys.stderr,
        )
        for finding in dangling:
            print(f"  {finding}", file=sys.stderr)
        print(f"  {order.REMEDIATION_REPARENT}", file=sys.stderr)
    else:
        print("dangling-epic: none — every open issue's declared parent is open")
    print(f"live claims: {len(live)}")
    for issue, claim in sorted(live.items()):
        print(f"  #{issue} held by {claim.agent} ({claim.lane}) since {claim.at} reason={claim.reason}")
    if getattr(args, "live", False):
        projection = live_mod.project(snapshot, ledger=args.ledger)
        print(live_mod.render(projection))
    return EXIT_OK


def cmd_liveness(args: argparse.Namespace) -> int:
    """Does the board's liveness contract have a producer — and is it the declared one?

    The defect this verb exists for (issue #1179): a refresh job was declared and
    a reconciler proved it could heal drift *in a scratch crontab*, while nothing
    asserted the REAL crontab, so nothing noticed the producer was absent. Tri-state
    0/1/2, and an unreadable crontab or manifest is 2 — never a pass.
    """
    verdict = liveness_mod.assess(
        manifest_path=Path(args.manifest) if args.manifest else None,
        crontab_file=Path(args.crontab_file) if args.crontab_file else None,
        self_refresh=not args.no_self_refresh,
    )
    print(json.dumps(verdict.to_json(), indent=2))
    if not verdict.assessable:
        print(f"liveness: CANNOT-ASSESS — {verdict.detail}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    if verdict.finding:
        print(f"liveness: NOT-OK — {verdict.finding}", file=sys.stderr)
        return EXIT_NOT_OK
    print(f"liveness: OK — {verdict.detail or verdict.verdict}")
    return EXIT_OK


def cmd_dangling(args: argparse.Namespace) -> int:
    """Report open issues whose declared parent is CLOSED (issue #1179).

    Exit 0 = none, 1 = the board has dangling issues (each named, with the
    remedy), 2 = the board cannot be assessed. The `epic-closed` refusal itself is
    deliberately NOT relaxed: the epic that would own the work is gone, so the fix
    is visibility plus a reachable re-parenting path, not a silent re-allow.
    """
    assessed = _board_verdict("dangling", args)
    if assessed is None:
        return EXIT_CANNOT_ASSESS
    snapshot, _age = assessed
    findings = order.dangling_epic_findings(snapshot)
    if not findings:
        print("dangling-epic: none — every open issue's declared parent is open")
        return EXIT_OK
    print(
        f"dangling-epic: {len(findings)} open issue(s) dangle on a closed parent; "
        "they are refused `epic-closed` and can never be claimed until re-parented",
        file=sys.stderr,
    )
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    print(f"  {order.REMEDIATION_REPARENT}", file=sys.stderr)
    return EXIT_NOT_OK


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
            # Load only to VALIDATE the focus file: it raises FocusInvalid on a
            # malformed declaration, while the epic itself is resolved below.
            focus_mod.load(args.focus)
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


def cmd_try_loop(args: argparse.Namespace) -> int:
    """Run the tiered try-loop dispatcher for one issue (L0 -> L1 -> L2 escalation).

    ``--dry-run`` resolves the tier -> model mapping and prints it without
    dispatching, writing the ledger, or touching labels. ``--labels`` (JSON) and
    ``--body-file`` let the caller supply the issue's labels/body directly, so the
    deterministic fixture and the negative control run without ``gh``.
    """
    labels = args.labels
    if labels is None:
        labels = tiered._gh_labels(args.issue)  # noqa: SLF001
    elif isinstance(labels, str):
        try:
            labels = json.loads(labels)
        except json.JSONDecodeError as exc:
            print(f"try-loop: CANNOT-ASSESS — --labels is not valid JSON: {exc}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS

    if args.body_file:
        try:
            body = Path(args.body_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"try-loop: CANNOT-ASSESS — --body-file unreadable: {exc}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
    else:
        body = tiered._gh_body(args.issue)  # noqa: SLF001

    # --no-gh / --no-dispatch swap the real side effects for no-ops so the loop
    # can be driven against a fixture without touching GitHub or a live model.
    noop = lambda *a, **k: None  # noqa: E731
    try:
        outcome = tiered.run(
            args.issue,
            body,
            labels,
            audit_path=args.audit,
            max_attempts=args.max_attempts,
            provider=args.provider,
            agent=args.agent,
            apply_label=noop if args.no_gh else None,
            post_comment=noop if args.no_gh else None,
            invoke=noop if args.no_dispatch else None,
            dry_run=args.dry_run,
        )
    except tiered.TieredRefusal as exc:
        print(f"try-loop REFUSED: {exc.reason} — {exc.detail}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    if outcome.get("dry_run"):
        print(json.dumps({
            "dry_run": True,
            "issue": outcome["issue"],
            "tier": outcome["tier"],
            "warning": outcome.get("warning"),
            "provider": outcome["provider"],
            "model": outcome["model"],
            "acceptance_commands": outcome["commands"],
        }, indent=2))
        return EXIT_OK

    print(json.dumps({
        "issue": outcome["issue"],
        "tier": outcome["tier"],
        "warning": outcome.get("warning"),
        "final_status": outcome["final_status"],
        "final_tier": outcome.get("final_tier"),
        "escalated_to": outcome.get("escalated_to"),
        "attempts": [{"tier": a["tier"], "model": a["model"], "status": a["status"]} for a in outcome["attempts"]],
    }, indent=2))
    return EXIT_OK if outcome["final_status"] == "pass" else EXIT_NOT_OK


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

    if args.fix:
        snapshot_path = Path(args.snapshot)
        if not snapshot_path.exists():
            print(
                f"queue --fix: CANNOT-ASSESS — {snapshot_path} is missing "
                "(refresh it with: python3 governance/dispatch/cli.py snapshot --from-github)",
                file=sys.stderr,
            )
            return EXIT_CANNOT_ASSESS
        try:
            snapshot = snapshot_mod.load(snapshot_path)
        except (OSError, ValueError) as exc:
            print(
                f"queue --fix: CANNOT-ASSESS — {snapshot_path} is unreadable ({exc}); "
                f"refresh it with: {REFRESH_COMMAND}",
                file=sys.stderr,
            )
            return EXIT_CANNOT_ASSESS
        if snapshot_mod.is_stale(snapshot, args.stale_minutes):
            age = snapshot_mod.age_minutes(snapshot)
            print(
                f"queue --fix: CANNOT-ASSESS — snapshot-stale ({age:.1f}m > {args.stale_minutes}m); "
                "closed/open state needs a fresh board — refresh first: "
                "python3 governance/dispatch/cli.py snapshot --from-github",
                file=sys.stderr,
            )
            return EXIT_CANNOT_ASSESS
        text = queue_path.read_text(encoding="utf-8")
        new_text, removed = queue_mod.prune_closed_text(text, snapshot)
        if removed:
            queue_path.write_text(new_text, encoding="utf-8")
            listed = ", ".join(f"#{n}" for n in removed)
            print(f"queue --fix: dropped {len(removed)} closed issue(s) from {queue_path}: {listed}")
        else:
            print(f"queue --fix: OK ({queue_path} has no closed issues)")
        # Self-verify (GR-12/AO-GR-19: a check that cannot fail is a
        # formality): re-load and re-validate what was just written against
        # the same snapshot. A prune whose regex missed a wave shape (e.g. a
        # future block-style `issues:` list) must be caught here, not reported
        # as a false "OK" (issue #1113).
        rewritten = queue_mod.load(queue_path)
        survivors = [
            problem
            for problem in queue_mod.validate(rewritten, snapshot)
            if "already closed" in problem
        ]
        if survivors:
            print(
                f"queue --fix: FAIL — {len(survivors)} closed issue(s) survived the prune "
                "(the file's 'issues:' shape was not recognized):",
                file=sys.stderr,
            )
            for problem in survivors:
                print(f"  - {problem}", file=sys.stderr)
            return EXIT_NOT_OK
        return EXIT_OK

    if args.check:
        snapshot_path = Path(args.snapshot)
        snapshot = None
        if snapshot_path.exists():
            # An UNREADABLE board is CANNOT-ASSESS, never a traceback: a caller
            # that reads rc 1 from an uncaught JSONDecodeError cannot tell "the
            # committed queue is wrong" from "the board could not be read", and
            # unassessable is explicitly not a verdict (issue #1189).
            try:
                snapshot = snapshot_mod.load(snapshot_path)
            except (OSError, ValueError) as exc:
                print(
                    f"queue --check: CANNOT-ASSESS — {snapshot_path} is unreadable ({exc}); "
                    f"refresh it with: {REFRESH_COMMAND}",
                    file=sys.stderr,
                )
                return EXIT_CANNOT_ASSESS
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
        try:
            snapshot = snapshot_mod.load(snapshot_path)
        except (OSError, ValueError) as exc:
            print(
                f"queue --next: CANNOT-ASSESS — {snapshot_path} is unreadable ({exc}); "
                f"refresh it with: {REFRESH_COMMAND}",
                file=sys.stderr,
            )
            return EXIT_CANNOT_ASSESS
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


def cmd_freshness(args: argparse.Namespace) -> int:
    """Assert the committed board snapshot's age is inside the tolerance this
    consumer declares (issue #1189).

    The check that owns this artifact (``scripts/check-dispatch-queue.sh``) runs
    offline, so the board is a *committed* point-in-time file and the 15-minute
    liveness threshold ``controls.yaml`` declares for a running loop can never be
    met — arming the check with it made the check permanently CANNOT-ASSESS, which
    ``verify.sh`` folds into SKIP and the reviewer reads as a pass. This verb
    states the age a committed artifact CAN honour and refuses beyond it, naming
    the file, the timestamp, the age, the tolerance and the ONE refresh verb.

    Tri-state, fail-closed: ``0`` inside the tolerance / ``1`` outside it or
    unaged (a named violation) / ``2`` unreadable or absent — never a pass.
    """
    now = None
    if args.now:
        try:
            now = queue_freshness.parse_iso(args.now)
        except ValueError as exc:
            print(f"freshness: CANNOT-ASSESS — --now {args.now!r} is not a timestamp ({exc})", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
    max_age = queue_freshness.DEFAULT_MAX_AGE_HOURS if args.max_age_hours is None else args.max_age_hours
    try:
        if getattr(args, "refresh", False):
            verdict, healed, refresh_detail = board_selfheal.self_heal(
                queue_freshness.assess,
                Path(args.snapshot),
                max_age_hours=max_age,
                now=now,
                repo=args.repo,
                timeout=getattr(args, "refresh_window", None),
                stale_code=queue_freshness.CODE_BOARD_STALE,
            )
        else:
            verdict = queue_freshness.assess(Path(args.snapshot), max_age_hours=max_age, now=now)
            healed, refresh_detail = False, ""
    except queue_freshness.CannotAssess as exc:
        print(f"freshness: CANNOT-ASSESS — {exc} (refresh it with: {queue_freshness.REFRESH_COMMAND})", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    if verdict.ok:
        if healed:
            print(f"freshness: OK (self-healed — {refresh_detail}) — {verdict.render()}")
        else:
            print(f"freshness: OK — {verdict.render()}")
        return EXIT_OK
    for finding in verdict.findings:
        print(f"  FAIL  {finding.render()}", file=sys.stderr)
    if refresh_detail:
        print(f"  FAIL  self-heal refresh failed: {refresh_detail}", file=sys.stderr)
    elif not getattr(args, "refresh", False):
        print("  FAIL  no self-heal attempted — run with --refresh to try it", file=sys.stderr)
    print(
        "freshness: FAIL — the committed board snapshot is outside the age this "
        "consumer tolerates (see above), so every exists/open answer is against a "
        f"stale frontier (refresh it with: {queue_freshness.REFRESH_COMMAND})",
        file=sys.stderr,
    )
    return EXIT_NOT_OK


def add_paths(parser: argparse.ArgumentParser) -> None:
    """Board artifacts every subcommand reads (after the subcommand, e.g. `audit --ledger x`)."""
    parser.add_argument("--snapshot", default=str(snapshot_mod.DEFAULT_PATH))
    parser.add_argument("--ledger", default=str(claims.DEFAULT_CLAIMS_DIR))
    parser.add_argument("--locks", default=str(claims.DEFAULT_LOCK_DIR))
    parser.add_argument("--stale-minutes", type=int, default=snapshot_mod.DEFAULT_STALENESS_MINUTES)
    parser.add_argument("--pool", default=str(pool_mod.POOL_PATH))
    # The liveness half of the staleness contract (issue #1179). OFF by default
    # and opt-in: a refusal that names its own remedy should offer it, but a READ
    # verb must not perform a network call or rewrite a tracked artifact unless
    # the caller asked — measured: making it the default let a pytest suite drive
    # a real board refresh during `make verify`.
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "on a stale snapshot, run the ONE bounded board refresh in band (the same "
            "seam the fleet loop uses) before deciding"
        ),
    )
    parser.add_argument(
        "--refresh-window",
        type=float,
        default=None,
        help="seconds the in-band refresh may take (default: the trigger window)",
    )
    parser.add_argument(
        "--repo",
        default=snapshot_mod.DEFAULT_REPO,
        help="the GitHub board a refresh reads (default: %(default)s)",
    )


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
    claim.add_argument(
        "--base",
        default="",
        help=(
            "speculative branch-stacking (DG-3, #699): the upstream lane's OWN branch "
            "this claim is cut from. Accepted ONLY when the issue would otherwise be "
            "refused `blocked` by that exact upstream (refused by name, "
            "speculative-base-not-upstream, if --base names any other branch); never "
            "bypasses out-of-order/already-claimed/file-region refusals"
        ),
    )
    claim.add_argument(
        "--main",
        default="",
        help="the repository the isolation attestation is written into (default: this checkout)",
    )
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

    try_loop = sub.add_parser(
        "try-loop",
        help="tiered try-loop dispatcher (L0 -> L1 -> L2 escalation, #1524)",
    )
    try_loop.add_argument("--issue", type=int, required=True)
    try_loop.add_argument("--agent", default="brain")
    try_loop.add_argument("--provider", default=None, help="claude or deepseek (default: policy default_provider)")
    try_loop.add_argument("--max-attempts", type=int, default=None, help="attempt budget per tier (default: issue body N=, else policy)")
    try_loop.add_argument(
        "--audit",
        default=str(tiered.audit.DEFAULT_AUDIT_PATH),
        help="the tiered-attempt audit trail (default: %(default)s)",
    )
    try_loop.add_argument(
        "--labels",
        default=None,
        help="JSON list of labels to read instead of `gh issue view <n> --json labels`",
    )
    try_loop.add_argument(
        "--body-file",
        default="",
        help="read the issue body from this file instead of `gh issue view <n> --json body`",
    )
    try_loop.add_argument("--dry-run", action="store_true", help="resolve the tier->model mapping and print it; no dispatch, no ledger, no labels")
    try_loop.add_argument("--no-gh", action="store_true", help="do not apply escalate labels or post comments (fixture/negative-control)")
    try_loop.add_argument("--no-dispatch", action="store_true", help="do not invoke a model (run acceptance commands only)")
    try_loop.set_defaults(func=cmd_try_loop)

    status = sub.add_parser("status", help="show the active milestone, frontier and live claims")
    add_paths(status)
    status.add_argument("--focus", default=str(focus_mod.DEFAULT_PATH), help="the pinned focus to resolve against")
    status.add_argument(
        "--live", action="store_true",
        help="also print the live projection (issue #885): claim set, frontier and ready wave, read fresh",
    )
    status.set_defaults(func=cmd_status)

    dangling = sub.add_parser(
        "dangling", help="report open issues whose declared parent is closed (issue #1179)"
    )
    add_paths(dangling)
    dangling.set_defaults(func=cmd_dangling)

    liveness = sub.add_parser(
        "liveness", help="does the board's liveness contract have an installed producer? (issue #1179)"
    )
    liveness.add_argument("--manifest", default="", help="the fleet-jobs manifest to read the declaration from")
    liveness.add_argument(
        "--crontab-file",
        default="",
        help="read this file INSTEAD of the live crontab (the gate's fixture seam; the real crontab is the default)",
    )
    liveness.add_argument(
        "--no-self-refresh",
        action="store_true",
        help="declare that the entry point has no in-band refresh (makes a missing producer a finding)",
    )
    liveness.set_defaults(func=cmd_liveness)

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
    # `--repo` comes from add_paths (#1179); redeclaring it here conflicted.
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
    # `queue` reads no repo and runs no refresh: its staleness refusal is its own
    # declared contract, so it keeps its own `--stale-minutes` and no `--repo`.
    queue_cmd.add_argument("--next", action="store_true", help="print the next claimable issue(s)")
    queue_cmd.add_argument(
        "--check", action="store_true", help="validate the file: no duplicates, all numbers known, no cycles"
    )
    queue_cmd.add_argument(
        "--fix",
        action="store_true",
        help="drop issues CLOSED on the (fresh) board snapshot from the committed queue file (issue #1113)",
    )
    queue_cmd.set_defaults(func=cmd_queue)

    fresh = sub.add_parser(
        "freshness",
        help="assert the committed board snapshot's age is inside the tolerance this consumer declares",
    )
    fresh.add_argument("--snapshot", default=str(snapshot_mod.DEFAULT_PATH))
    fresh.add_argument(
        "--max-age-hours",
        type=float,
        default=None,
        help=f"the tolerated age in hours (default: {queue_freshness.DEFAULT_MAX_AGE_HOURS:g})",
    )
    fresh.add_argument(
        "--now",
        default=None,
        help=(
            "NEGATIVE-CONTROL SEAM: evaluate the age at this instant instead of the "
            "wall clock, so a gate can provoke the refusal deterministically. The "
            "gate's assertion on the real snapshot never passes it."
        ),
    )
    fresh.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "self-heal (issue #1692): on a stale snapshot, run the ONE bounded board "
            "refresh before failing — OFF by default, a READ verb must not reach the "
            "network unasked"
        ),
    )
    fresh.add_argument(
        "--refresh-window",
        type=float,
        default=None,
        help="seconds the in-band refresh may take (default: the trigger window)",
    )
    fresh.add_argument(
        "--repo",
        default=snapshot_mod.DEFAULT_REPO,
        help="the GitHub board a --refresh reads (default: %(default)s)",
    )
    fresh.set_defaults(func=cmd_freshness)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
