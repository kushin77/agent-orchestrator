"""Heartbeat adapter CLI — derive, validate and report the cadence policy.

    python3 -m paperclip.adapters.heartbeat.cli policy  [--root DIR]
    python3 -m paperclip.adapters.heartbeat.cli derive  --rung RUNG --session ID [--root DIR] [--now TS]
    python3 -m paperclip.adapters.heartbeat.cli validate [--root DIR]   # derives every rung, then validates

Exit-code contract (guardrails/honesty tri-state): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
A refusal is NEVER reported as a pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import adapter

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2


def _root(value: str) -> Path:
    return Path(value).resolve() if value else adapter.ROOT


def cmd_policy(args: argparse.Namespace) -> int:
    try:
        print(json.dumps(adapter.policy(), indent=2))
    except adapter.HeartbeatRefused as exc:
        print(f"heartbeat: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    return EXIT_OK


def cmd_derive(args: argparse.Namespace) -> int:
    try:
        record = adapter.derive_heartbeat(
            _root(args.root),
            args.rung,
            session_id=args.session,
            agent_id=args.agent,
            now=args.now,
        )
    except adapter.HeartbeatRefused as exc:
        print(f"heartbeat: REFUSED — {exc}", file=sys.stderr)
        return EXIT_NOT_OK
    print(json.dumps(record, indent=2))
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    root = _root(args.root)
    try:
        adapter.policy()
    except adapter.HeartbeatRefused as exc:
        print(f"heartbeat: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    fail = 0
    for rung in adapter.RUNGS:
        try:
            record = adapter.derive_heartbeat(
                root, rung.name, session_id=args.session, now=args.now
            )
        except adapter.HeartbeatRefused as exc:
            # A refusal is a legitimate outcome (the rung is unattributable or
            # down), not a validator failure: report it and continue.
            print(f"  refused  {rung.name}: {exc}")
            continue
        findings = adapter.validate_heartbeat(record)
        if findings:
            fail += 1
            print(f"  FAIL     {rung.name}")
            for finding in findings:
                print(f"             {finding}")
        else:
            print(f"  OK       {rung.name} cause={record['wake']['cause']} status={record['outcome']['status']}")
    if fail:
        print(f"heartbeat: FAIL — {fail} rung(s) violate the contract", file=sys.stderr)
        return EXIT_NOT_OK
    print("heartbeat: OK — every derived beat conforms to the frozen contract")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperclip-heartbeat", description=__doc__)
    parser.add_argument("--root", default="", help="the fleet tree (default: the repo root)")
    sub = parser.add_subparsers(dest="command", required=True)

    policy_parser = sub.add_parser("policy", help="print the derived cadence policy")
    policy_parser.set_defaults(func=cmd_policy)

    derive = sub.add_parser("derive", help="derive one rung's heartbeat")
    derive.add_argument("--rung", required=True, choices=[r.name for r in adapter.RUNGS])
    derive.add_argument("--session", default="unbound")
    derive.add_argument("--agent", default=None)
    derive.add_argument("--now", default=None)
    derive.set_defaults(func=cmd_derive)

    validate = sub.add_parser("validate", help="derive and validate every rung")
    validate.add_argument("--session", default="unbound")
    validate.add_argument("--now", default=None)
    validate.set_defaults(func=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
