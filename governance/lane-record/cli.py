#!/usr/bin/env python3
"""The lane record's verb surface (issue #1270, EPIC #1268).

---knowledge---
module_id: governance.lane-record.cli
system: governance
app: lane-record
solution_class: pattern
patterns: [provoked-negative-control]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [cmd_runtimes, cmd_validate, cmd_evaluate, cmd_controls, build_parser, main]
invariants: ""
gotchas: ""
related: ["#1268", "#1270"]
do_not_duplicate: null
---knowledge---

    runtimes    the DERIVED registered-runtime set, and why each is one
    validate    one record against the frozen shape and the runtime vocabulary
    evaluate    every record in a tree: shape, vocabulary, then the pair relation
    controls    the declaration in controls.yaml against the code it describes

``evaluate`` and ``validate`` write nothing, so the gate can drive them against
the live tree AND against a scratch records tree without either run touching the
other. Nothing here writes a record: a record is written by the runtime that
holds the lane, and this module is what decides whether the one it wrote is
readable, in scope and paired.

Exit contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. An unreadable schema, an
unreadable runtime registry, an unreadable declaration or a records directory
that cannot be read is CANNOT-ASSESS, never a pass.

Usage:
    python3 governance/lane-record/cli.py runtimes
    python3 governance/lane-record/cli.py validate --file <record.json>
    python3 governance/lane-record/cli.py evaluate [--records <dir>]
    python3 governance/lane-record/cli.py controls
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

import controls  # noqa: E402
import lane_record  # noqa: E402


def _emit(findings, as_json: bool, summary: str, rc: int) -> int:
    if as_json:
        print(
            json.dumps(
                {
                    "summary": summary,
                    "rc": rc,
                    "findings": [
                        {"code": item.code, "subject": item.subject, "reason": item.reason}
                        for item in findings
                    ],
                },
                indent=1,
                sort_keys=True,
            )
        )
        return rc
    for item in findings:
        print(item.line(), file=sys.stderr)
    print(summary)
    return rc


def cmd_runtimes(args: argparse.Namespace) -> int:
    ids = lane_record.runtime_ids(args.root)
    summary = "lane-record: runtimes=%d -- %s" % (len(ids), ", ".join(ids))
    if args.json:
        print(json.dumps({"summary": summary, "runtimes": list(ids)}, indent=1))
        return 0
    print(summary)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    schema = lane_record.load_schema(args.schema)
    runtimes = lane_record.runtime_ids(args.root)
    path = Path(args.file)
    document, findings = lane_record.read_one(path)
    if document is None:
        return _emit(findings, args.json, "lane-record: validate %s unreadable" % path, 1)
    findings = findings + lane_record.validate_record(document, schema, runtimes, args.root)
    rc = 1 if findings else 0
    summary = "lane-record: validate %s kind=%s findings=%d" % (
        path,
        document.get("kind"),
        len(findings),
    )
    return _emit(findings, args.json, summary, rc)


def cmd_evaluate(args: argparse.Namespace) -> int:
    schema = lane_record.load_schema(args.schema)
    runtimes = lane_record.runtime_ids(args.root)
    records = Path(args.records)
    if not getattr(args, "records_explicit", True) and not records.is_dir():
        # NAMED BOUNDARY: a checkout where no lane has written a record yet has no
        # <fleet>/lane-records. That is zero lanes in flight, reported WITH its
        # count so an empty tree cannot look like a tree nobody opened; a records
        # tree that was NAMED and cannot be read is CANNOT-ASSESS instead.
        summary = (
            "lane-record: records=0 briefs=0 results=0 pairs=0 runtimes=%d "
            "-- no lane record under %s, so no lane is in flight"
            % (len(runtimes), records)
        )
        return _emit((), args.json, summary, 0)
    report = lane_record.evaluate(records, schema, runtimes, args.root)
    return _emit(report.findings, args.json, report.summary(), report.rc)


def cmd_controls(args: argparse.Namespace) -> int:
    declaration = controls.load(args.controls)
    findings = controls.problems(declaration, args.root)
    view = controls.describe(declaration)
    summary = "lane-record-controls: version=%s kinds=%d limits=%d refusals=%d findings=%d" % (
        view["version"],
        len(view["kinds"]),
        view["limits"],
        view["refusals"],
        len(findings),
    )
    return _emit(findings, args.json, summary, 1 if findings else 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lane-record", description=__doc__)
    parser.add_argument("--root", default=str(lane_record.ROOT))
    parser.add_argument(
        "--fleet",
        default="",
        help="the fleet runtime-state directory; defaults to $AO_FLEET_DIR, else <root>/.fleet",
    )
    parser.add_argument("--schema", default="", help="another frozen schema, for a provocation")
    parser.add_argument("--json", action="store_true")

    verbs = parser.add_subparsers(dest="verb", required=True)
    runtimes = verbs.add_parser("runtimes", help="print the derived registered-runtime set")
    runtimes.add_argument("--json", action="store_true")
    runtimes.set_defaults(func=cmd_runtimes)

    validate = verbs.add_parser("validate", help="one record against the frozen shape")
    validate.add_argument("--file", required=True)
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_validate)

    evaluate = verbs.add_parser("evaluate", help="every record in a tree (read-only)")
    evaluate.add_argument("--records", default="", help="the records tree; defaults to <fleet>/lane-records")
    evaluate.add_argument("--json", action="store_true")
    evaluate.set_defaults(func=cmd_evaluate)

    controls_parser = verbs.add_parser("controls", help="the declaration against the code")
    controls_parser.add_argument("--controls", default="", help="another declaration, for a provocation")
    controls_parser.add_argument("--json", action="store_true")
    controls_parser.set_defaults(func=cmd_controls)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.records_explicit = bool(getattr(args, "records", ""))
    if not args.records_explicit:
        fleet = args.fleet or os.environ.get("AO_FLEET_DIR") or ""
        args.records = str(lane_record.records_dir(fleet or None, args.root))
    try:
        return args.func(args)
    except lane_record.CannotAssess as exc:
        print("lane-record: CANNOT-ASSESS -- %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
