#!/usr/bin/env python3
"""Lane isolation command line — mint, provision, export, audit, landed, close (#263).

Typical use, from the execution loop:

    python3 governance/isolation/cli.py open --issue 263 --agent copilot-brain --lane governance-isolation
    eval "$(python3 governance/isolation/cli.py env --issue 263 --agent copilot-brain)"
    python3 governance/isolation/cli.py audit --all
    python3 governance/isolation/cli.py landed --commit <sha>      # real landed history
    python3 governance/isolation/cli.py enforce --range HEAD      # enforced over real history

Exit-code contract (repo tri-state convention): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
An audit that assessed nothing is CANNOT-ASSESS, never OK: ``audit`` with no lane
records and ``enforce`` with no commits in range both report that they could not
assess the rule rather than reporting it satisfied (issue #287, GR-12).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.isolation.audit import audit_all, audit_lane  # noqa: E402
from governance.isolation.identity import (  # noqa: E402
    IdentityRefused,
    SessionIdentity,
    mint,
)
from governance.isolation.landed import (  # noqa: E402
    BASELINE_PATH,
    DEFAULT_RANGE,
    EXIT_CODES,
    VERDICT_OK,
    assess,
)
from governance.isolation.trailer import (  # noqa: E402
    PredicateUnavailable,
    classify_commit,
    run_landed,
)
from governance.isolation.worktree import (  # noqa: E402
    ProvisionRefused,
    close,
    default_worktree_root,
    git,
    list_records,
    machine_managed_uncommitted,
    main_repo_root,
    provision,
    read_record,
    record_dir,
    write_record,
)

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2


def _mint(args: argparse.Namespace) -> SessionIdentity:
    root = Path(args.root) if args.root else default_worktree_root()
    return mint(
        issue=args.issue,
        agent_id=args.agent,
        lane=args.lane or "",
        suffix=args.suffix or "",
        worktree_root=root,
    )


def cmd_open(args: argparse.Namespace) -> int:
    """Mint the identity and create the lane — worktree, branch, signature."""
    identity = _mint(args)
    main = Path(args.main)
    if not main.exists():
        print(f"open: CANNOT-ASSESS — {main} does not exist", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    try:
        result = provision(
            identity,
            main,
            base=args.base,
            fetch_remote="origin" if args.fetch else "",
            allow_tmpfs=args.allow_tmpfs_root,
        )
    except ProvisionRefused as refused:
        # A refusal is a decision, not a crash. Nothing was created, and the
        # reason names itself — e.g. lane-worktree-on-tmpfs (issue #516).
        print(f"open: NOT-OK — {refused}", file=sys.stderr)
        return EXIT_NOT_OK
    write_record(identity, main)
    problems = audit_lane(identity, main) if result.ok else []
    payload = {
        "identity": identity.to_json(),
        "created": result.created,
        "env": identity.env(),
        "problems": [str(problem) for problem in problems],
    }
    print(json.dumps(payload, indent=2))
    if problems:
        print(f"open: NOT-OK — lane provisioned but {len(problems)} isolation problem(s)", file=sys.stderr)
        return EXIT_NOT_OK
    print(f"open: OK — lane {identity.session_id} on {identity.branch} at {identity.worktree}", file=sys.stderr)
    return EXIT_OK


def cmd_env(args: argparse.Namespace) -> int:
    """The identity as environment: the session's own id and signature."""
    identity = _mint(args)
    if args.json:
        print(json.dumps(identity.env(), indent=2))
    else:
        print(identity.shell_env())
    return EXIT_OK


