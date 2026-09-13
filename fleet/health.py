#!/usr/bin/env python3
"""Fleet health signal — cmr-style healthy/degraded/failing (issue #163).

Harvested pattern (docs/CANNIBALIZATION.md #163): `leaderboard/docker/worker-fleet/personas.yaml`
ships a `fleet-health` persona ("periodic health reports") as prior art for a
health check separate from the dispatch loop itself. This module is that
check for this repo's file-mailbox transport: it never spawns anything, it
only reads state the loop already writes (`.fleet/slog.jsonl`, the claim
ledger, the running process) and reports a tri-state signal.

Signal:
    0 healthy  — the never-idle loop is running and has posted activity
                 recently, and no claim is wedged past the staleness window.
    1 degraded — the loop is running but stale (no slog activity in the
                 window), or a claim is held past the staleness window.
    2 failing  — the loop is not running at all.

Usage:
    python3 fleet/health.py check [--stale-minutes 30]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SLOG = ROOT / ".fleet" / "slog.jsonl"

sys.path.insert(0, str(ROOT / "governance" / "dispatch"))
import claims  # noqa: E402
from snapshot import parse_iso  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

HEALTHY, DEGRADED, FAILING = 0, 1, 2
LABELS = {HEALTHY: "healthy", DEGRADED: "degraded", FAILING: "failing"}


def loop_running() -> bool:
    result = subprocess.run(["pgrep", "-f", "fleet/terminal.py"], capture_output=True, text=True)
    return result.returncode == 0


def slog_age_minutes() -> float | None:
    if not SLOG.exists():
        return None
    return (time.time() - SLOG.stat().st_mtime) / 60.0


def stalest_claim_minutes(ledger_path: Path) -> float | None:
    if not Path(ledger_path).exists():
        return None
    live = claims.active_claims(claims.read_ledger(ledger_path))
    if not live:
        return None
    now = datetime.now(timezone.utc)
    ages = [(now - parse_iso(claim.at)).total_seconds() / 60.0 for claim in live.values()]
    return max(ages) if ages else None


def evaluate(stale_minutes: float, ledger_path: Path) -> tuple[int, list[str]]:
    reasons: list[str] = []
    if not loop_running():
        return FAILING, ["fleet/terminal.py is not running — the never-idle loop is dead"]

    level = HEALTHY
    age = slog_age_minutes()
    if age is None:
        level = DEGRADED
        reasons.append("no .fleet/slog.jsonl yet — no observed activity")
    elif age > stale_minutes:
        level = DEGRADED
        reasons.append(f"slog last touched {age:.1f}m ago (> {stale_minutes:.0f}m stale window)")

    claim_age = stalest_claim_minutes(ledger_path)
    if claim_age is not None and claim_age > stale_minutes:
        level = DEGRADED
        reasons.append(f"a claim has been held {claim_age:.1f}m (> {stale_minutes:.0f}m stale window)")

    if not reasons:
        reasons.append("loop running, slog fresh, no wedged claims")
    return level, reasons


def cmd_check(args: argparse.Namespace) -> int:
    level, reasons = evaluate(args.stale_minutes, Path(args.ledger))
    print(json.dumps({"signal": level, "status": LABELS[level], "reasons": reasons}))
    return level


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-health", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="print the health signal and exit with it (0/1/2)")
    check.add_argument("--stale-minutes", type=float, default=30.0)
    check.add_argument("--ledger", default=str(claims.DEFAULT_LEDGER))
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
