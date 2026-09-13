"""Operator CLI for the governance board enforcement gate (issue #143).

Usage:
    python3 governance/board/cli.py check
    python3 governance/board/cli.py exceptions

Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate import EXCEPTIONS_RELPATH, REPORT_RELPATH, load_exceptions, run_gate, write_report
from model import STATUS_CANNOT_ASSESS, STATUS_EXCEPTED, STATUS_NOT_OK, STATUS_OK, ExceptionInvalid


def _find_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return here.parents[2]


def cmd_check(args: argparse.Namespace) -> int:
    root = _find_root()
    report = run_gate(root)
    write_report(report, root / REPORT_RELPATH)

    for check in report.checks:
        marker = {
            STATUS_OK: "OK",
            STATUS_NOT_OK: "FAIL",
            STATUS_CANNOT_ASSESS: "CANNOT-ASSESS",
            STATUS_EXCEPTED: "EXCEPTED",
        }[check.status]
        line = "  [%s] %s (%s)" % (marker, check.name, check.command)
        if check.exception_applied:
            line += " — exception: %s" % check.exception_applied
        print(line)
        if check.status in (STATUS_NOT_OK, STATUS_CANNOT_ASSESS) and check.output_tail:
            for out_line in check.output_tail.splitlines():
                print("      %s" % out_line)

    if report.expired_exceptions:
        print(
            "board-gate: expired exception(s), no longer suppressing failures: %s"
            % ", ".join(report.expired_exceptions)
        )
    for esc in report.escalations:
        print(
            "board-gate: ESCALATION — %s failed %d time(s): %s"
            % (esc["check"], esc["occurrences"], esc["reason"])
        )

    print("board-gate: status=%s (report: %s)" % (report.status, REPORT_RELPATH))

    if report.status == STATUS_OK:
        return 0
    if report.status == STATUS_CANNOT_ASSESS:
        return 2
    return 1


def cmd_exceptions(args: argparse.Namespace) -> int:
    root = _find_root()
    try:
        exceptions = load_exceptions(root / EXCEPTIONS_RELPATH)
    except ExceptionInvalid as exc:
        print("board-gate: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return 2
    if not exceptions:
        print("no active exceptions declared")
        return 0
    for exc in exceptions:
        active = "active" if exc.is_active() else "EXPIRED"
        print(
            "%s: %s (approved by %s, expires %s, %s)"
            % (exc.check, exc.reason, exc.approved_by, exc.expires, active)
        )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="board-gate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("exceptions").set_defaults(func=cmd_exceptions)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
