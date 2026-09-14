#!/usr/bin/env python3
"""CLI for the paperclip.ing adapter (issue #428).

Three verbs, matching the three things an operator needs from the seam:

* ``plan``  — build and print the offline dry-run sync plan (JSON or markdown).
  No network: it is a pure function of the tree.
* ``check`` — validate every mapped record against the three frozen seam
  schemas; tri-state exit (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS). This is what the
  gate calls.
* ``push``  — the live path: send the plan to the upstream ``/api`` surface over
  ``HttpTransport``. Implemented, and deliberately **never** run by the gate.

Run as ``python3 integrations/paperclip/cli.py <verb>`` from the repo root.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integrations.paperclip import client as client_mod  # noqa: E402
from integrations.paperclip import mapping as mapping_mod  # noqa: E402
from integrations.paperclip.model import PaperclipError  # noqa: E402


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root).resolve()


def cmd_plan(args: argparse.Namespace) -> int:
    root = _root(args)
    plan = mapping_mod.build_plan(root, company_id=args.company, session_id=args.session_id)
    if args.markdown:
        print(f"# paperclip-ing sync plan ({plan['paperclip']['company_id']})\n")
        print(f"mode: `{plan['paperclip']['mode']}` ({plan['paperclip']['adr']})\n")
        for section in ("agents", "tickets", "budgets", "heartbeats"):
            print(f"- {section}: {len(plan[section])}")
        print("\n## tickets")
        for ticket in plan["tickets"][: args.limit]:
            print(
                f"- `{ticket['id']}` -> {ticket['owner']} [{ticket['status']}] "
                f"goal={ticket['goal']}"
            )
        print("\n## budgets")
        for budget in plan["budgets"]:
            print(
                f"- {budget['scope']['level']}:{budget['scope']['id']} "
                f"cap={budget['cap']} {budget['currency']} spent={budget['spent']} "
                f"hard_stop={budget['hard_stop']}"
            )
    else:
        print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = _root(args)
    schema_dir = root / mapping_mod.SCHEMA_DIR
    if not schema_dir.is_dir():
        print(f"check: CANNOT-ASSESS — no seam schemas at {schema_dir}", file=sys.stderr)
        return 2
    schemas = mapping_mod.load_schemas(root)
    plan = mapping_mod.build_plan(root, company_id=args.company, session_id=args.session_id)
    findings = mapping_mod.validate_plan(plan, schemas)
    counts = {s: len(plan[s]) for s in mapping_mod.SECTION_SCHEMA}
    if findings:
        print(
            f"check: NOT-OK — {len(findings)} finding(s) across "
            f"{counts['heartbeats']} heartbeat(s), {counts['tickets']} ticket(s), "
            f"{counts['budgets']} budget(s)",
            file=sys.stderr,
        )
        for finding in findings:
            print(f"  FAIL  {finding}", file=sys.stderr)
        return 1
    print(
        "check: OK — "
        f"{counts['heartbeats']} heartbeat(s), {counts['tickets']} ticket(s), "
        f"{counts['budgets']} budget(s) conform to the three seam schemas"
    )
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    root = _root(args)
    plan = mapping_mod.build_plan(root, company_id=args.company, session_id=args.session_id)
    client = client_mod.PaperclipClient(
        args.base_url,
        token=args.token,
        company_id=args.company,
        run_id=args.run_id,
    )
    if args.dry_run:
        print(
            f"push(dry-run): would send {len(plan['tickets'])} ticket(s) to "
            f"{args.base_url}/api/companies/{args.company}/issues"
        )
        return 0
    sent = 0
    try:
        client.health()
        for ticket in plan["tickets"]:
            client.create_issue(ticket)
            sent += 1
    except PaperclipError as exc:
        print(f"push: FAILED after {sent} ticket(s) — {exc}", file=sys.stderr)
        return 1
    print(f"push: sent {sent} ticket(s) to {args.base_url}/api/companies/{args.company}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperclip-adapter",
        description="paperclip.ing integration adapter (issue #428, ADR-0013)",
    )
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    parser.add_argument("--company", default="purebliss", help="upstream company id")
    parser.add_argument("--session-id", default="unbound", help="minted session id")
    sub = parser.add_subparsers(dest="verb", required=True)

    plan = sub.add_parser("plan", help="print the offline dry-run sync plan")
    plan.add_argument("--markdown", action="store_true", help="markdown instead of JSON")
    plan.add_argument("--limit", type=int, default=20, help="tickets shown in markdown")
    plan.set_defaults(func=cmd_plan)

    check = sub.add_parser("check", help="validate the mapping against the seam schemas")
    check.set_defaults(func=cmd_check)

    push = sub.add_parser("push", help="send the plan to the upstream /api surface")
    push.add_argument("--base-url", required=True, help="upstream server root")
    push.add_argument("--token", default="", help="upstream agent key / JWT / board token")
    push.add_argument("--run-id", default=None, help="X-Paperclip-Run-Id for mutating calls")
    push.add_argument("--dry-run", action="store_true", help="print what would be sent")
    push.set_defaults(func=cmd_push)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
