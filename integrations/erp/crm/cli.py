"""The offline CLI for the CRM-family lane (issue #650): `check`, `demo`, `definitions`.

Exit contract, the repository's tri-state convention (``guardrails/honesty``):

* ``0`` **OK** — every invariant measured and satisfied;
* ``1`` **NOT-OK** — a measured invariant is violated, named on stderr;
* ``2`` **CANNOT-ASSESS** — the question cannot be answered (a declaration or a
  harvest record will not load). Never a pass: a lane that cannot read its own
  declarations cannot report on them, and reporting OK would be the false green
  this repository's doctrine forbids.

``check`` measures four things a suite alone would not: that the shipped
declaration set covers every kind this module owns, that the inspection outcome
vocabulary the code carries is the one the declaration declares, that the golden
path is **deterministic** (two runs, byte-identical audit head and rollup), and
that every refusal this module can raise is provoked and refused by name
(``negative_control``). ``demo`` prints the scenario; ``definitions`` prints the
validated declaration set.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional, Sequence, TextIO

from . import negative_control, provenance
from .definitions import DefinitionSet, load as load_definitions
from .flows import INSPECTION_OUTCOMES, GoldenPath, golden_path
from .model import KIND_INSPECTION, KINDS, Refused

CANNOT_ASSESS = 2
NOT_OK = 1
OK = 0


def _report(lines: Sequence[str], sink: TextIO) -> None:
    for line in lines:
        print(line, file=sink)


def _load(path: Optional[str], what: str, sink: TextIO) -> Optional[DefinitionSet]:
    """Load the declaration set, reporting CANNOT-ASSESS and returning None when it will not load."""
    try:
        loaded = load_definitions(path) if path else load_definitions()
    except Refused as refusal:
        print(f"  CANNOT-ASSESS  {what}: {refusal.detail}", file=sink)
        return None
    print(f"  OK    {what} loaded ({len(loaded.kinds)} kind(s), {loaded.source})", file=sink)
    return loaded


def _check_coverage(definitions: DefinitionSet, sink: TextIO) -> List[str]:
    """The declaration set must cover every kind this module owns and the outcomes it carries."""
    failures: List[str] = []
    for kind in KINDS:
        if not definitions.has_kind(kind):
            failures.append(f"the declaration set omits the kind {kind!r}")
    declared_outcomes = tuple(
        state
        for state in definitions.kind(KIND_INSPECTION).states
        if state in INSPECTION_OUTCOMES
    )
    if tuple(sorted(declared_outcomes)) != tuple(sorted(INSPECTION_OUTCOMES)):
        failures.append(
            "the inspection outcomes this module carries "
            f"({', '.join(INSPECTION_OUTCOMES)}) are not the ones declared "
            f"({', '.join(declared_outcomes) or 'none'})"
        )
    if not failures:
        print(
            f"  OK    declaration coverage: {len(KINDS)} kind(s), inspection outcomes "
            f"{', '.join(sorted(INSPECTION_OUTCOMES))}",
            file=sink,
        )
    return failures


def _check_golden_path(definitions: DefinitionSet, sink: TextIO) -> List[str]:
    """Run the scenario twice and require it to satisfy its invariants, identically."""
    failures: List[str] = []
    first: GoldenPath = golden_path("check", definitions)
    second: GoldenPath = golden_path("check", definitions)
    if first.findings:
        failures.extend(
            f"golden path: {finding.code}: {finding.detail}" for finding in first.findings
        )
    if first.workspace.rail.head != second.workspace.rail.head:
        failures.append(
            "the golden path is not deterministic: two runs produced different audit heads "
            f"({first.workspace.rail.head[:12]} and {second.workspace.rail.head[:12]})"
        )
    if first.rollup.to_dict() != second.rollup.to_dict():
        failures.append("the golden path is not deterministic: two runs produced different rollups")
    if not failures:
        print(
            f"  OK    golden path: {len(first.workspace.documents)} document(s), "
            f"{len(first.workspace.rail)} audit entr(ies), head {first.workspace.rail.head[:12]}, "
            f"rollup {first.rollup.minutes} min / {first.rollup.amount_minor} "
            f"{first.rollup.currency} minor",
            file=sink,
        )
    return failures


def _check_negative_controls(sink: TextIO) -> List[str]:
    """Every refusal this module can raise must be provoked and refused by name."""
    driver = negative_control.run(sink)
    if driver != 0:
        return ["negative-control: one or more refusals were not provoked"]
    return []


def command_check(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    print("erp-crm check", file=sink)
    definitions = _load(args.definitions, "definition set", sink)
    if definitions is None:
        return CANNOT_ASSESS
    try:
        harvest = provenance.load(args.provenance) if args.provenance else provenance.load()
    except Refused as refusal:
        print(f"  CANNOT-ASSESS  harvest record: {refusal.detail}", file=sink)
        return CANNOT_ASSESS
    print(
        f"  OK    harvest record: {len(harvest.harvests)} shape(s) under {harvest.policy}",
        file=sink,
    )

    failures: List[str] = []
    failures.extend(_check_coverage(definitions, sink))
    failures.extend(_check_golden_path(definitions, sink))
    failures.extend(_check_negative_controls(sink))

    if failures:
        print(f"erp-crm check: NOT-OK — {len(failures)} problem(s)", file=err)
        for failure in failures:
            print(f"  FAIL  {failure}", file=err)
        return NOT_OK
    print("erp-crm check: OK", file=sink)
    return OK


def command_demo(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    try:
        path = golden_path("demo", load_definitions(args.definitions) if args.definitions else None)
    except Refused as refusal:
        print(f"erp-crm demo: CANNOT-ASSESS — {refusal.detail}", file=err)
        return CANNOT_ASSESS
    print(json.dumps(path.to_dict(), indent=2, sort_keys=True), file=sink)
    return OK


def command_definitions(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    try:
        definitions = load_definitions(args.definitions) if args.definitions else load_definitions()
    except Refused as refusal:
        print(f"erp-crm definitions: CANNOT-ASSESS — {refusal.detail}", file=err)
        return CANNOT_ASSESS
    print(json.dumps(definitions.to_dict(), indent=2, sort_keys=True), file=sink)
    return OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m integrations.erp.crm.cli",
        description="CRM / projects / quality / support documents and flows (issue #650).",
    )
    parser.add_argument(
        "--definitions",
        metavar="PATH",
        help="declaration set to use (default: catalog/definitions.json)",
    )
    subparsers = parser.add_subparsers(dest="command")
    check = subparsers.add_parser("check", help="verify declarations, the golden path and the controls")
    check.add_argument("--provenance", metavar="PATH", help="harvest record to use")
    subparsers.add_parser("demo", help="print the golden-path scenario")
    subparsers.add_parser("definitions", help="print the validated declaration set")
    return parser


def main(argv: Optional[Sequence[str]] = None, sink: Optional[TextIO] = None) -> int:
    out = sink if sink is not None else sys.stdout
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.command:
        parser.print_help(file=out)
        return CANNOT_ASSESS
    if args.command == "check":
        return command_check(args, out, sys.stderr)
    if args.command == "demo":
        return command_demo(args, out, sys.stderr)
    return command_definitions(args, out, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
