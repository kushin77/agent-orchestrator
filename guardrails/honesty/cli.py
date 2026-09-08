"""Honesty CLI (issue #28).

Subcommands:

    honesty status <exit-code>
        Map a guard exit code to its tri-state status (0 -> OK, 1 -> NOT-OK,
        2/124/other -> CANNOT-ASSESS).

    honesty aggregate <status>...
        Aggregate guard statuses; any NOT-OK fails, any CANNOT-ASSESS keeps
        the aggregate from passing, all-OK passes.

    honesty analyze <path>... [--strict]
        Run the anti-formality/self-match scanner over shell guards.  Review
        aid by default (exit 0, findings printed); with --strict it exits 1 on
        any unsuppressed finding.

    honesty negative <manifest.yaml> [--report out.json]
        Run the negative/positive/CANNOT-ASSESS controls in the manifest and
        exit nonzero if any control fails (a guard that cannot fail fails its
        own negative control).

    honesty attest --guard-id G --rc N --evidence E [--controls c1,c2] [-o f]
        Record a guard verdict as an attestation JSON document (evidence, not
        vibes).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .analyzer import analyze as run_analyzer
from .attestation import GuardAttestation
from .negative_control import (
    exit_code_for_outcomes,
    load_manifest,
    run_controls,
    write_report,
)
from .tristate import aggregate, from_exit_code, parse, serialize, to_exit_code


def _cmd_status(args: argparse.Namespace) -> int:
    print(from_exit_code(int(args.exit_code)).value)
    return 0


def _cmd_aggregate(args: argparse.Namespace) -> int:
    states = [parse(s) for s in args.statuses]
    result = aggregate(states)
    print(result.value)
    # A gate whose aggregate is NOT-OK or CANNOT-ASSESS must exit nonzero.
    return to_exit_code(result)


def _cmd_analyze(args: argparse.Namespace) -> int:
    result = run_analyzer(args.paths)
    for finding in result.findings:
        loc = f"{finding.path}:{finding.line}"
        print(f"[{finding.rule}] {loc}: {finding.text}")
        if finding.detail:
            print(f"    {finding.detail}")
    if result.findings:
        print(
            f"{len(result.findings)} formality finding(s) across "
            f"{result.files} file(s) ({result.suppressed} suppressed)",
            file=sys.stderr,
        )
    else:
        print(
            f"no formalities found across {result.files} file(s) "
            f"({result.suppressed} suppressed)"
        )
    if args.strict and result.findings:
        return 1
    return 0


def _cmd_negative(args: argparse.Namespace) -> int:
    manifest_abs = os.path.abspath(args.manifest)
    base = os.path.dirname(manifest_abs)
    controls = load_manifest(manifest_abs)
    outcomes = run_controls(controls, cwd=base)
    for outcome in outcomes:
        mark = "PASS" if outcome.passed else "FAIL"
        print(
            f"[{mark}] {outcome.control_id}: guard={outcome.guard} "
            f"verdict={outcome.verdict.value} (exit {outcome.exit_code}) "
            f"expected={outcome.expected.value}"
        )
    if args.report:
        write_report(outcomes, args.report)
        print(f"blockproof report: {args.report}")
    return exit_code_for_outcomes(outcomes)


def _cmd_attest(args: argparse.Namespace) -> int:
    controls = [c.strip() for c in (args.controls or "").split(",") if c.strip()]
    attestation = GuardAttestation.record(
        guard_id=args.guard_id,
        exit_code=int(args.rc),
        evidence=args.evidence,
        provenance=args.provenance,
        controls=controls,
    )
    document = attestation.to_json()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(document)
            fh.write("\n")
        print(f"attestation: {args.out}")
    else:
        print(document)
    if not attestation.attested:
        print("WARNING: attestation is not evidence (CANNOT-ASSESS)", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="honesty",
        description="Guard honesty: tri-state status, no-false-green, "
        "negative controls, attestation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="map an exit code to its tri-state")
    p_status.add_argument("exit_code", help="guard exit code (0/1/2/124/...)")
    p_status.set_defaults(func=_cmd_status)

    p_agg = sub.add_parser("aggregate", help="aggregate guard statuses")
    p_agg.add_argument("statuses", nargs="+", help="statuses, e.g. OK NOT-OK")
    p_agg.set_defaults(func=_cmd_aggregate)

    p_an = sub.add_parser("analyze", help="anti-formality/self-match scan")
    p_an.add_argument("paths", nargs="+", help="files or directories to scan")
    p_an.add_argument(
        "--strict", action="store_true", help="exit 1 on any unsuppressed finding"
    )
    p_an.set_defaults(func=_cmd_analyze)

    p_neg = sub.add_parser("negative", help="run negative-control manifest")
    p_neg.add_argument("manifest", help="path to the negative-control YAML manifest")
    p_neg.add_argument(
        "--report", default=None, help="write blockproof JSON report to this path"
    )
    p_neg.set_defaults(func=_cmd_negative)

    p_att = sub.add_parser("attest", help="record a guard verdict as attestation")
    p_att.add_argument("--guard-id", required=True)
    p_att.add_argument("--rc", required=True, help="guard exit code")
    p_att.add_argument("--evidence", required=True, help="actual guard output/proof")
    p_att.add_argument("--provenance", default="")
    p_att.add_argument("--controls", default="", help="comma-separated control ids")
    p_att.add_argument("-o", "--out", default=None, help="output JSON path")
    p_att.set_defaults(func=_cmd_attest)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
