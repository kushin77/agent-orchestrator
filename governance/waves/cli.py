#!/usr/bin/env python3
"""Wave sync CLI — bootstrap report + wave ledger (issue #181).

---knowledge---
module_id: governance.waves.cli
system: governance
app: waves
solution_class: pattern
patterns: [no-false-green, honesty-tri-state, offline-hermetic]
derives_from: null
owner_sme: sync-sme
tier: L1
interfaces: [cmd_bootstrap, cmd_ledger_append, cmd_ledger_query, main]
invariants: ""
gotchas: ""
related: ["#181"]
do_not_duplicate: null
---knowledge---

Tri-state exit codes (repo convention, GR-12):

* ``0`` — OK (report written, record appended, query matched)
* ``1`` — NOT-OK (invalid input, or a query that matched nothing)
* ``2`` — CANNOT-ASSESS (missing pins in offline mode, no ledger to query)

Subcommands::

    bootstrap      --since <iso|sha> --out report.md [--online] [--pins path]
    ledger-append  --wave-id w1 --started-at ISO --finished-at ISO
                   --issues N --cost-usd X --escalations E --verify-failures F
                   [--pin deepseek=<sha>] [--pin codeidx-context-pack=<sha>]
                   [--direction <ref>] [--ledger path]
    ledger-query   [--wave-id w1] [--latest] [--ledger path]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootstrap  # noqa: E402
import ledger  # noqa: E402
from model import MODULE_PINS  # noqa: E402

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2


def _parse_pin(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(f"pin must be <module>=<sha>, got {value!r}")
    key, sha = value.split("=", 1)
    key = key.strip()
    sha = sha.strip()
    if key not in MODULE_PINS:
        raise argparse.ArgumentTypeError(
            f"unknown module pin {key!r} (expected one of {', '.join(MODULE_PINS)})"
        )
    if not sha:
        raise argparse.ArgumentTypeError(f"pin {key!r} has an empty SHA")
    return key, sha


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="waves", description="Wave sync tools (issue #181)")
    sub = parser.add_subparsers(dest="command", required=True)

    boot = sub.add_parser("bootstrap", help="write the wave-bootstrap report")
    boot.add_argument("--since", required=True, help="ISO timestamp or commit SHA the delta query starts from")
    boot.add_argument("--out", required=True, help="path to write the markdown report")
    boot.add_argument("--online", action="store_true", help="query the boards (network; never run by the gate)")
    boot.add_argument("--pins", default=str(bootstrap.DEFAULT_PINS_PATH), help="offline pin file (test seam)")

    append = sub.add_parser("ledger-append", help="append one wave record to the ledger")
    append.add_argument("--wave-id", required=True)
    append.add_argument("--started-at", default="")
    append.add_argument("--finished-at", default="")
    append.add_argument("--issues", type=int, default=0)
    append.add_argument("--cost-usd", type=float, default=0.0)
    append.add_argument("--escalations", type=int, default=0)
    append.add_argument("--verify-failures", type=int, default=0)
    append.add_argument("--pin", action="append", default=[], type=_parse_pin, metavar="MODULE=SHA")
    append.add_argument("--direction", action="append", default=[], metavar="REF")
    append.add_argument("--ledger", default=str(ledger.DEFAULT_LEDGER_PATH))

    query = sub.add_parser("ledger-query", help="query the wave ledger")
    query.add_argument("--wave-id", default=None)
    query.add_argument("--latest", action="store_true", help="print only the most recent match")
    query.add_argument("--ledger", default=str(ledger.DEFAULT_LEDGER_PATH))
    return parser


def cmd_bootstrap(args: argparse.Namespace) -> int:
    if args.online:
        try:
            out = bootstrap.build_report(args.since, args.out, online=True)
        except RuntimeError as exc:
            print(f"bootstrap: NOT-OK — {exc}", file=sys.stderr)
            return EXIT_NOT_OK
    else:
        try:
            out = bootstrap.build_report(args.since, args.out, pins_path=args.pins, online=False)
        except (ValueError, FileNotFoundError) as exc:
            print(f"bootstrap: CANNOT-ASSESS — {exc}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
    print(f"report: {out}")
    return EXIT_OK


def cmd_ledger_append(args: argparse.Namespace) -> int:
    try:
        pins: dict[str, str] = {}
        for key, sha in args.pin:
            pins[key] = sha
        record = ledger.build_record(
            wave_id=args.wave_id,
            started_at=args.started_at,
            finished_at=args.finished_at,
            issues=args.issues,
            cost_usd=args.cost_usd,
            escalations=args.escalations,
            verify_failures=args.verify_failures,
            module_pins=pins,
            direction_issues=args.direction,
        )
        path = ledger.append_record(record, args.ledger)
    except (ValueError, OSError) as exc:
        print(f"ledger-append: NOT-OK — {exc}", file=sys.stderr)
        return EXIT_NOT_OK

    print(json.dumps(record.to_json(), sort_keys=True))
    violations = ledger.slo_violations(record)
    if violations:
        for violation in violations:
            print(f"slo: {violation}", file=sys.stderr)
    else:
        print(f"ledger: {path} (within SLO)", file=sys.stderr)
    return EXIT_OK


def cmd_ledger_query(args: argparse.Namespace) -> int:
    ledger_path = Path(args.ledger)
    if not ledger_path.exists():
        print(f"ledger-query: CANNOT-ASSESS — {ledger_path} does not exist", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    try:
        records = ledger.read_ledger(ledger_path)
    except ValueError as exc:
        print(f"ledger-query: NOT-OK — {exc}", file=sys.stderr)
        return EXIT_NOT_OK

    matches = ledger.query(records, args.wave_id)
    if args.latest:
        matches = matches[:1]
    if not matches:
        print("ledger-query: no matching record", file=sys.stderr)
        return EXIT_NOT_OK
    print(json.dumps([record.to_json() for record in matches], indent=2, sort_keys=True))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "bootstrap":
        return cmd_bootstrap(args)
    if args.command == "ledger-append":
        return cmd_ledger_append(args)
    if args.command == "ledger-query":
        return cmd_ledger_query(args)
    parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
