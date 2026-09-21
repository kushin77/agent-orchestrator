#!/usr/bin/env python3
"""Lessons CLI — RCA + lessons enforcement (issue #141).

---knowledge---
module_id: governance.lessons.cli
system: governance
app: lessons
solution_class: enterprise
patterns: [no-false-green, honesty-tri-state]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [cmd_check, cmd_status, cmd_linkage, cmd_record, cmd_template, build_parser, main]
invariants: ""
gotchas: ""
related: ["#141", "#1178"]
do_not_duplicate: null
---knowledge---

Honest tri-state exit codes (repo convention, GR-12 / no-false-green):

* ``0`` — OK
* ``1`` — NOT-OK (an enforcement error; deviations too under ``--strict``)
* ``2`` — CANNOT-ASSESS (no ledger, no board snapshot, not a git work tree)

Subcommands::

    check      run every enforcement rule (the gate of record)
    status     summarize the ledger without judging it
    linkage    census the ledger -> board linkage (issue #1178)
    record     append one validated record to the canonical ledger
    template   print the canonical RCA template

Examples::

    python3 governance/lessons/cli.py check
    python3 governance/lessons/cli.py check --strict
    python3 governance/lessons/cli.py linkage --orphans-only
    python3 governance/lessons/cli.py record --file /tmp/lesson.json
    python3 governance/lessons/cli.py status
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from checker import (  # noqa: E402
    LEDGER_RELPATH,
    POLICY_RELPATH,
    REPORT_RELPATH,
    SNAPSHOT_RELPATH,
    TEMPLATE_RELPATH,
    GitProbe,
    Ledger,
    LedgerUnavailable,
    PolicyUnavailable,
    check_ledger,
    load_ledger,
    load_policy,
    load_snapshot,
    parse_ledger_text,
    write_report,
)
import linkage as _linkage  # noqa: E402
from model import Entry, errors, validate_record, warnings  # noqa: E402

DEFAULT_ROOT = Path(_PKG_DIR).parent.parent

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2


def _print_findings(findings) -> None:
    if not findings:
        print("  (no findings)")
        return
    for finding in findings:
        print(
            "  %-5s %-34s %s"
            % (finding.severity.upper(), finding.code, finding.render())
        )


def _summary(report) -> str:
    counts = report.counts
    line = (
        "incidents: %d (%d closed) | rcas: %d | corrective actions: %d (%d open) | "
        "lessons: %d | suggestions: %d | board issues carrying the `incident` "
        "record label: %d"
        % (
            counts.get("incidents", 0),
            counts.get("incidents_closed", 0),
            counts.get("rcas", 0),
            counts.get("corrective_actions", 0),
            counts.get("corrective_actions_open", 0),
            counts.get("lessons", 0),
            counts.get("suggestions", 0),
            counts.get("board_incidents_scanned", 0),
        )
    )
    if "records" in counts:
        line += " | ledger records reaching the board: %d of %d (%d orphaned)" % (
            counts.get("with_issue", 0),
            counts.get("records", 0),
            counts.get("orphans", 0),
        )
    return line


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.root)
    try:
        ledger = load_ledger(root / LEDGER_RELPATH)
    except LedgerUnavailable as exc:
        print("lessons: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    probe = GitProbe(root)
    if not probe.available:
        print(
            "lessons: CANNOT-ASSESS — %s is not a git work tree, so a recorded "
            "artifact cannot be shown to be committed" % root,
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    snapshot_path = root / SNAPSHOT_RELPATH
    if not snapshot_path.is_file():
        print(
            "lessons: CANNOT-ASSESS — no board snapshot at %s" % snapshot_path,
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS
    try:
        snapshot = load_snapshot(snapshot_path)
    except (OSError, ValueError) as exc:
        print("lessons: CANNOT-ASSESS — unreadable board snapshot (%s)" % exc,
              file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    if not snapshot:
        print(
            "lessons: CANNOT-ASSESS — board snapshot holds no issues",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    policy_path = root / POLICY_RELPATH
    policy = None
    if policy_path.is_file():
        try:
            policy = load_policy(policy_path)
        except PolicyUnavailable as exc:
            print("lessons: NOT-OK — %s" % exc, file=sys.stderr)
            return EXIT_NOT_OK

    report = check_ledger(
        ledger,
        root=root,
        snapshot=snapshot,
        policy=policy,
        strict=args.strict,
        git=probe,
    )
    write_report(report, root / REPORT_RELPATH)

    print("ledger: %s" % LEDGER_RELPATH)
    print(_summary(report))
    _print_findings(report.findings)

    hard = errors(report.findings)
    soft = warnings(report.findings)
    if hard:
        print(
            "lessons: FAIL (%d error(s), %d deviation(s))"
            % (len(hard), len(soft)),
            file=sys.stderr,
        )
        return EXIT_NOT_OK
    print(
        "lessons: OK (%d incident(s), %d lesson(s) enforced, "
        "%d deviation(s) tracked)" % (
            report.counts.get("incidents", 0),
            report.counts.get("lessons", 0),
            len(soft),
        )
    )
    if soft:
        print(
            "  note: deviations are real and tracked; `--strict` fails the gate "
            "on them (report: %s)" % REPORT_RELPATH
        )
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.root)
    try:
        ledger = load_ledger(root / LEDGER_RELPATH)
    except LedgerUnavailable as exc:
        print("lessons: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    # The board snapshot is loaded when it is present, so the summary's holder
    # count is a measurement rather than a constant: without it
    # `board_incidents_scanned` is 0 by construction and the operator reads an
    # empty scope that is not there (issue #1178 measured exactly that: `status`
    # printed 0 while `check` measured 1).
    snapshot = None
    snapshot_path = root / SNAPSHOT_RELPATH
    if snapshot_path.is_file():
        try:
            snapshot = load_snapshot(snapshot_path)
        except (OSError, ValueError):
            snapshot = None
    report = check_ledger(ledger, root=root, snapshot=snapshot)
    print(_summary(report))
    if report.findings:
        print("  %d finding(s) in this ledger; run `check` for the gate verdict"
              % len(report.findings))
    return EXIT_OK


def cmd_linkage(args: argparse.Namespace) -> int:
    """Print the ledger -> board census, and where each record cannot reach."""
    root = Path(args.root)
    try:
        ledger = load_ledger(root / LEDGER_RELPATH)
    except LedgerUnavailable as exc:
        print("lessons: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    snapshot_path = root / SNAPSHOT_RELPATH
    if not snapshot_path.is_file():
        print("lessons: CANNOT-ASSESS — no board snapshot at %s" % snapshot_path,
              file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    try:
        snapshot = load_snapshot(snapshot_path)
    except (OSError, ValueError) as exc:
        print("lessons: CANNOT-ASSESS — unreadable board snapshot (%s)" % exc,
              file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    if not snapshot:
        print("lessons: CANNOT-ASSESS — board snapshot holds no issues",
              file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    records = list(ledger.records.values())
    rows = _linkage.linkage_map(records, snapshot)
    census = _linkage.counts(records, snapshot)

    print(
        "linkage: %d record(s); %d reach a board issue; %d of those reach a goal "
        "(epic or milestone); %d orphaned (%d explicitly declared)"
        % (
            census["records"],
            census["with_issue"],
            census["with_goal"],
            census["orphans"],
            census["orphans_declared"],
        )
    )
    print(
        "  board issues the ledger names as an incident origin: %d; carrying the "
        "`incident` label: %d"
        % (census["ledger_named_issues"], census["ledger_named_labelled"])
    )
    if args.orphans_only:
        rows = [row for row in rows if not row.reachable]
    print("  %-14s %-17s %-7s %-40s %s" % ("record", "kind", "issue", "goal", "path"))
    for row in rows:
        print(
            "  %-14s %-17s %-7s %-40s %s"
            % (
                row.id,
                row.kind,
                ("#%d" % row.issue) if row.issue is not None else "-",
                row.goal or "-",
                " -> ".join(row.path),
            )
        )
        if not row.reachable and row.orphan_declared:
            print("      declared orphan: %s" % (row.orphan_reason or "(no reason)"))
    return EXIT_OK


def cmd_record(args: argparse.Namespace) -> int:
    root = Path(args.root)
    ledger_path = root / LEDGER_RELPATH
    if args.file:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            print("lessons: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
            return EXIT_CANNOT_ASSESS
    else:
        text = sys.stdin.read()

    try:
        payload = json.loads(text)
    except ValueError as exc:
        print("lessons: NOT-OK — record does not parse as JSON (%s)" % exc,
              file=sys.stderr)
        return EXIT_NOT_OK
    if not isinstance(payload, dict):
        print("lessons: NOT-OK — a record is a single JSON object", file=sys.stderr)
        return EXIT_NOT_OK

    entry = Entry(line=0, raw="", record=payload)
    findings = validate_record(entry)
    if not findings:
        existing = Ledger(ledger_path, [], [])
        if ledger_path.is_file():
            existing = parse_ledger_text(ledger_path.read_text(encoding="utf-8"),
                                         ledger_path)
        if str(payload.get("id", "")) in existing.records:
            print(
                "lessons: NOT-OK — %s is already in the ledger (one authoritative "
                "line per id)" % payload.get("id"),
                file=sys.stderr,
            )
            return EXIT_NOT_OK
    if findings:
        _print_findings(findings)
        print(
            "lessons: NOT-OK — %d schema error(s); the record was not appended"
            % len(findings),
            file=sys.stderr,
        )
        return EXIT_NOT_OK

    line = json.dumps(payload, sort_keys=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print("lessons: OK — appended %s to %s" % (payload.get("id"), LEDGER_RELPATH))
    return EXIT_OK


def cmd_template(args: argparse.Namespace) -> int:
    path = Path(args.root) / TEMPLATE_RELPATH
    try:
        print(path.read_text(encoding="utf-8"), end="")
    except OSError as exc:
        print("lessons: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governance/lessons/cli.py",
        description="RCA and lessons enforcement (issue #141).",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="run every enforcement rule")
    p_check.add_argument(
        "--strict",
        action="store_true",
        help="escalate tracked deviations to errors",
    )
    p_check.set_defaults(func=cmd_check)

    p_status = sub.add_parser("status", help="summarize the ledger")
    p_status.set_defaults(func=cmd_status)

    p_linkage = sub.add_parser(
        "linkage", help="census the ledger -> board linkage for every record"
    )
    p_linkage.add_argument(
        "--orphans-only",
        action="store_true",
        help="print only the records that reach no board issue",
    )
    p_linkage.set_defaults(func=cmd_linkage)

    p_record = sub.add_parser("record", help="append one validated record")
    p_record.add_argument("--file", default=None, help="read the record from a file")
    p_record.set_defaults(func=cmd_record)

    p_template = sub.add_parser("template", help="print the canonical RCA template")
    p_template.set_defaults(func=cmd_template)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
