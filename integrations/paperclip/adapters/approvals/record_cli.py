#!/usr/bin/env python3
"""CLI for the approval RECORD (issue #1272) — grant / check / list.

Distinct from ``cli.py`` (issue #416's read-only projection: project/verify/
authority). This is the write path: ``grant`` is what an operator's "approved"
in chat becomes; ``check`` is what a consumer (``scripts/merge-pr.sh``, the
branch sweep, a scheduler flip) calls before acting, and refuses by name
(``approval-missing:<scope>`` etc.) when there is no valid record.

Tri-state exit: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no HMAC key configured).

Run as ``python3 integrations/paperclip/adapters/approvals/record_cli.py <verb>``.

---knowledge---
module_id: integrations.paperclip.adapters.approvals.record_cli
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [cmd_grant, cmd_check, cmd_list, cmd_self_test, build_parser, main]
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
import tempfile
from pathlib import Path

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from integrations.paperclip.adapters.approvals import record as record_mod  # noqa: E402

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2
DEFAULT_TTL_SECONDS = 15 * 60


def cmd_grant(args: argparse.Namespace) -> int:
    try:
        rec = record_mod.grant(args.actor, args.scope, args.ttl_seconds)
    except record_mod.ApprovalKeyError as exc:
        print(f"grant: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return CANNOT_ASSESS
    except record_mod.ApprovalRefused as exc:
        print(f"grant: NOT-OK — {exc.code}: {exc.detail}", file=sys.stderr)
        return NOT_OK
    print(json.dumps(rec.to_dict(), indent=2, sort_keys=True))
    print(f"grant: OK — {rec.scope()} granted to {rec.actor}, expires {rec.expires}")
    return OK


def cmd_check(args: argparse.Namespace) -> int:
    try:
        rec = record_mod.check(args.scope, consume=not args.no_consume)
    except record_mod.ApprovalKeyError as exc:
        print(f"check: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return CANNOT_ASSESS
    except record_mod.ApprovalRefused as exc:
        print(f"approval-missing:{args.scope}" if exc.code == "approval-missing" else f"{exc.code}:{args.scope}", file=sys.stderr)
        print(f"check: NOT-OK — {exc.code}: {exc.detail}", file=sys.stderr)
        return NOT_OK
    print(json.dumps(rec.to_dict(), indent=2, sort_keys=True))
    print(f"check: OK — {rec.scope()} granted by {rec.actor}")
    return OK


def cmd_list(args: argparse.Namespace) -> int:
    records = record_mod.list_records()
    print(json.dumps([r.to_dict() for r in records], indent=2, sort_keys=True))
    print(f"list: OK — {len(records)} record(s)")
    return OK


def cmd_self_test(args: argparse.Namespace) -> int:
    """Provoke every named refusal on a scratch store, and prove a valid grant->check works."""
    key = b"self-test-key-not-a-secret"
    checks: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "approvals"

        rec = record_mod.grant("operator", "merge:pr#1", 300, store=store, key=key)
        checks.append(("valid-grant-signs", rec.verify_signature(key)))

        checked = record_mod.check("merge:pr#1", store=store, key=key)
        checks.append(("valid-check-accepted-once", checked.actor == "operator"))

        try:
            record_mod.check("merge:pr#1", store=store, key=key)
            checks.append(("approval-used-fires", False))
        except record_mod.ApprovalRefused as exc:
            checks.append(("approval-used-fires", exc.code == "approval-used"))

        try:
            record_mod.check("merge:pr#missing", store=store, key=key)
            checks.append(("approval-missing-fires", False))
        except record_mod.ApprovalRefused as exc:
            checks.append(("approval-missing-fires", exc.code == "approval-missing"))

        record_mod.grant("operator", "delete:branch-x", 1, store=store, key=key, now=0.0)
        try:
            record_mod.check("delete:branch-x", store=store, key=key, now=10.0)
            checks.append(("approval-expired-fires", False))
        except record_mod.ApprovalRefused as exc:
            checks.append(("approval-expired-fires", exc.code == "approval-expired"))

        record_mod.grant("operator", "flip:AO_FLAG", 300, store=store, key=key)
        path = store / "flip__AO_FLAG.json"
        data = json.loads(path.read_text())
        data["actor"] = "attacker"
        path.write_text(json.dumps(data))
        try:
            record_mod.check("flip:AO_FLAG", store=store, key=key)
            checks.append(("approval-tampered-fires", False))
        except record_mod.ApprovalRefused as exc:
            checks.append(("approval-tampered-fires", exc.code == "approval-tampered"))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'OK   ' if ok else 'FAIL '} {name}")
    if failed:
        print(f"self-test: NOT-OK — {len(failed)} control(s) did not fire: {failed}", file=sys.stderr)
        return NOT_OK
    print(f"self-test: OK — {len(checks)} control(s) proven")
    return OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperclip-approvals-record", description=__doc__)
    sub = parser.add_subparsers(dest="verb", required=True)

    grant_p = sub.add_parser("grant", help="create the record an 'approved' in chat becomes")
    grant_p.add_argument("--actor", required=True, help="who is granting (operator identity)")
    grant_p.add_argument("--scope", required=True, help="kind:target, e.g. merge:pr#123")
    grant_p.add_argument("--ttl-seconds", type=float, default=DEFAULT_TTL_SECONDS)
    grant_p.set_defaults(func=cmd_grant)

    check_p = sub.add_parser("check", help="refuse by name, or return the valid record")
    check_p.add_argument("--scope", required=True, help="kind:target, e.g. merge:pr#123")
    check_p.add_argument(
        "--no-consume", action="store_true", help="do not mark a merge-scope record used (inspection only)"
    )
    check_p.set_defaults(func=cmd_check)

    sub.add_parser("list", help="print every record in the store").set_defaults(func=cmd_list)
    sub.add_parser("self-test", help="provoke every refusal on a scratch store").set_defaults(func=cmd_self_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
