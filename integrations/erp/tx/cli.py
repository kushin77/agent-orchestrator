"""The offline CLI for the transactional-spine lane (issue #648): ``check``, ``demo``, ``definitions``.

Exit contract, the repository's tri-state convention (``guardrails/honesty``):

* ``0`` **OK** — every invariant measured and satisfied;
* ``1`` **NOT-OK** — a measured invariant is violated, named on stderr;
* ``2`` **CANNOT-ASSESS** — the question cannot be answered (the indexer cannot
  be read, or the ERP-02 model will not load). Never a pass: a lane that cannot
  resolve its own definitions cannot report on them, and reporting OK would be
  the false green this repository's doctrine forbids.

``check`` measures four things a suite alone would not:

1. **the resolution** — the lane's declared documents (through the indexer), the
   cycle, the stock family and the ledger family, each *derived* rather than
   declared, and each reported with the declarations the spine does not drive;
2. **the golden path is deterministic and keyless** — the cycle is run *twice* and
   the two runs must produce the same audit head, the same ledger totals and the
   same stock balances. A run whose evidence depends on when it ran cannot be
   asserted on;
3. **cancellation reverses exactly** — the cancellation path must leave both
   ledgers netting to zero for every document it cancelled, which is criterion 2
   measured rather than claimed;
4. **every refusal is provoked** — ``negative_control`` provokes each code of the
   closed vocabulary and fails when the provoked set and the vocabulary diverge.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence, TextIO

from . import negative_control
from .definitions import DefinitionSet, load as load_definitions
from .model import Refused
from .spine import GoldenPath, cancellation_path, golden_path

CANNOT_ASSESS = 2
NOT_OK = 1
OK = 0


def _load(path: Optional[str], sink: TextIO) -> Optional[DefinitionSet]:
    """Resolve the definition set, reporting CANNOT-ASSESS rather than guessing."""
    if path:
        print(
            "  CANNOT-ASSESS  a definition set path cannot be substituted: this "
            "lane's definitions are resolved from the indexer and ERP-02, and a "
            "file of its own is exactly what acceptance criterion 3 forbids",
            file=sink,
        )
        return None
    try:
        definitions = load_definitions()
    except Refused as refusal:
        print(f"  CANNOT-ASSESS  definitions: {refusal.detail}", file=sink)
        return None
    print(
        f"  OK    definitions: {len(definitions.lane_documents)} declared document(s), "
        f"cycle {' -> '.join(definitions.chain)}, ledger {definitions.accounting_kind}",
        file=sink,
    )
    return definitions


def _report_resolution(definitions: DefinitionSet, sink: TextIO) -> None:
    print(
        f"  OK    indexer: issue #{definitions.issue} owns "
        f"{', '.join(definitions.lane_ids())} (families: "
        f"{', '.join(definitions.families())})",
        file=sink,
    )
    print(
        f"  OK    derived cycle: {' -> '.join(definitions.chain)} "
        f"(from {len(definitions.links)} schema link(s))",
        file=sink,
    )
    print(
        f"  OK    derived effects: stock through {', '.join(definitions.stock_kinds)}, "
        f"ledger through {definitions.accounting_kind} "
        f"({len(definitions.voucher_sources)} accepted voucher source(s))",
        file=sink,
    )
    undriven = definitions.undriven()
    if undriven:
        named = "; ".join(f"{document.id} — {reason}" for document, reason in undriven)
        print(
            f"  NOTE  declared but not driven by this spine ({len(undriven)}): {named}",
            file=sink,
        )


def _check_determinism(definitions: DefinitionSet, sink: TextIO) -> List[str]:
    """Run the golden path twice and require one answer."""
    failures: List[str] = []
    first: GoldenPath = golden_path("check", definitions)
    second: GoldenPath = golden_path("check", definitions)
    failures.extend(
        f"golden path: {finding.code}: {finding.detail}" for finding in first.findings
    )
    if first.workspace.rail.head != second.workspace.rail.head:
        failures.append(
            "the golden path is not deterministic: two runs produced different audit "
            f"heads ({first.workspace.rail.head[:12]} and {second.workspace.rail.head[:12]})"
        )
    if first.workspace.ledger.balances() != second.workspace.ledger.balances():
        failures.append(
            "the golden path is not deterministic: two runs produced different ledger "
            "balances"
        )
    if first.workspace.summary()["stock"] != second.workspace.summary()["stock"]:
        failures.append(
            "the golden path is not deterministic: two runs produced different stock "
            "balances"
        )
    if not failures:
        summary = first.workspace.summary()
        debits, _credits = first.workspace.ledger.totals()
        print(
            f"  OK    golden path: {len(first.workspace.documents)} document(s), "
            f"{len(first.workspace.rail)} audit entr(ies), head "
            f"{first.workspace.rail.head[:12]}, stock {summary['stock']}, "
            f"ledger {debits:.2f} of debits",
            file=sink,
        )
    return failures


def _check_cancellation(definitions: DefinitionSet, sink: TextIO) -> List[str]:
    """Require the cancellation path to reverse both ledgers exactly."""
    path = cancellation_path("check", definitions)
    failures = [f"cancellation: {finding.code}: {finding.detail}" for finding in path.findings]
    if not failures:
        print(
            f"  OK    cancellation path: {len(path.steps)} step(s), every cancelled "
            "document nets to zero on the stock and general ledgers",
            file=sink,
        )
    return failures


def _check_negative_controls(sink: TextIO) -> List[str]:
    driver = negative_control.run(sink)
    if driver != 0:
        return ["negative-control: one or more refusals were not provoked"]
    return []


def command_check(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    print("erp-tx check", file=sink)
    definitions = _load(args.definitions, sink)
    if definitions is None:
        return CANNOT_ASSESS
    _report_resolution(definitions, sink)

    failures: List[str] = []
    failures.extend(_check_determinism(definitions, sink))
    failures.extend(_check_cancellation(definitions, sink))
    failures.extend(_check_negative_controls(sink))

    if failures:
        print(f"erp-tx check: NOT-OK — {len(failures)} problem(s)", file=err)
        for failure in failures:
            print(f"  FAIL  {failure}", file=err)
        return NOT_OK
    print("erp-tx check: OK", file=sink)
    return OK


def command_demo(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    if args.definitions:
        print(
            "erp-tx demo: CANNOT-ASSESS — a definition set path cannot be substituted",
            file=err,
        )
        return CANNOT_ASSESS
    try:
        definitions = load_definitions()
    except Refused as refusal:
        print(f"erp-tx demo: CANNOT-ASSESS — {refusal.detail}", file=err)
        return CANNOT_ASSESS
    payload = {
        "goldenPath": golden_path("demo", definitions).to_dict(),
        "cancellationPath": cancellation_path("demo", definitions).to_dict(),
    }
    print(json.dumps(payload, indent=2, sort_keys=True), file=sink)
    return OK


def command_definitions(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    if args.definitions:
        print(
            "erp-tx definitions: CANNOT-ASSESS — a definition set path cannot be "
            "substituted",
            file=err,
        )
        return CANNOT_ASSESS
    try:
        definitions = load_definitions()
    except Refused as refusal:
        print(f"erp-tx definitions: CANNOT-ASSESS — {refusal.detail}", file=err)
        return CANNOT_ASSESS
    print(json.dumps(definitions.to_dict(), indent=2, sort_keys=True), file=sink)
    return OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m integrations.erp.tx.cli",
        description="The ERP transactional spine: selling -> stock -> accounting (issue #648).",
    )
    parser.add_argument(
        "--definitions",
        metavar="PATH",
        help=(
            "rejected: this lane resolves its definitions from the indexer and "
            "ERP-02, and has no declaration file of its own to substitute"
        ),
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("check", help="verify the resolution, the golden path and the controls")
    subparsers.add_parser("demo", help="print the golden and cancellation transcripts")
    subparsers.add_parser("definitions", help="print the resolved definition set")
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