def cmd_audit(args: argparse.Namespace) -> int:
    """Re-derive isolation from the real worktrees and refuse when it is broken."""
    main = Path(args.main)
    if not main.exists():
        print(f"audit: CANNOT-ASSESS — {main} does not exist", file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    if args.session:
        identity = read_record(args.session, main)
        if identity is None:
            print(f"audit: CANNOT-ASSESS — no lane record for session {args.session}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        results = {identity.session_id: audit_lane(identity, main)}
    else:
        results = audit_all(main)

    if not results:
        # An empty audit is not a pass (issue #287): with no lane records there is
        # nothing to re-derive, so the honest answer is CANNOT-ASSESS. Reporting OK
        # here is exactly the vacuous green the no-false-green doctrine rejects —
        # and it is indistinguishable from a machine where lanes were never
        # provisioned, which is the failure the rule exists to catch.
        print(
            f"session-isolation: CANNOT-ASSESS — no lane records under {record_dir(main)}, so no lane "
            "was audited; an empty audit is not a pass",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    failed = 0
    for session_id, problems in sorted(results.items()):
        if problems:
            failed += 1
            print(f"  FAIL  lane {session_id}", file=sys.stderr)
            for problem in problems:
                print(f"          {problem}", file=sys.stderr)
        else:
            print(f"  OK    lane {session_id}")
    if failed:
        print(f"session-isolation: FAIL ({failed} of {len(results)} lane(s) not isolated)", file=sys.stderr)
        return EXIT_NOT_OK
    print(f"session-isolation: OK ({len(results)} lane(s) isolated)")
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    main = Path(args.main)
    identities = list_records(main)
    if args.json:
        print(json.dumps([identity.to_json() for identity in identities], indent=2))
        return EXIT_OK
    for identity in identities:
        print(f"{identity.session_id}  #{identity.issue}  {identity.branch}  {identity.agent_id}  {identity.worktree}")
    return EXIT_OK


def cmd_landed(args: argparse.Namespace) -> int:
    """Re-check the ticket-trailer rule against real landed history.

    The lane audit re-derives isolation from the *live* worktrees, so history
    that already landed on the default branch is never examined — the second
    gap #287 names. This mode points the same rule at commits that have landed:
    one named commit (``--commit``), or a whole range. Either way the verdict is
    the shared predicate's (``scripts/check-pr-contract.sh``), so this is a
    second *surface* for the rule and never a second rule.
    """
    main = Path(args.main)
    if not main.exists():
        print(f"landed: CANNOT-ASSESS — {main} does not exist", file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    if args.commit:
        resolved = git(main, "rev-parse", "--verify", f"{args.commit}^{{commit}}")
        if resolved.returncode != 0:
            print(f"landed: CANNOT-ASSESS — {args.commit} does not resolve to a commit in {main}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        sha = resolved.stdout.strip()
        author = git(main, "log", "-1", "--format=%an <%ae>", sha).stdout.strip()
        try:
            finding = classify_commit(main, sha)
        except PredicateUnavailable as exc:
            print(f"landed: CANNOT-ASSESS — {exc}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        if finding:
            print(f"  FAIL  {finding}:{sha[:12]} (authored by {author})", file=sys.stderr)
            print(
                "isolation-landed: FAIL — a landed commit does not carry `Refs <slug>#<n>` "
                "in its trailing trailer block",
                file=sys.stderr,
            )
            return EXIT_NOT_OK
        print(f"  OK    {sha[:12]} carries the ticket trailer in its trailing block (authored by {author})")
        return EXIT_OK

    try:
        result = run_landed(main, args.range_, args.gate)
    except PredicateUnavailable as exc:
        print(f"landed: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    # The shared gate's own output, verbatim: the verdict is delegated, not
    # re-interpreted. The grandfathered boundary it reports is part of that
    # verdict.
    if result.output.strip():
        print(result.output.rstrip(), file=sys.stderr)
    if result.returncode == 0:
        print(
            f"isolation-landed: OK — no landed commit in {args.range_} lacks the ticket trailer "
            "where the shared predicate requires it"
        )
        return EXIT_OK
    if result.returncode == EXIT_CANNOT_ASSESS:
        return EXIT_CANNOT_ASSESS
    print(f"isolation-landed: FAIL — the shared predicate named landed commit(s) in {args.range_}", file=sys.stderr)
    return EXIT_NOT_OK


def cmd_enforce(args: argparse.Namespace) -> int:
    """Enforce the ticket-trailer rule over landed history, with recorded legacy.

    ``landed`` answers "what does the shared predicate say about this history";
    ``enforce`` answers "does this history satisfy the rule, given the legacy that
    was measured before the rule existed" — the surface issue #287 found missing.
    The quarantine is keyed by commit and can only shrink: a recorded entry that
    now complies is a failure, and an entry outside the range leaves the rule
    unproven rather than satisfied.
    """
    main = Path(args.main)
    if not main.exists():
        print(f"enforce: CANNOT-ASSESS — {main} does not exist", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    result = assess(main, args.range_, args.baseline, args.gate)
    stream = sys.stderr if result.verdict != VERDICT_OK else sys.stdout
    for line in result.lines():
        print(line, file=stream)
    return EXIT_CODES[result.verdict]


def cmd_close(args: argparse.Namespace) -> int:
    """Remove a lane's worktree — never discarding the lane's own work silently.

    What was *ignored* is reported, not hidden: a reclaim that proceeded past
    machine-managed board state says which paths it passed over (#834), so
    "the worktree looked clean" and "the worktree was dirty only in state the
    fleet regenerates" are distinguishable in the output.
    """
    main = Path(args.main)
    identity = read_record(args.session, main)
    if identity is None:
        print(f"close: CANNOT-ASSESS — no lane record for session {args.session}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    ignored = machine_managed_uncommitted(identity.worktree) if identity.worktree.exists() and not args.force else []
    kept = close(identity, main, force=args.force)
    if kept:
        for reason in kept:
            print(f"close: NOT-OK — {reason}", file=sys.stderr)
        return EXIT_NOT_OK
    note = f" (ignored machine-managed state: {', '.join(ignored)})" if ignored else ""
    print(f"close: OK — lane {identity.session_id} removed{note}")
    return EXIT_OK


def default_main() -> str:
    """The repository the caller is working in (its own main checkout)."""
    try:
        return str(main_repo_root(Path.cwd()))
    except ProvisionRefused:
        return "."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="session", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add_mint_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--issue", type=int, required=True)
        target.add_argument("--agent", required=True, help="agent slug, never an email address")
        target.add_argument("--lane", default="", help="the lane that owns the issue")
        target.add_argument("--suffix", default="", help="disambiguates a second session on one issue")
        target.add_argument("--root", default="", help="worktree root (default $AO_WORKTREE_ROOT or ~/ao-worktrees)")

    open_cmd = sub.add_parser("open", help="mint the identity and create the lane")
    add_mint_args(open_cmd)
    open_cmd.add_argument("--main", default=default_main(), help="the repository to add the worktree to")
    open_cmd.add_argument("--base", default="origin/master", help="the commit the lane branches from")
    open_cmd.add_argument("--fetch", action="store_true", help="fetch origin/master first")
    open_cmd.add_argument(
        "--allow-tmpfs-root",
        action="store_true",
        help="accept a RAM-backed worktree root — throwaway gate scratch only; a lane on tmpfs is lost on reboot (#516)",
    )
    open_cmd.set_defaults(func=cmd_open)

    env_cmd = sub.add_parser("env", help="print the session environment (id + signature)")
    add_mint_args(env_cmd)
    env_cmd.add_argument("--json", action="store_true")
    env_cmd.set_defaults(func=cmd_env)

    audit_cmd = sub.add_parser("audit", help="verify lanes satisfy the isolation contract")
    audit_cmd.add_argument("--main", default=default_main())
    audit_cmd.add_argument("--session", default="", help="audit one lane (default: every recorded lane)")
    audit_cmd.add_argument("--all", action="store_true", help="accepted for symmetry with --session")
    audit_cmd.set_defaults(func=cmd_audit)

    list_cmd = sub.add_parser("list", help="list provisioned lanes")
    list_cmd.add_argument("--main", default=default_main())
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(func=cmd_list)

    landed_cmd = sub.add_parser("landed", help="re-check the ticket-trailer rule against landed history")
    landed_cmd.add_argument("--main", default=default_main(), help="the repository whose landed history is re-checked")
    landed_cmd.add_argument("--commit", default="", help="classify one landed commit (any rev)")
    landed_cmd.add_argument("--range", dest="range_", default="HEAD", help="the landed range to re-check (default HEAD)")
    landed_cmd.add_argument("--gate", default="", help="the enforcement boundary commit (default: the shared gate's own)")
    landed_cmd.set_defaults(func=cmd_landed)

    enforce_cmd = sub.add_parser(
        "enforce",
        help="enforce the ticket-trailer rule over landed history against recorded legacy (#287)",
    )
    enforce_cmd.add_argument("--main", default=default_main(), help="the repository whose landed history is enforced")
    enforce_cmd.add_argument(
        "--range",
        dest="range_",
        default=DEFAULT_RANGE,
        help=f"the landed range to enforce over (default {DEFAULT_RANGE}: everything reachable from the branch)",
    )
    enforce_cmd.add_argument(
        "--baseline",
        default=str(BASELINE_PATH),
        help="the recorded-legacy baseline (default: governance/isolation/landed-baseline.json)",
    )
    enforce_cmd.add_argument("--gate", default="", help="the enforcement boundary commit (default: the shared gate's own)")
    enforce_cmd.set_defaults(func=cmd_enforce)

    close_cmd = sub.add_parser("close", help="remove a lane worktree")
    close_cmd.add_argument("--session", required=True)
    close_cmd.add_argument("--main", default=default_main())
    close_cmd.add_argument("--force", action="store_true", help="remove even with uncommitted work")
    close_cmd.set_defaults(func=cmd_close)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (IdentityRefused, ProvisionRefused) as exc:
        print(f"{args.command}: REFUSED — {exc}", file=sys.stderr)
        return EXIT_NOT_OK


if __name__ == "__main__":
    raise SystemExit(main())
