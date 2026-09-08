"""Isolation integrity CLI (issue #30).

    isolation scan <paths>... [--base ROOT] [--json FILE] [--strict]
        Static code scan for tenant-isolation anti-patterns (R1-R4).
    isolation check [--strict]
        Integrity self-check over this repo's tenant-scoped modules
        (registry/service, identity/rbac, gateway/mcp, engine/memory).
    isolation integrity <dataset.json> [--json FILE]
        Data-integrity scan of a tenant-scoped dataset (D1-D4).
    isolation cadence <dataset.json> [--json FILE]
        Runtime per-tenant cross-tenant read/write probes (AC1).
    isolation repair <dataset.json> [--apply] [--operator NAME] [--out FILE]
        Opt-in repair: dry-run plan by default; mutate only with --apply.
    isolation triage <findings.json>
        Security-finding triage gate: severity -> BLOCK / SME-reviewer / LOG.

Exit codes follow the guard-honesty contract (issue #28): 0 = OK, 1 =
NOT-OK (a finding / gate block), 2 = CANNOT-ASSESS (--strict self-check with
unmodeled surfaces; unrecoverable scan error).  Dry-run repair and triage of
non-blocking findings exit 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Make the guardrails/ namespace importable so `honesty` (tri-state) resolves
# when the CLI runs from the repo root or the lane directory.
_GUARDRAILS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _GUARDRAILS_ROOT not in sys.path:
    sys.path.insert(0, _GUARDRAILS_ROOT)

from . import __version__  # noqa: E402
from .integrity import (load_dataset, run_cadence_probes,  # noqa: E402
                        scan_dataset, store_from_dataset)
from .model import ScanReport  # noqa: E402
from .repair import plan_repairs, repair_execute  # noqa: E402
from .report import render_json, render_text  # noqa: E402
from .scanner import scan_paths  # noqa: E402
from .selfcheck import repo_root, self_check, self_check_exit  # noqa: E402
from .triage import TriageAction, partition, triage_finding  # noqa: E402
from .tristate import EXIT_CANNOT_ASSESS, EXIT_NOT_OK, EXIT_OK  # noqa: E402

_PARSER = None


def _write_json(path: str, report: ScanReport, title: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_json(report, title=title) + "\n")


def _cmd_scan(args: argparse.Namespace) -> int:
    base = args.base or os.getcwd()
    report = scan_paths(args.paths, base=base)
    print(render_text(report, title="Isolation code scan"))
    if args.json:
        _write_json(args.json, report, "Isolation code scan")
    if report.has_findings:
        return EXIT_NOT_OK
    if args.strict and report.aggregate().value == "CANNOT-ASSESS":
        return EXIT_CANNOT_ASSESS
    return EXIT_OK


def _cmd_check(args: argparse.Namespace) -> int:
    root = repo_root()
    print(f"self-check root: {root}")
    report = self_check(root)
    print(render_text(report, title="Isolation integrity self-check "
                                    "(tenant-scoped modules)"))
    if args.json:
        _write_json(args.json, report, "Isolation integrity self-check")
    return self_check_exit(report, strict=args.strict)


def _cmd_integrity(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.dataset)
    report = scan_dataset(dataset, target=os.path.basename(args.dataset))
    print(render_text(report, title=f"Dataset integrity scan: {args.dataset}"))
    if args.json:
        _write_json(args.json, report, "Dataset integrity scan")
    return EXIT_NOT_OK if report.has_findings else EXIT_OK


def _cmd_cadence(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.dataset)
    store = store_from_dataset(dataset)
    tenants = sorted(str(t) for t in dataset.get("tenants", {}))
    findings, outcome = run_cadence_probes(
        store, tenants, target=os.path.basename(args.dataset))
    report = ScanReport(findings=findings)
    print(render_text(report, title=f"Per-tenant cadence probes: "
                                    f"{args.dataset}"))
    print(f"probes run: {outcome.probes_run}   passed: {outcome.passed}   "
          f"failed: {outcome.failed}")
    if args.json:
        _write_json(args.json, report, "Per-tenant cadence probes")
    return EXIT_NOT_OK if findings else EXIT_OK


def _cmd_repair(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.dataset)
    actions = plan_repairs(dataset)
    print(f"Repair plan for {args.dataset} ({len(actions)} action(s)) — "
          f"{'DRY-RUN (no mutation)' if not args.apply else 'APPLY'}")
    if args.apply and not actions:
        print("  nothing to repair")
    for action in actions:
        print(f"  [{action.action}] {action.from_tenant}/{action.record_id} "
              f"-> {action.to_tenant}   ({action.reason})")
    if not args.apply:
        print("no changes applied; pass --apply to execute (opt-in repair)")
        return EXIT_OK
    try:
        repaired = repair_execute(dataset, operator=args.operator)
    except Exception as exc:  # RepairAbortError and friends
        print(f"repair ABORTED (all-or-nothing; input untouched): {exc}")
        return EXIT_NOT_OK
    out = args.out or args.dataset
    with open(out + ".tmp", "w", encoding="utf-8") as handle:
        json.dump(repaired, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(out + ".tmp", out)
    print(f"repair committed to {out}; "
          f"{len(repaired.get('audit', []))} audit entr(y/ies)")
    return EXIT_OK


def _cmd_triage(args: argparse.Namespace) -> int:
    with open(args.findings, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and "findings" in payload:
        payload = payload["findings"]
    from .report import finding_from_dict
    findings = [finding_from_dict(item) for item in payload]
    buckets = partition(findings)
    print("Triage gate")
    for action in (TriageAction.BLOCK, TriageAction.SME_REVIEW,
                   TriageAction.LOG):
        print(f"  {action.value:>10}: {len(buckets[action])}")
        for finding in buckets[action]:
            print(f"      [{finding.rule_id}] {finding.severity.value.upper()} "
                  f"{finding.target} :: {finding.scope or '-'}")
    if buckets[TriageAction.BLOCK]:
        print("gate: BLOCK (auto-blocking findings present)")
        return EXIT_NOT_OK
    print("gate: no auto-blocking findings")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="isolation",
        description="Tenant isolation integrity — detect-first / opt-in "
                    "repair (issue #30).",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="static code anti-pattern scan")
    p_scan.add_argument("paths", nargs="+")
    p_scan.add_argument("--base", default="")
    p_scan.add_argument("--json", default="")
    p_scan.add_argument("--strict", action="store_true")
    p_scan.set_defaults(func=_cmd_scan)

    p_check = sub.add_parser("check",
                             help="self-check of tenant-scoped modules")
    p_check.add_argument("--json", default="")
    p_check.add_argument("--strict", action="store_true")
    p_check.set_defaults(func=_cmd_check)

    p_integ = sub.add_parser("integrity",
                             help="scan a tenant-scoped dataset (D1-D4)")
    p_integ.add_argument("dataset")
    p_integ.add_argument("--json", default="")
    p_integ.set_defaults(func=_cmd_integrity)

    p_cad = sub.add_parser("cadence",
                           help="per-tenant cross-tenant runtime probes")
    p_cad.add_argument("dataset")
    p_cad.add_argument("--json", default="")
    p_cad.set_defaults(func=_cmd_cadence)

    p_rep = sub.add_parser("repair",
                           help="opt-in repair (dry-run default)")
    p_rep.add_argument("dataset")
    p_rep.add_argument("--apply", action="store_true",
                       help="execute the repair (the only mutation path)")
    p_rep.add_argument("--operator", default="system")
    p_rep.add_argument("--out", default="")
    p_rep.set_defaults(func=_cmd_repair)

    p_tri = sub.add_parser("triage", help="security-finding triage gate")
    p_tri.add_argument("findings")
    p_tri.set_defaults(func=_cmd_triage)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
