#!/usr/bin/env python3
"""Fleet health signal — cmr-style healthy/degraded/failing (issue #163).

Harvested pattern (docs/CANNIBALIZATION.md #163): `leaderboard/docker/worker-fleet/personas.yaml`
ships a `fleet-health` persona ("periodic health reports") as prior art for a
health check separate from the dispatch loop itself. This module is that
check for this repo's file-mailbox transport: it never spawns anything, it
only reads state the loop already writes (the rung heartbeats, the claim
ledger, the running processes) and reports a tri-state signal.

Both rungs are probed (issue #277). The brain is the middle rung and the only
thing that turns an operator order into a directive, so a dead brain is not
healthy even while the sister still beats — this is the same both-rungs truth
`channel.report_rung` reports. Freshness is read from the rung heartbeat's *age*,
not `.fleet/slog.jsonl`'s mtime: the log is only written when a message moves, so
an idle-but-healthy fleet must not read as degraded.

Signal:
    0 healthy  — the sister and the brain are running current builds with fresh
                 heartbeats, and no claim is wedged past the staleness window.
    1 degraded — a rung is down (at least degraded for a dead brain), running
                 stale code, or its heartbeat is stale; or a claim is held past
                 the staleness window.
    2 failing  — the sister loop is not running at all.

Usage:
    python3 fleet/health.py check [--stale-minutes 30]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import runtime

ROOT = Path(__file__).resolve().parent.parent
# The two probe targets, mirroring `channel.report_rung`: the sister loop and the
# brain. Resolved on every call (see `rungs`) so a redirected heartbeat path is
# honoured.
SISTER_PROCESS = "fleet/terminal.py"
BRAIN_PROCESS = "fleet/brain.py"
SISTER_HEARTBEAT = runtime.FLEET_DIR / "sister.heartbeat.json"
BRAIN_HEARTBEAT = runtime.FLEET_DIR / "brain.heartbeat.json"

sys.path.insert(0, str(ROOT / "governance" / "dispatch"))
sys.path.insert(0, str(ROOT / "fleet"))
import channel  # noqa: E402
import claims  # noqa: E402
from snapshot import parse_iso  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

HEALTHY, DEGRADED, FAILING = 0, 1, 2
LABELS = {HEALTHY: "healthy", DEGRADED: "degraded", FAILING: "failing"}


def process_running(pattern: str) -> bool:
    """True when a live process matches `pattern` (pgrep exit 0)."""
    try:
        result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def loop_running() -> bool:
    """The sister loop — the never-idle dispatcher."""
    return process_running(SISTER_PROCESS)


def brain_running() -> bool:
    """The brain — the middle rung; no operator order is dispatched without it."""
    return process_running(BRAIN_PROCESS)


def read_beat(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def rungs() -> tuple[tuple[str, Path, str, Callable[[], bool]], ...]:
    """(name, heartbeat path, start command, liveness probe) per rung.

    Resolved on every call, not at import, so the heartbeat paths follow the
    module constants rather than a snapshot taken when the module loaded.
    """
    return (
        ("sister", SISTER_HEARTBEAT, "bash fleet/terminal.sh", loop_running),
        ("brain", BRAIN_HEARTBEAT, "bash fleet/brain.sh", brain_running),
    )


def stalest_claim_minutes(ledger_path: Path) -> float | None:
    if not Path(ledger_path).exists():
        return None
    live = claims.active_claims(claims.read_ledger(ledger_path))
    if not live:
        return None
    now = datetime.now(timezone.utc)
    ages = [(now - parse_iso(claim.at)).total_seconds() / 60.0 for claim in live.values()]
    return max(ages) if ages else None


def rung_health(
    name: str, heartbeat_path: Path, start_cmd: str, probe: Callable[[], bool], head: str
) -> tuple[int, str | None]:
    """One rung's level + reason; down, unreported, stale or drifted is degraded.

    Mirrors `channel.report_rung`'s two truths — the rung must be alive *and*
    running the commit it declares — so health cannot disagree with `channel.py
    status` about a rung.
    """
    if not probe():
        return DEGRADED, f"{name}: not running — this rung is down (start: {start_cmd})"
    beat = read_beat(heartbeat_path)
    if beat is None:
        return DEGRADED, (
            f"{name}: no heartbeat file — it is running a build older than the heartbeat "
            f"check, so merged fixes are not live (restart: {start_cmd})"
        )
    age = channel.heartbeat_age_seconds(beat)
    if age is None:
        return DEGRADED, f"{name}: heartbeat present but undated"
    if age > channel.STALE_HEARTBEAT_SECONDS:
        return DEGRADED, (
            f"{name}: heartbeat stale ({int(age)}s since the last beat > "
            f"{channel.STALE_HEARTBEAT_SECONDS}s) — the loop is not making progress"
        )
    running = str(beat.get("commit", "unknown"))
    if head != "unknown" and running != head:
        return DEGRADED, (
            f"{name}: running {running} but HEAD is {head} — merged fixes are not live "
            f"(restart: {start_cmd})"
        )
    return HEALTHY, None


def evaluate(stale_minutes: float, ledger_path: Path) -> tuple[int, list[str]]:
    reasons: list[str] = []
    if not loop_running():
        return FAILING, ["fleet/terminal.py is not running — the never-idle loop is dead"]

    level = HEALTHY
    head = channel.head_commit()
    # Probe BOTH rungs, exactly as `channel.py status` does: a dead brain while the
    # sister still beats is at least degraded, never healthy. Freshness is the rung
    # heartbeat's age, not `.fleet/slog.jsonl`'s mtime — an idle-but-healthy fleet
    # writes no messages, and reading that as degraded was the second half of #277.
    for name, beat_path, start_cmd, probe in rungs():
        rung_level, rung_reason = rung_health(name, beat_path, start_cmd, probe, head)
        level = max(level, rung_level)
        if rung_reason:
            reasons.append(rung_reason)

    claim_age = stalest_claim_minutes(ledger_path)
    if claim_age is not None and claim_age > stale_minutes:
        level = max(level, DEGRADED)
        reasons.append(f"a claim has been held {claim_age:.1f}m (> {stale_minutes:.0f}m stale window)")

    if not reasons:
        reasons.append("sister and brain running current builds with fresh heartbeats, no wedged claims")
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
