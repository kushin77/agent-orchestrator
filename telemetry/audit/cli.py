"""Command-line interface for the audit read model (telemetry/audit, #347).

---knowledge---
module_id: telemetry.audit.cli
system: telemetry
app: audit
solution_class: enterprise
patterns: [read-only-projection, offline-cli, tri-state-exit]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [main]
invariants: "reads through the ledger; it owns no storage, no key material and no chain crypto"
gotchas: ""
related: ["#347", "#1510"]
do_not_duplicate: null
---knowledge---


Serves the tamper-evident audit trail as a read-only, filterable view — the
backend the shell's Audit view consumes. Chain crypto and storage belong to
[`telemetry/ledger`](../ledger/README.md); this CLI only reads through it.

Run from anywhere (``telemetry/`` on the path), e.g.::

    python3 telemetry/audit/cli.py /var/lib/audit list --actor agent:worker-1 --json
    python3 telemetry/audit/cli.py /var/lib/audit verify-chain
    python3 telemetry/audit/cli.py /var/lib/audit stats

Commands (exit codes follow the issue #28 wire contract: OK=0, NOT-OK=1,
CANNOT-ASSESS=2):

    list          [filters] [--json|--markdown]
                  Filter the trail. Fields: --actor, --agent, --action,
                  --severity, --since, --until, --entity, --tenant, and repeatable
                  --filter KEY=VALUE. An unknown field or severity is refused.
    verify-chain  [--tenant T --expected-seq N --expected-hash H] [--json]
                  Verify every tenant hash chain (tri-state); a modified,
                  reordered or removed record is a NOT-OK. Pass the trusted tail
                  for one tenant to also catch a silently truncated trail.
    stats         [--json|--markdown]
                  Counts, time span and the derived severity/actor/action
                  histograms.

Encryption keys are only needed by the ledger's own ``read``/``append``; this
read model never decrypts a payload, so the CLI needs no key material.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import read_model  # noqa: E402  (after sys.path)
from read_model import (  # noqa: E402  (after sys.path)
    SEVERITIES,
    AuditReadModel,
    FilterError,
    ReadModelError,
    open_read_model,
    severity_of,
)

from ledger.keystore import EnvKeystore, FileKeystore  # noqa: E402  (after sys.path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit.cli",
        description="Read-only, filterable view over the audit trail (issue #347).",
    )
    parser.add_argument("ledger_dir", help="directory holding <tenant>.jsonl chains")
    parser.add_argument(
        "--keystore",
        default=None,
        help="JSON keystore file {tenant: {key: <64hex>}} (default: environment)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    lp = sub.add_parser("list", help="filter the audit trail")
    _add_scope(lp)
    _add_filters(lp)
    _add_format(lp)

    vp = sub.add_parser("verify-chain", help="verify every tenant chain (tri-state)")
    vp.add_argument("--tenant", default=None, help="single tenant (with the anchor)")
    vp.add_argument("--expected-seq", type=int, default=None)
    vp.add_argument("--expected-hash", default=None)
    vp.add_argument("--json", action="store_true", help="emit JSON")

    sp = sub.add_parser("stats", help="counts and histograms")
    sp.add_argument("--tenant", default=None, help="restrict to one tenant")
    _add_format(sp)
    return parser


def _add_scope(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tenant", default=None, help="restrict to one tenant")


def _add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--actor", default=None, help="canonical 'kind:id' or bare id")
    parser.add_argument("--agent", default=None, help="shorthand for actor=agent:<id>")
    parser.add_argument("--action", default=None, help="exact action, e.g. model.call")
    parser.add_argument("--severity", default=None, choices=list(SEVERITIES))
    parser.add_argument("--since", default=None, help="inclusive RFC 3339 lower bound")
    parser.add_argument("--until", default=None, help="inclusive RFC 3339 upper bound")
    parser.add_argument("--entity", default=None, help="audited resource or evidence")
    parser.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="repeatable; an unknown key is refused",
    )


def _add_format(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--json", action="store_true", help="emit JSON")
    group.add_argument("--markdown", action="store_true", help="emit a markdown table")


def _resolve_keystore(args: argparse.Namespace) -> Any:
    if args.keystore:
        return FileKeystore(args.keystore)
    return EnvKeystore()


def _criteria(args: argparse.Namespace) -> Dict[str, Any]:
    criteria: Dict[str, Any] = {}
    for name in ("actor", "agent", "action", "severity", "since", "until", "entity"):
        value = getattr(args, name, None)
        if value is not None:
            criteria[name] = value
    if getattr(args, "tenant", None) is not None:
        criteria["tenant"] = args.tenant
    for raw in getattr(args, "filter", []) or []:
        key, sep, value = raw.partition("=")
        if not sep or not key:
            raise FilterError(f"--filter {raw!r} is not KEY=VALUE")
        if key in criteria:
            raise FilterError(f"--filter {key!r} duplicates --{key.replace('_', '-')}")
        criteria[key] = value
    return criteria


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _markdown_records(rows: List[Dict[str, Any]]) -> str:
    header = "| Tenant | Seq | Ts | Actor | Action | Severity | Resource |"
    divider = "| --- | --- | --- | --- | --- | --- | --- |"
    lines = [header, divider]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    row.get("tenantId"),
                    row.get("seq"),
                    row.get("ts"),
                    row.get("actor"),
                    row.get("action"),
                    severity_of(row.get("action", "")),
                    row.get("resource"),
                )
            )
            + " |"
        )
    return "\n".join(lines)


def _markdown_stats(stats: Dict[str, Any]) -> str:
    lines = [
        f"total records: {stats['totalRecords']}",
        "",
        "| Tenant | Records | First | Last | Tail seq |",
        "| --- | --- | --- | --- | --- |",
    ]
    for tenant, entry in sorted(stats["tenants"].items()):
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    tenant,
                    entry["records"],
                    entry["firstTs"],
                    entry["lastTs"],
                    entry["tailSeq"],
                )
            )
            + " |"
        )
    lines += ["", "| Severity | Count |", "| --- | --- |"]
    for rung in SEVERITIES:
        lines.append(f"| {rung} | {stats['severityHistogram'][rung]} |")
    return "\n".join(lines)


def _emit(value: Any, *, as_json: bool, markdown: str) -> None:
    if as_json:
        print(json.dumps(value, sort_keys=True, indent=2))
    else:
        print(markdown)


def _run(args: argparse.Namespace, model: AuditReadModel) -> int:
    if args.command == "list":
        rows = model.filter(**_criteria(args))
        _emit(rows, as_json=args.json, markdown=_markdown_records(rows))
        return 0
    if args.command == "stats":
        scoped = model
        if args.tenant is not None:
            scoped = AuditReadModel(model.store, tenants=[args.tenant])
        stats = scoped.stats()
        _emit(stats, as_json=args.json, markdown=_markdown_stats(stats))
        return 0
    if args.command == "verify-chain":
        expected: Optional[Dict[str, Tuple[int, str]]] = None
        if args.expected_seq is not None or args.expected_hash is not None:
            if args.tenant is None or args.expected_seq is None or not args.expected_hash:
                raise ReadModelError(
                    "--expected-seq and --expected-hash need --tenant (give all three)"
                )
            expected = {args.tenant: (args.expected_seq, args.expected_hash)}
        verdict = model.verify_chain(expected=expected)
        if args.json:
            print(json.dumps(verdict.as_dict(), sort_keys=True, indent=2))
        else:
            for tenant, entry in sorted(verdict.tenants.items()):
                print(
                    f"{tenant}: {entry.get('status')} "
                    f"seq={entry.get('seq')} hash={entry.get('hash')} "
                    f"— {entry.get('detail')}"
                )
            print(f"verify-chain: {verdict.status}")
        for entry in verdict.findings():
            print(
                f"  FINDING {entry.get('tenant')}: record {entry.get('brokenAt')} "
                f"— {entry.get('detail')}",
                file=sys.stderr,
            )
        return verdict.exit_code
    raise ReadModelError(f"unknown command {args.command!r}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        store = _resolve_keystore(args)
        model = open_read_model(args.ledger_dir, keystore=store)
        return _run(args, model)
    except FilterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except read_model.ReadModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive, honest non-zero
        print(f"error: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
