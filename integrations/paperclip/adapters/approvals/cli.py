#!/usr/bin/env python3
"""CLI for the approvals projection (issue #416).

Verbs:

* ``project``   — print every approval derived from the tree (canonical JSON).
* ``verify``    — re-read the authority behind every approval and refuse an
  inconsistency (a grant with no record, a double approval, an unauthorised
  decider). This is what the gate calls.
* ``authority`` — print the kind -> authority map; a kind with no authority
  behind it is refused by name.

Tri-state exit: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. The adapter is read-only:
none of these verbs writes the tree.

Run as ``python3 integrations/paperclip/adapters/approvals/cli.py <verb>`` from the repo root.

---knowledge---
module_id: integrations.paperclip.adapters.approvals.cli
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [cmd_project, cmd_verify, cmd_authority, build_parser, main]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from integrations.paperclip.adapters.approvals import mapping as mapping_mod  # noqa: E402
from integrations.paperclip.adapters.approvals import verify as verify_mod  # noqa: E402
from integrations.paperclip.adapters.approvals.model import (  # noqa: E402
    AUTHORITIES,
    KINDS,
    ApprovalRefused,
    authority_for,
)


def _root(args: argparse.Namespace) -> Path:
    if args.root:
        return Path(args.root).resolve()
    return Path(__file__).resolve().parents[4]


def cmd_project(args: argparse.Namespace) -> int:
    root = _root(args)
    projection = mapping_mod.project(root)
    sys.stdout.write(mapping_mod.render(projection))
    if projection.findings:
        print(f"project: NOT-OK — {len(projection.findings)} refusal(s)", file=sys.stderr)
        for finding in projection.findings:
            print(f"  FAIL  {finding.line()}", file=sys.stderr)
        return 1
    print(f"project: OK — {len(projection.approvals)} approval(s)", file=sys.stderr)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = _root(args)
    if not verify_mod.deterministic(root):
        print("verify: FAIL — two projections over one revision differ (non-deterministic)", file=sys.stderr)
        return 1
    projection = mapping_mod.project(root)
    findings = verify_mod.verify(root, projection)
    if findings:
        print(f"verify: NOT-OK — {len(findings)} finding(s)", file=sys.stderr)
        print(verify_mod.findings_summary(findings), file=sys.stderr)
        return 1
    granted = sum(1 for a in projection.approvals if a.state == "granted")
    denied = sum(1 for a in projection.approvals if a.state == "denied")
    pending = sum(1 for a in projection.approvals if a.state == "pending")
    print(
        f"verify: OK — {len(projection.approvals)} approval(s) "
        f"({granted} granted / {denied} denied / {pending} pending), each backed by its authority"
    )
    return 0


def cmd_authority(args: argparse.Namespace) -> int:
    if args.kind:
        refused = 0
        for kind in args.kind:
            try:
                authority_for(kind)
            except ApprovalRefused as exc:
                print(f"  REFUSED  {exc}", file=sys.stderr)
                refused += 1
        if refused:
            return 1
    print(json.dumps({k: AUTHORITIES[k].to_dict() for k in KINDS}, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperclip-approvals", description=__doc__)
    parser.add_argument("--root", default="", help="tree to project (default: the repo root)")
    sub = parser.add_subparsers(dest="verb", required=True)

    sub.add_parser("project", help="print the derived approvals").set_defaults(func=cmd_project)
    sub.add_parser("verify", help="re-check every approval against its authority").set_defaults(func=cmd_verify)

    authority = sub.add_parser("authority", help="print the kind -> authority map")
    authority.add_argument("kind", nargs="*", help="kind(s) to check for an authority (refused by name when absent)")
    authority.set_defaults(func=cmd_authority)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
