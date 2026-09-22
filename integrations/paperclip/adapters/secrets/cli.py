"""CLI for the per-agent secret vault primitive (issue #417).

Read-only verbs over the reference/rotation view. There is deliberately **no
write verb**: writing a value is the store of record's business (Secret Manager,
under the caller's own IAM), and a view that could write would be the second
store this lane exists to refuse.

Exit-code contract (guardrails/honesty tri-state): 0 OK / 1 NOT-OK /
2 CANNOT-ASSESS. CANNOT-ASSESS is never reported as a pass.

Usage::

    python3 -m integrations.paperclip.adapters.secrets.cli validate [--view FILE]
    python3 -m integrations.paperclip.adapters.secrets.cli view
    python3 -m integrations.paperclip.adapters.secrets.cli catalog
    python3 -m integrations.paperclip.adapters.secrets.cli rotation
    python3 -m integrations.paperclip.adapters.secrets.cli orphans
    python3 -m integrations.paperclip.adapters.secrets.cli read --path GSM_PATH \\
        --principal ID [--scope SCOPE ...]

---knowledge---
module_id: integrations.paperclip.adapters.secrets.cli
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [cmd_validate, cmd_view, cmd_catalog, cmd_rotation, cmd_orphans, cmd_read, build_parser, main]
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
from typing import Any, List, Optional

from . import vault
from .model import Caller, SecretError


def _repo_root() -> Path:
    """The repository root — ``integrations/paperclip/adapters/secrets/cli.py`` -> four up."""
    return Path(__file__).resolve().parents[4]


def _load_view(args: argparse.Namespace, root: Path) -> Any:
    if args.view:
        return json.loads(Path(args.view).read_text(encoding="utf-8"))
    return vault.build_view(root)


def cmd_validate(args: argparse.Namespace, root: Path) -> int:
    if not (root / vault.SCHEMA_REL).exists():
        print(f"secrets: CANNOT-ASSESS — no view schema at {vault.SCHEMA_REL}", file=sys.stderr)
        return 2
    document = vault.load_catalog(root)
    try:
        view = _load_view(args, root)
    except SecretError as exc:
        print(f"  FAIL  {exc}", file=sys.stderr)
        return 1
    findings = vault.validate_view(view, root=root)
    findings.extend(vault.catalog_findings(document))
    if findings:
        for finding in findings:
            print(f"  FAIL  {finding}", file=sys.stderr)
        return 1
    count = len(view.get("secrets") or []) if isinstance(view, dict) else 0
    print(
        f"  OK    {count} secret(s): every secret names a GSM path, the store of record "
        f"'{vault.GSM_STORE}' is singular, no value is carried and every secret has a consumer"
    )
    return 0


def cmd_view(args: argparse.Namespace, root: Path) -> int:
    print(json.dumps(vault.build_view(root), indent=2, sort_keys=True))
    return 0


def cmd_catalog(args: argparse.Namespace, root: Path) -> int:
    document = vault.load_catalog(root)
    findings = vault.catalog_findings(document)
    if findings:
        for finding in findings:
            print(f"  FAIL  {finding}", file=sys.stderr)
        return 1
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


def cmd_rotation(args: argparse.Namespace, root: Path) -> int:
    report = vault.rotation_report(vault.load_catalog(root))
    for entry in report:
        state = entry["last_rotated_at"] if entry["rotated"] else "never-rotated"
        consumer = entry["consumer"] if entry["consumer"] else "NO-CONSUMER"
        print(f"  {entry['gsm_path']}  rotated_at={state}  consumer={consumer}")
    print(f"  OK    {len(report)} secret(s) with an expressible rotation state")
    return 0


def cmd_orphans(args: argparse.Namespace, root: Path) -> int:
    found = vault.orphans(vault.load_catalog(root))
    if not found:
        print("  OK    no orphaned secret: every declared secret has a consumer")
        return 0
    for gsm_path in found:
        print(f"  FAIL  {gsm_path}: no consumer — an orphaned secret is reported, not silently kept",
              file=sys.stderr)
    return 1


def cmd_read(args: argparse.Namespace, root: Path) -> int:
    caller = Caller.of(args.principal or "", args.scope or [])
    try:
        record = vault.read_secret_from_root(root, args.path, caller)
    except SecretError as exc:
        print(f"  FAIL  {exc}", file=sys.stderr)
        return 1
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperclip-secrets", description=__doc__)
    parser.add_argument("--root", default=None, help="repository root (default: inferred)")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate the view (0/1/2)")
    validate.add_argument("--view", default=None, help="validate this view file instead of the tree")
    validate.set_defaults(func=cmd_validate)

    for name, func in (("view", cmd_view), ("catalog", cmd_catalog),
                       ("rotation", cmd_rotation), ("orphans", cmd_orphans)):
        sub.add_parser(name, help=name).set_defaults(func=func)

    read = sub.add_parser("read", help="read one secret BY REFERENCE (never its value)")
    read.add_argument("--path", required=True, help="the GSM path of the secret")
    read.add_argument("--principal", default="", help="the authenticated caller id")
    read.add_argument("--scope", action="append", default=[], help="a scope the caller holds")
    read.set_defaults(func=cmd_read)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else _repo_root()
    try:
        return args.func(args, root)
    except SecretError as exc:
        print(f"secrets: FAIL — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
