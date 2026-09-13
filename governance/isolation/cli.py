#!/usr/bin/env python3
"""Lane isolation command line — mint, provision, export, audit, close (#263).

Typical use, from the execution loop:

    python3 governance/isolation/cli.py open --issue 263 --agent copilot-brain --lane governance-isolation
    eval "$(python3 governance/isolation/cli.py env --issue 263 --agent copilot-brain)"
    python3 governance/isolation/cli.py audit --all

Exit-code contract (repo tri-state convention): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
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
from governance.isolation.worktree import (  # noqa: E402
    ProvisionRefused,
    close,
    default_worktree_root,
    list_records,
    main_repo_root,
    provision,
    read_record,
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
    result = provision(identity, main, base=args.base, fetch_remote="origin" if args.fetch else "")
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
        print("session-isolation: OK (no lanes provisioned)")
        return EXIT_OK

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


def cmd_close(args: argparse.Namespace) -> int:
    """Remove a lane's worktree — never discarding uncommitted work silently."""
    main = Path(args.main)
    identity = read_record(args.session, main)
    if identity is None:
        print(f"close: CANNOT-ASSESS — no lane record for session {args.session}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    kept = close(identity, main, force=args.force)
    if kept:
        for reason in kept:
            print(f"close: NOT-OK — {reason}", file=sys.stderr)
        return EXIT_NOT_OK
    print(f"close: OK — lane {identity.session_id} removed")
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
