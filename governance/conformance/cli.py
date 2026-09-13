#!/usr/bin/env python3
"""Conformance CLI — CMR class / pattern / template enforcement (issue #140).

Checks the board (is in-scope work classified, and does the declared class hold?)
and a change set (does the work honour the cross-cutting mandates?).

Honest tri-state exit codes (repo convention, GR-12 / no-false-green):

* ``0`` — OK
* ``1`` — NOT-OK (a conformance error, or deviations under ``--strict``)
* ``2`` — CANNOT-ASSESS (no policy, no snapshot, not a git work tree)

Subcommands::

    check        classify the board and report findings (the gate of record)
    change-set   check the current diff against the mandates
    policy       print the declared policy
    report       write .verify/conformance-report.json without failing

Examples::

    python3 governance/conformance/cli.py check
    python3 governance/conformance/cli.py check --milestone "M24 - ..." --strict
    python3 governance/conformance/cli.py change-set --base origin/master
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from checker import (  # noqa: E402
    POLICY_RELPATH,
    REPORT_RELPATH,
    SNAPSHOT_RELPATH,
    SUITES_RELPATH,
    PolicyUnavailable,
    check_board,
    check_change_set,
    load_policy,
    load_snapshot,
    missing_suite_registration,
    write_report,
)
from model import errors, warnings  # noqa: E402

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
            "  %-7s %-28s %s"
            % (finding.severity.upper(), finding.code, finding.message)
        )


def _git(root: Path, *args: str) -> tuple:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return (-1, "")
    return (out.returncode, out.stdout)


def _resolve_base(root: Path, base: str) -> str:
    code, out = _git(root, "merge-base", base, "HEAD")
    if code == 0 and out.strip():
        return out.strip()
    return base


def cmd_check(args: argparse.Namespace) -> int:
    try:
        policy = load_policy(args.root / POLICY_RELPATH)
    except PolicyUnavailable as exc:
        print("conformance: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    snapshot_path = args.root / SNAPSHOT_RELPATH
    if not snapshot_path.is_file():
        print(
            "conformance: CANNOT-ASSESS — no board snapshot at %s"
            % snapshot_path,
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    issues = load_snapshot(snapshot_path)
    if not issues:
        print(
            "conformance: CANNOT-ASSESS — board snapshot holds no issues",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    report = check_board(
        issues,
        policy,
        milestone=args.milestone,
        include_unmilestoned=args.include_unmilestoned,
        strict=args.strict,
        generated_at=None,
    )
    write_report(report, args.root / REPORT_RELPATH)

    print(
        "scope: %s | scanned: %d | classes: %s"
        % (
            report.scope,
            report.scanned,
            ", ".join("%s=%d" % kv for kv in sorted(report.class_counts.items())) or "(none)",
        )
    )
    _print_findings(report.findings)

    hard = errors(report.findings)
    if hard:
        print(
            "conformance: FAIL (%d error(s), %d warning(s))"
            % (len(hard), len(warnings(report.findings))),
            file=sys.stderr,
        )
        return EXIT_NOT_OK
    print(
        "conformance: OK (%d item(s) conform, %d deviation(s) reported)"
        % (report.scanned, len(warnings(report.findings)))
    )
    return EXIT_OK


def cmd_change_set(args: argparse.Namespace) -> int:
    try:
        policy = load_policy(args.root / POLICY_RELPATH)
    except PolicyUnavailable as exc:
        print("conformance: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    base = _resolve_base(args.root, args.base)
    code, out = _git(args.root, "diff", "--name-only", "%s...HEAD" % base)
    if code != 0:
        print(
            "conformance: CANNOT-ASSESS — cannot diff against %s" % base,
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    changed = [line.strip() for line in out.splitlines() if line.strip()]
    _, added_out = _git(args.root, "diff", "--name-only", "--diff-filter=A",
                        "%s...HEAD" % base)
    added = [line.strip() for line in added_out.splitlines() if line.strip()]

    findings = check_change_set(changed, policy, added=added, root=args.root)
    suites_path = args.root / SUITES_RELPATH
    suite_lines = (
        suites_path.read_text(encoding="utf-8").splitlines()
        if suites_path.is_file()
        else []
    )
    findings.extend(missing_suite_registration(changed, suite_lines))

    print("base: %s | changed: %d | added: %d" % (base[:12], len(changed), len(added)))
    _print_findings(findings)

    hard = errors(findings)
    if hard:
        print("conformance: FAIL (%d error(s))" % len(hard), file=sys.stderr)
        return EXIT_NOT_OK
    print("conformance: OK (change set honours the mandates)")
    return EXIT_OK


def cmd_policy(args: argparse.Namespace) -> int:
    try:
        policy = load_policy(args.root / POLICY_RELPATH)
    except PolicyUnavailable as exc:
        print("conformance: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    print(json.dumps(policy.as_dict(), indent=2, sort_keys=True))
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    """Write the report and never fail — for board reporting."""
    namespace = argparse.Namespace(**vars(args))
    namespace.strict = False
    return cmd_check(namespace)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governance/conformance/cli.py",
        description="CMR class/pattern/template conformance (issue #140).",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="classify the board and report")
    p_check.add_argument("--milestone", default=None)
    p_check.add_argument("--include-unmilestoned", action="store_true")
    p_check.add_argument(
        "--strict",
        action="store_true",
        help="escalate declared-class deviations to errors",
    )
    p_check.set_defaults(func=cmd_check)

    p_change = sub.add_parser("change-set", help="check the diff against mandates")
    p_change.add_argument("--base", default="origin/master")
    p_change.set_defaults(func=cmd_change_set)

    p_policy = sub.add_parser("policy", help="print the declared policy")
    p_policy.set_defaults(func=cmd_policy)

    p_report = sub.add_parser("report", help="write the report only")
    p_report.add_argument("--milestone", default=None)
    p_report.add_argument("--include-unmilestoned", action="store_true")
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
