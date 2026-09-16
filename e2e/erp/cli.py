"""``python3 -m e2e.erp.cli`` — the ERP end-to-end lane's own tri-state check.

The repository's tri-state contract (``guardrails/honesty``) is load-bearing here, and
it is not decoration: this lane's inputs are *other lanes'* declarations — ERP-01's
manifest, ERP-02's document model, ERP-03's indexer-resolved definitions, ERP-08's role
map, ERP-09's rate card. A declaration that will not load is **CANNOT-ASSESS** and not
NOT-OK: the lane cannot report on a module it could not read, and calling that a failure
would blame the module for the reader's problem.

``check`` measures four separable things, each able to fail on its own:

1. **the declarations load** — the module manifest through ERP-01's own loader, the
   document model, the indexer-resolved definitions, the ERP-08 catalogues and the
   ERP-09 workspace, each named on the way;
2. **the golden path passes** — the four stages of the tenant journey, with every
   failure the stages measured listed (not just the first);
3. **the golden path is deterministic** — the whole journey is run *twice* and the two
   runs must produce the same evidence digest. A journey whose evidence depends on when
   it ran cannot be asserted on, which is what makes the metered figures quotable;
4. **every negative control refuses, by name** — the flag OFF, the cross-tenant read,
   the budget-exhausted tenant, and the six sibling lanes' own drivers composed over one
   tree.

Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. ``demo`` prints the journey and
the controls as one JSON document; ``controls`` prints only the controls.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO

from e2e.erp.golden_path import REPO_ROOT, run_erp_golden_path, run_cycle
from e2e.erp.negative_controls import run_erp_negative_controls

OK = 0
NOT_OK = 1
CANNOT_ASSESS = 2

#: Where the lane's evidence lands when no ``--out`` is given. Gitignored, like the
#: gate's own attestation directory.
DEFAULT_RUN_DIR = Path(".verify") / "e2e-erp"


def _declarations(repo_root: Path, sink: TextIO) -> Optional[List[str]]:
    """Read every declaration this lane consumes. Returns problems, or None for CANNOT-ASSESS."""
    from integrations.erp.auth import policies as auth_policies
    from integrations.erp.auth import roles as auth_roles
    from integrations.erp.catalog.model import MANIFEST_PATH, CannotAssess, load_manifest
    from integrations.erp.core.validators import load_model
    from integrations.erp.finops import harness
    from integrations.erp.tx.definitions import load as load_definitions
    from integrations.erp.tx.model import Refused

    problems: List[str] = []
    try:
        manifest = load_manifest(repo_root / MANIFEST_PATH)
    except CannotAssess as refusal:
        print(f"  CANNOT-ASSESS  the module manifest: {refusal}", file=sink)
        return None
    print(
        f"  OK    the module manifest loads: id={manifest.get('id')} "
        f"mandatory={manifest.get('mandatory')} data_source={manifest.get('data_source')}",
        file=sink,
    )
    try:
        model = load_model()
    except Exception as exc:  # noqa: BLE001 - the model cannot be read
        print(f"  CANNOT-ASSESS  the ERP-02 document model: {type(exc).__name__}: {exc}", file=sink)
        return None
    print(
        f"  OK    the document model loads: {len(model.document_kinds())} kind(s), "
        f"{len(model.lifecycle_kinds())} with a lifecycle",
        file=sink,
    )
    try:
        definitions = load_definitions()
    except Refused as refusal:
        print(f"  CANNOT-ASSESS  the cycle definitions: {refusal.code}: {refusal.detail}", file=sink)
        return None
    print(
        f"  OK    the cycle resolves through the indexer: {' -> '.join(definitions.chain)} "
        f"from {len(definitions.links)} schema link(s), ledger {definitions.accounting_kind}",
        file=sink,
    )
    try:
        role_map = auth_roles.load_default()
        policy_set = auth_policies.load_default(kinds=role_map.kinds)
    except Refused as refusal:
        print(f"  CANNOT-ASSESS  the ERP-08 catalogues: {refusal.code}: {refusal.detail}", file=sink)
        return None
    print(
        f"  OK    the ERP-08 catalogues load: {len(role_map.kinds)} kind(s), "
        f"{len(role_map.role_names)} role(s), {len(policy_set.rules)} field rule(s)",
        file=sink,
    )
    try:
        workspace = harness.build_workspace()
    except Exception as exc:  # noqa: BLE001 - the metering workspace cannot be built
        print(f"  CANNOT-ASSESS  the ERP-09 workspace: {type(exc).__name__}: {exc}", file=sink)
        return None
    coverage = workspace.meter.coverage()
    print(
        f"  OK    the ERP-09 workspace loads: {len(workspace.policies)} budgeted tenant(s), "
        f"{len(workspace.meter.kinds())} kind(s) priced",
        file=sink,
    )
    for finding in coverage:
        problems.append(f"the rate card does not cover {finding.code}: {finding.detail}")
    return problems


def command_check(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    run_dir = Path(args.out) if args.out else DEFAULT_RUN_DIR
    problems: List[str] = []

    print("erp-e2e check", file=sink)
    print("== the declarations this lane consumes ==", file=sink)
    declared = _declarations(Path(args.repo_root), sink)
    if declared is None:
        print("erp-e2e check: CANNOT-ASSESS", file=err)
        return CANNOT_ASSESS
    problems.extend(declared)

    print("== the golden path, twice (determinism) ==", file=sink)
    first = run_erp_golden_path(work_dir=str(run_dir), repo_root=Path(args.repo_root))
    second = run_erp_golden_path(work_dir=str(run_dir / "second"), repo_root=Path(args.repo_root))
    for failure in first["failures"]:
        problems.append(f"golden path: {failure}")
    if first["digest"] != second["digest"]:
        problems.append(
            "the golden path is not deterministic: two runs produced evidence digests "
            f"{first['digest'][:16]} and {second['digest'][:16]}"
        )
    if first["passed"]:
        metering = first["stages"]["metering"]
        cycle = first["stages"]["cycle"]
        print(
            f"  OK    the cycle {' -> '.join(cycle['chain'])} posts "
            f"{cycle['ledger']['totals']['debits']:.2f} of debits against "
            f"{cycle['ledger']['totals']['credits']:.2f} of credits and moves "
            f"{abs(cycle['stock']['moved']):.0f} of stock",
            file=sink,
        )
        print(
            f"  OK    {metering['operations']} operation(s) metered onto the tenant's ledger "
            f"({metering['ledger']['verdict']['status']}), cost "
            f"{metering['rollup']['bill']['costUsd']:.4f} USD certified at chain seq "
            f"{metering['certifiedTail']['seq']}",
            file=sink,
        )
    if first["digest"] == second["digest"]:
        print(f"  OK    the golden path is deterministic (digest {first['digest'][:16]})", file=sink)

    print("== the negative controls ==", file=sink)
    cycle = run_cycle()
    controls = run_erp_negative_controls(work_dir=str(run_dir), repo_root=Path(args.repo_root), cycle=cycle)
    for control in controls["controls"]:
        label = "OK   " if control["passed"] else "FAIL "
        print(f"  {label} {control['controlId']} refused by {control['refusedBy']}", file=sink)
        print(f"        {control['detail']}", file=sink)
        if not control["passed"]:
            for failure in control["evidence"].get("failures", []):
                problems.append(f"{control['controlId']}: {failure}")
    composed = next(row for row in controls["controls"] if row["controlId"] == "sibling-refusals-composed")
    if controls["passed"]:
        print(
            f"  OK    {composed['evidence']['declaredTotal']} refusal(s) declared across "
            f"{len(composed['evidence']['lanes'])} sibling lane(s), all refused by name",
            file=sink,
        )

    for problem in problems:
        print(f"  FAIL  {problem}", file=sink)
    if problems:
        print(f"erp-e2e check: NOT-OK — {len(problems)} problem(s)", file=err)
        return NOT_OK
    print("erp-e2e check: OK", file=sink)
    return OK


def command_demo(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    run_dir = Path(args.out) if args.out else DEFAULT_RUN_DIR
    payload: Dict[str, Any] = {
        "goldenPath": run_erp_golden_path(work_dir=str(run_dir), repo_root=Path(args.repo_root)),
        "negativeControls": run_erp_negative_controls(
            work_dir=str(run_dir), repo_root=Path(args.repo_root), cycle=run_cycle()
        ),
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str), file=sink)
    passed = payload["goldenPath"]["passed"] and payload["negativeControls"]["passed"]
    return OK if passed else NOT_OK


def command_controls(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    run_dir = Path(args.out) if args.out else DEFAULT_RUN_DIR
    payload = run_erp_negative_controls(
        work_dir=str(run_dir), repo_root=Path(args.repo_root), cycle=run_cycle()
    )
    print(json.dumps(payload, indent=2, sort_keys=True, default=str), file=sink)
    return OK if payload["passed"] else NOT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m e2e.erp.cli",
        description="The ERP module end to end: the golden path and its refusals (issue #655).",
    )
    parser.add_argument(
        "--out",
        metavar="DIR",
        help=f"where the evidence documents land (default: {DEFAULT_RUN_DIR})",
    )
    parser.add_argument(
        "--repo-root",
        metavar="DIR",
        default=str(REPO_ROOT),
        help="the repository to measure (default: this checkout)",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check", help="the declarations, the journey twice, the controls")
    sub.add_parser("demo", help="the journey and the controls as one JSON document")
    sub.add_parser("controls", help="the negative controls alone")
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    sink: Optional[TextIO] = None,
    err: Optional[TextIO] = None,
) -> int:
    out = sink if sink is not None else sys.stdout
    errors = err if err is not None else sys.stderr
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.command:
        parser.print_help(file=out)
        return CANNOT_ASSESS
    if args.command == "check":
        return command_check(args, out, errors)
    if args.command == "demo":
        return command_demo(args, out, errors)
    return command_controls(args, out, errors)


if __name__ == "__main__":  # pragma: no cover - a manual entry point
    raise SystemExit(main())
