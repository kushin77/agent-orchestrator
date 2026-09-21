#!/usr/bin/env python3
"""Remediation CLI — auto-generate violator remediation issues (issue #142).

---knowledge---
module_id: governance.remediation.cli
system: governance
app: remediation
solution_class: pattern
patterns: [no-false-green, honesty-tri-state, offline-hermetic, dry-run-default]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [now_iso, write_report, cmd_scan, cmd_route, build_parser, main]
invariants: ""
gotchas: ""
related: ["#140", "#142"]
do_not_duplicate: null
---knowledge---

Consumes `governance/conformance` findings (issue #140) — board conformance
and change-set mandate checks — and turns them into remediation issue
payloads, deduped/merged across repeated findings, routed to GitHub with
severity/SLA-based escalation to the governance board.

Honest tri-state exit codes (repo convention, GR-12 / no-false-green):

* ``0`` — OK (no violations, or all routed successfully)
* ``1`` — NOT-OK (violations found in ``scan --fail-on-findings``, or a
  routing call failed)
* ``2`` — CANNOT-ASSESS (no policy, no board snapshot, `gh` unavailable)

Subcommands::

    scan    run conformance checks, generate the remediation report (offline)
    route   scan, then create/update/escalate GitHub issues for the findings

Examples::

    python3 governance/remediation/cli.py scan
    python3 governance/remediation/cli.py scan --milestone "M24 - ..."
    python3 governance/remediation/cli.py route --repo kushin77/agent-orchestrator --apply

This is the scheduled-audit entry point (issue #142 acceptance criterion "a
violation scanner runs automatically on changes and on scheduled audits"): it
is a plain Makefile-invocable script, not a new GitHub Actions workflow (GR-15
keeps automation code-native) — wire it into an existing scheduled runner or
``make`` target rather than adding a workflow file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

_CONFORMANCE_DIR = os.path.join(os.path.dirname(_PKG_DIR), "conformance")
if _CONFORMANCE_DIR not in sys.path:
    sys.path.insert(0, _CONFORMANCE_DIR)

# governance/conformance and governance/remediation are both standalone
# modules (no package __init__.py); conformance's own domain module is
# `model.py` while this package's is `remediation_model.py` specifically so
# the two can be imported together in one process without a sys.modules
# collision on the bare name "model".
from checker import (  # noqa: E402
    POLICY_RELPATH,
    SNAPSHOT_RELPATH,
    PolicyUnavailable,
    check_board,
    load_policy,
    load_snapshot,
)
from generator import generate  # noqa: E402
from github import route  # noqa: E402
from remediation_model import RemediationReport  # noqa: E402

DEFAULT_ROOT = Path(_PKG_DIR).parent.parent
REPORT_RELPATH = Path(".verify") / "remediation-report.json"

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_report(report: RemediationReport, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def _scan(args: argparse.Namespace):
    """Run the board conformance check and generate remediation issues.

    Returns (exit_code, report_or_None).
    """
    try:
        policy = load_policy(args.root / POLICY_RELPATH)
    except PolicyUnavailable as exc:
        print("remediation: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS, None

    snapshot_path = args.root / SNAPSHOT_RELPATH
    if not snapshot_path.is_file():
        print(
            "remediation: CANNOT-ASSESS — no board snapshot at %s" % snapshot_path,
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS, None

    issues = load_snapshot(snapshot_path)
    if not issues:
        print("remediation: CANNOT-ASSESS — board snapshot holds no issues", file=sys.stderr)
        return EXIT_CANNOT_ASSESS, None

    conformance_report = check_board(
        issues,
        policy,
        milestone=args.milestone,
        include_unmilestoned=args.include_unmilestoned,
        strict=args.strict,
    )

    remediation_issues = generate(
        conformance_report.findings,
        policy_ref=str(POLICY_RELPATH),
        scope=args.scope,
        repo=args.repo or "",
    )

    report = RemediationReport(
        generated_at=now_iso(),
        scope=conformance_report.scope,
        scanned=conformance_report.scanned,
        issues=remediation_issues,
    )
    return EXIT_OK, report


def cmd_scan(args: argparse.Namespace) -> int:
    code, report = _scan(args)
    if report is None:
        return code

    write_report(report, args.root / REPORT_RELPATH)
    print(
        "scope: %s | scanned: %d | remediation issues: %d | escalated: %d"
        % (
            report.scope,
            report.scanned,
            len(report.issues),
            sum(1 for i in report.issues if i.escalate),
        )
    )
    for issue in report.issues:
        flag = " [ESCALATE]" if issue.escalate else ""
        print(
            "  %-8s %-10s %s%s" % (issue.severity.upper(), issue.owner_lane, issue.title, flag)
        )

    if args.fail_on_findings and report.issues:
        print(
            "remediation: FAIL (%d finding(s) require remediation)" % len(report.issues),
            file=sys.stderr,
        )
        return EXIT_NOT_OK
    print("remediation: OK (%d remediation issue(s) generated)" % len(report.issues))
    return EXIT_OK


def cmd_route(args: argparse.Namespace) -> int:
    code, report = _scan(args)
    if report is None:
        return code

    write_report(report, args.root / REPORT_RELPATH)

    if not args.repo:
        print("remediation: CANNOT-ASSESS — --repo is required to route issues", file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    try:
        results = route(report.issues, args.repo, dry_run=not args.apply)
    except RuntimeError as exc:
        print("remediation: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    for result in results:
        tag = " [escalated]" if result.escalated else ""
        number = "#%d" % result.number if result.number else "(dry-run)"
        print("  %-10s %-8s %s%s" % (result.action, number, result.key, tag))

    print(
        "remediation: OK (%d issue(s) routed%s)"
        % (len(results), "" if args.apply else ", dry-run — pass --apply to create/update")
    )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governance/remediation/cli.py",
        description="Automated violator remediation issue generation (issue #142).",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--milestone", default=None)
    common.add_argument("--include-unmilestoned", action="store_true")
    common.add_argument("--strict", action="store_true")
    common.add_argument("--scope", choices=("repo", "org"), default="repo")
    common.add_argument("--repo", default=None, help="owner/repo, e.g. kushin77/agent-orchestrator")

    p_scan = sub.add_parser("scan", parents=[common], help="generate the remediation report")
    p_scan.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="exit NOT-OK when any remediation issue is generated",
    )
    p_scan.set_defaults(func=cmd_scan)

    p_route = sub.add_parser(
        "route", parents=[common], help="scan, then create/update GitHub issues"
    )
    p_route.add_argument(
        "--apply",
        action="store_true",
        help="perform the gh calls (default is dry-run)",
    )
    p_route.set_defaults(func=cmd_route)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
