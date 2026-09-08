"""Command-line interface for the tamper-evident audit ledger
(telemetry/ledger, issue #31).

Run from anywhere with ``telemetry/`` importable, e.g.::

    PYTHONPATH=telemetry python3 -m ledger.cli <ledger-dir> <command> ...

Commands (exit codes follow the issue #28 wire contract: OK=0, NOT-OK=1,
CANNOT-ASSESS=2):

    append  <tenant> --action <action> [options]
            Append one audit record. ``--actor user:alice`` or
            ``--actor-kind agent --actor-id worker-1``. Sensitive payloads
            come from ``--payload-file <path>`` or ``--payload-json '<json>'``
            and are encrypted at rest; a missing key/cipher refuses the append.
    verify  <tenant> [--expected-seq N --expected-hash H] | --all
            Verify the hash chain; print status + detail; exit 0/1/2.
    export  <tenant>
            Print the tenant's stored trail (one JSON object per line;
            payloads remain encrypted envelopes). Only the tenant's own chain.
    read    <tenant> <seq>
            Decrypt and print the payload of record <seq>; exit 2 when the
            tenant key is unavailable (CANNOT-ASSESS).
    state   <tenant>
            Print the trusted tail (seq + hash) for truncation anchoring.
    rechain <tenant> --ack
            Opt-in destructive repair of broken hash links (see README).

Encryption keys are injected, never stored in code or the ledger: set
``AO_AUDIT_LEDGER_KEY_<TENANT>`` (64 hex) in the environment, or pass
``--keystore <file>`` where the file is ``{"<tenant>": {"key": "<64 hex>"}}``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional, Tuple

from .errors import LedgerError, RepairRefusedError
from .keystore import EnvKeystore, FileKeystore, Keystore
from .store import LedgerStore, open_ledger
from .verify import read_payload, verify_all, verify_ledger


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ledger.cli",
        description="Per-tenant tamper-evident audit ledger (issue #31).",
    )
    parser.add_argument("ledger_dir", help="directory holding <tenant>.jsonl chains")
    parser.add_argument(
        "--keystore",
        default=None,
        help="JSON keystore file {tenant: {key: <64hex>}} (default: env "
        "AO_AUDIT_LEDGER_KEY_<TENANT>)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ap = sub.add_parser("append", help="append one audit record")
    ap.add_argument("tenant")
    ap.add_argument("--actor", default=None, help="canonical 'kind:id' actor")
    ap.add_argument("--actor-kind", default=None, choices=["user", "agent", "system"])
    ap.add_argument("--actor-id", default=None)
    ap.add_argument("--action", required=True)
    ap.add_argument("--impersonated-by", default=None)
    ap.add_argument("--resource", default=None)
    ap.add_argument("--evidence", default=None)
    ap.add_argument("--model-used", default=None)
    ap.add_argument("--cost-usd", default=None)
    ap.add_argument("--payload-json", default=None, help="sensitive payload JSON")
    ap.add_argument("--payload-file", default=None, help="read payload JSON from file")
    ap.add_argument("--ts", default=None, help="RFC 3339 UTC timestamp (default now)")

    vp = sub.add_parser("verify", help="verify a tenant hash chain (tri-state)")
    vp.add_argument("tenant", nargs="?", default=None,
                    help="tenant id (omit with --all)")
    vp.add_argument("--expected-seq", type=int, default=None)
    vp.add_argument("--expected-hash", default=None)
    vp.add_argument("--all", action="store_true", help="verify every tenant")

    ep = sub.add_parser("export", help="export a tenant's stored audit trail")
    ep.add_argument("tenant")

    rp = sub.add_parser("read", help="decrypt a record payload (tri-state)")
    rp.add_argument("tenant")
    rp.add_argument("seq", type=int)

    sp = sub.add_parser("state", help="print a tenant's trusted tail state")
    sp.add_argument("tenant")

    rc = sub.add_parser("rechain", help="opt-in destructive link repair")
    rc.add_argument("tenant")
    rc.add_argument("--ack", action="store_true", help="acknowledge repair")
    return parser


def _resolve_keystore(args: argparse.Namespace) -> Keystore:
    if args.keystore:
        return FileKeystore(args.keystore)
    return EnvKeystore()


def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _load_payload(args: argparse.Namespace) -> Any:
    if args.payload_file is not None and args.payload_json is not None:
        raise LedgerError("give --payload-file or --payload-json, not both")
    if args.payload_file is not None:
        with open(args.payload_file, "r", encoding="utf-8") as handle:
            return json.load(handle)
    if args.payload_json is not None:
        return json.loads(args.payload_json)
    return None


def _run(args: argparse.Namespace, store: LedgerStore) -> int:
    if args.command == "append":
        payload = _load_payload(args)
        record = store.append(
            args.tenant,
            actor=args.actor,
            actor_kind=args.actor_kind,
            actor_id=args.actor_id,
            action=args.action,
            impersonated_by=args.impersonated_by,
            resource=args.resource,
            evidence=args.evidence,
            model_used=args.model_used,
            cost_usd=args.cost_usd,
            payload=payload,
            ts=args.ts,
        )
        _print_json(record)
        return 0

    if args.command == "verify":
        if args.all:
            verdicts = verify_all(store)
            worst = 0
            for tenant in sorted(verdicts):
                verdict = verdicts[tenant]
                _print_json(verdict.as_dict())
                worst = max(worst, verdict.exit_code)
            return worst
        if args.tenant is None:
            raise LedgerError("verify needs a <tenant> or --all")
        expected: Optional[Tuple[int, str]] = None
        if args.expected_seq is not None or args.expected_hash is not None:
            if args.expected_seq is None or not args.expected_hash:
                raise LedgerError(
                    "--expected-seq and --expected-hash must be given together"
                )
            expected = (args.expected_seq, args.expected_hash)
        verdict = verify_ledger(store, args.tenant, expected=expected)
        _print_json(verdict.as_dict())
        return verdict.exit_code

    if args.command == "export":
        for record in store.export(args.tenant):
            _print_json(record)
        return 0

    if args.command == "read":
        status, payload, detail = read_payload(store, args.tenant, args.seq)
        if status == "OK":
            if payload is None:
                print("payload: null")
            else:
                print("payload: " + json.dumps(payload, sort_keys=True))
            return 0
        if status == "CANNOT-ASSESS":
            print(f"status: CANNOT-ASSESS ({detail})", file=sys.stderr)
            return 2
        print(f"status: NOT-OK ({detail})", file=sys.stderr)
        return 1

    if args.command == "state":
        seq, hash_value = store.tail_state(args.tenant)
        print(f"seq: {seq}")
        print(f"hash: {hash_value}")
        return 0

    if args.command == "rechain":
        verdict = store.rechain(args.tenant, acknowledge=args.ack)
        _print_json(verdict.as_dict())
        return verdict.exit_code

    raise LedgerError(f"unknown command {args.command!r}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        keystore = _resolve_keystore(args)
        store = open_ledger(args.ledger_dir, keystore=keystore)
        return _run(args, store)
    except RepairRefusedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive, honest non-zero
        print(f"error: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
