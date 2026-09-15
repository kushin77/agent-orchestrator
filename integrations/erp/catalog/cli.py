#!/usr/bin/env python3
"""ERP module declaration CLI — `verify` and `vocabulary` (EPIC #645, issue #646).

``verify`` judges the module's declaration rooted at ``--root`` and reports every
refusal by name. ``vocabulary`` prints the domain facts the catalogue declares,
one ``<field>\t<value>`` pair per line, so the gate and the suite can provoke a
restatement with a fact read from the catalogue instead of one hard-coded in the
check — a check that carries its own copy of the data it checks is a second
store with extra steps.

Honest tri-state exit codes (the repository convention, GR-12 / no-false-green):

* ``0`` — OK
* ``1`` — NOT-OK (at least one named refusal)
* ``2`` — CANNOT-ASSESS (an input is unreadable, malformed, or a schema uses a
  keyword the repository's validator cannot enforce)

Examples::

    python3 integrations/erp/catalog/cli.py verify
    python3 integrations/erp/catalog/cli.py vocabulary
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

#: This file is <root>/integrations/erp/catalog/cli.py.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from integrations.erp.catalog import model, validate  # noqa: E402


def cmd_verify(args: argparse.Namespace) -> int:
    report = validate.verify(args.root)
    for finding in report.findings:
        print(finding.line())
    for message in report.cannot_assess:
        print("  CANNOT-ASSESS  %s" % message)
    if report.cannot_assess:
        print(
            "erp-module: CANNOT-ASSESS — %d input(s) could not be assessed"
            % len(report.cannot_assess),
            file=sys.stderr,
        )
        return model.EXIT_CANNOT_ASSESS
    if report.findings:
        print(
            "erp-module: FAIL (%d finding(s) over %d catalogue file(s))"
            % (len(report.findings), report.catalogue_files),
            file=sys.stderr,
        )
        return model.EXIT_NOT_OK
    print(
        "erp-module: OK (%d catalogue file(s), %d declared document type(s))"
        % (report.catalogue_files, len(report.documents))
    )
    return model.EXIT_OK


def cmd_vocabulary(args: argparse.Namespace) -> int:
    facts = validate.declared_vocabulary(args.root)
    if not facts:
        print("erp-module: no declared vocabulary (is the catalogue readable?)", file=sys.stderr)
        return model.EXIT_CANNOT_ASSESS
    for field, value in facts:
        print("%s\t%s" % (field, value))
    return model.EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="integrations/erp/catalog/cli.py",
        description="The ERP module's declaration surface (EPIC #645, issue #646).",
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify", help="judge the module's declaration")
    verify.set_defaults(func=cmd_verify)

    vocabulary = sub.add_parser("vocabulary", help="print the declared domain facts")
    vocabulary.set_defaults(func=cmd_vocabulary)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
