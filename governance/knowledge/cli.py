#!/usr/bin/env python3
"""Knowledge-index CLI — build, validate, query, report (issue #139).

---knowledge---
module_id: governance.knowledge.cli
system: governance
app: knowledge
solution_class: pattern
patterns: [no-false-green, honesty-tri-state, fail-closed]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [cmd_build, cmd_validate, cmd_query, cmd_coverage, build_parser, main]
invariants: ""
gotchas: ""
related: ["#139"]
do_not_duplicate: null
---knowledge---

Runs the indexer on demand. The scheduled path (a Makefile target driven by the
ops runner's cron) simply calls ``build``; nothing here needs a human.

Honest tri-state exit codes (the repo convention, GR-12 / no-false-green):

* ``0`` — OK
* ``1`` — NOT-OK (validation errors, or a query that matched nothing)
* ``2`` — CANNOT-ASSESS (no repository root / no catalogue to read)

Subcommands::

    build      write the catalogue (and refresh the report)
    validate   build in memory, check it, write the report — the gate of record
    query      search the index, printing source-backed evidence
    coverage   per-kind coverage for board reporting

Examples::

    python3 governance/knowledge/cli.py build
    python3 governance/knowledge/cli.py validate
    python3 governance/knowledge/cli.py query --text "fail-closed" --kind policy
    python3 governance/knowledge/cli.py coverage
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

import indexer  # noqa: E402
import query as query_mod  # noqa: E402
from model import errors, warnings  # noqa: E402

DEFAULT_ROOT = Path(_PKG_DIR).parent.parent
CATALOG_PATH = Path("governance") / "knowledge" / indexer.CATALOG_FILENAME

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2


def _emit_findings(index) -> None:
    for finding in index.findings:
        print("  %-7s %-30s %s" % (finding.severity.upper(), finding.code, finding.message))
    if not index.findings:
        print("  (no findings)")


def _report_payload(index, drift=()) -> dict:
    payload = index.as_dict()
    payload["drift"] = [finding.as_dict() for finding in drift]
    payload["error_count"] = len(errors(index.findings))
    payload["warning_count"] = len(warnings(index.findings)) + len(drift)
    return payload


def _write_report(root: Path, payload: dict) -> Path:
    path = root / indexer.REPORT_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def cmd_build(args: argparse.Namespace) -> int:
    index = indexer.build_index(args.root)
    payload = _report_payload(index)
    _write_report(args.root, payload)
    catalog = indexer.write_catalog(index, args.root / CATALOG_PATH)
    print("catalog: %s" % catalog)
    print("items: %d" % len(index.items))
    _emit_findings(index)
    return EXIT_NOT_OK if errors(index.findings) else EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    if not args.root.is_dir():
        print("knowledge-index: CANNOT-ASSESS — %s is not a directory" % args.root, file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    baseline = indexer.load_catalog(args.root / CATALOG_PATH)
    if baseline is None and not args.allow_missing_catalog:
        print(
            "knowledge-index: CANNOT-ASSESS — no catalogue at %s (run `build` first)"
            % (args.root / CATALOG_PATH),
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    index = indexer.build_index(args.root)
    drift = indexer.drift_findings(index, baseline) if baseline else []
    _write_report(args.root, _report_payload(index, drift))

    print("indexed items: %d" % len(index.items))
    _emit_findings(index)
    if drift:
        print("  %-7s %-30s %d asset(s) drifted since the recorded catalogue"
              % ("WARNING", "integrity-drift", len(drift)))

    hard = errors(index.findings)
    if hard:
        print(
            "knowledge-index: FAIL (%d error(s), %d warning(s))"
            % (len(hard), len(warnings(index.findings)) + len(drift)),
            file=sys.stderr,
        )
        return EXIT_NOT_OK

    print(
        "knowledge-index: OK (%d item(s), %d kind(s) covered, %d warning(s))"
        % (len(index.items), sum(1 for c in index.coverage if c.count), len(drift))
    )
    return EXIT_OK


def cmd_query(args: argparse.Namespace) -> int:
    baseline = indexer.load_catalog(args.root / CATALOG_PATH)
    index = indexer.build_index(args.root) if baseline is None else None
    if index is None:
        # Prefer the recorded catalogue: a query answers about what was indexed.
        from model import Index as _Index

        index = _Index.from_dict(baseline)

    results = query_mod.query(
        index,
        text=args.text,
        kind=args.kind,
        owner=args.owner,
        tag=args.tag,
        limit=args.limit,
    )
    payload = query_mod.summarize(index, results)
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not results:
        print("knowledge-index: no matches (NOT-OK)", file=sys.stderr)
        return EXIT_NOT_OK
    return EXIT_OK


def cmd_coverage(args: argparse.Namespace) -> int:
    baseline = indexer.load_catalog(args.root / CATALOG_PATH)
    index = indexer.build_index(args.root) if baseline is None else None
    if index is None:
        from model import Index as _Index

        index = _Index.from_dict(baseline)

    print(json.dumps(query_mod.coverage_report(index), indent=2, sort_keys=True))
    missing_required = [
        entry.kind for entry in index.coverage if entry.required and not entry.count
    ]
    if missing_required:
        print(
            "knowledge-index: required kind(s) uncovered: %s" % ", ".join(missing_required),
            file=sys.stderr,
        )
        return EXIT_NOT_OK
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governance/knowledge/cli.py",
        description="CMR/GDC institutional knowledge index (issue #139).",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="write the catalogue + report")
    p_build.set_defaults(func=cmd_build)

    p_validate = sub.add_parser("validate", help="check the index (gate of record)")
    p_validate.add_argument(
        "--allow-missing-catalog",
        action="store_true",
        help="build fresh even when no catalogue is recorded yet (cold start)",
    )
    p_validate.set_defaults(func=cmd_validate)

    p_query = sub.add_parser("query", help="search with source-backed evidence")
    p_query.add_argument("--text", default=None)
    p_query.add_argument("--kind", default=None)
    p_query.add_argument("--owner", default=None)
    p_query.add_argument("--tag", default=None)
    p_query.add_argument("--limit", type=int, default=None)
    p_query.set_defaults(func=cmd_query)

    p_cov = sub.add_parser("coverage", help="per-kind coverage")
    p_cov.set_defaults(func=cmd_coverage)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
