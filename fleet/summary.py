#!/usr/bin/env python3
"""Per-run telemetry summary — the readable report over the run log (issue #234).

WHY it exists: `fleet/terminal.py` appends one JSONL record per run (issue,
agent, lane, tier, thinking, runner, rc, duration, worktree, directive id — the
vocabulary issue #219 names), but nothing can look at that log as a whole.
FinOps decisions were being made on impressions — which tier dominates, how long
runs take, which outcomes repeat — exactly the failure the sibling metering
module caught in `deepseek` (#106). This module is that read path: it aggregates
the per-run records into a human-readable report (run count, tier mix, outcome
mix, mean duration) and writes nothing.

Design rule (the same one `fleet/report.py` follows): every aggregate is a PURE
function over a list of plain dicts, and `main()` is the only part that touches
the filesystem. That is what lets the tests assert the whole report from a
fixture, with no fleet, no network and no live log, and keeps the summary's
shape from depending on the machine that produced it.

The summary is READ-ONLY. It reads the run log and prints; it never appends,
claims, releases or dispatches anything. A record that omits a field is reported
under the `unknown` bucket (or skipped for the mean) rather than crashing the
report, so an older or partial record cannot take the summary down.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import telemetry

ROOT = Path(__file__).resolve().parent.parent
TELEMETRY_LOG = ROOT / ".fleet" / "telemetry.jsonl"

#: The per-run fields issue #219 names. The summary is tolerant: a record may
#: omit any of them and is still counted, with the missing value reported as
#: `unknown` (or skipped for the duration mean).
RECORD_FIELDS = (
    "issue",
    "agent",
    "lane",
    "tier",
    "thinking",
    "runner",
    "rc",
    "duration",
    "worktree",
    "directive_id",
)


def _tier(record: dict) -> str:
    """The run's FinOps tier, or `unknown` when the record omits it."""
    value = record.get("tier")
    return str(value) if value not in (None, "") else "unknown"


def _outcome(record: dict) -> str:
    """The run's outcome: `rc` per #219, `status` as the #232-era fallback."""
    value = record.get("rc")
    if value in (None, ""):
        value = record.get("status")
    return str(value) if value not in (None, "") else "unknown"


def _duration(record: dict) -> float | None:
    """The run's duration in seconds, or None when absent or non-numeric."""
    value = record.get("duration")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _identity(record: dict) -> str:
    """A stable per-run label: the directive id, or a #232-era run id fallback."""
    for key in ("directive_id", "run_id", "issue"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return "?"


def summarize(records: list[dict]) -> dict:
    """Aggregate per-run records into counts, tier/outcome mix and mean duration."""
    tier_mix: dict[str, int] = {}
    outcome_mix: dict[str, int] = {}
    durations: list[float] = []
    for record in records:
        tier = _tier(record)
        outcome = _outcome(record)
        tier_mix[tier] = tier_mix.get(tier, 0) + 1
        outcome_mix[outcome] = outcome_mix.get(outcome, 0) + 1
        duration = _duration(record)
        if duration is not None:
            durations.append(duration)
    return {
        "runs": len(records),
        "tier_mix": tier_mix,
        "outcome_mix": outcome_mix,
        "mean_duration": (sum(durations) / len(durations)) if durations else None,
    }


def render_report(records: list[dict]) -> str:
    """Render the summary as readable text (pure: no filesystem, no state)."""
    stats = summarize(records)
    if stats["tier_mix"]:
        tiers = ", ".join(
            f"{tier}={count}" for tier, count in sorted(stats["tier_mix"].items())
        )
    else:
        tiers = "none"
    if stats["mean_duration"] is None:
        mean = "n/a"
    else:
        mean = f"{stats['mean_duration']:.2f}s"
    lines = [
        "per-run telemetry summary",
        f"runs: {stats['runs']}",
        f"tier mix: {tiers}",
        f"mean duration: {mean}",
    ]
    for record in records:
        duration = _duration(record)
        duration_text = f"{duration:.2f}s" if duration is not None else "-"
        lines.append(
            "  {identity} issue={issue} agent={agent} tier={tier} rc={rc} "
            "duration={duration}".format(
                identity=_identity(record),
                issue=record.get("issue", "?"),
                agent=record.get("agent") or "-",
                tier=_tier(record),
                rc=_outcome(record),
                duration=duration_text,
            )
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleet-summary",
        description="Aggregate the per-run telemetry log into a readable report.",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=TELEMETRY_LOG,
        help="path to the per-run telemetry log (default: %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Read the log and print the summary; 0 on success, 1 on an unreadable log."""
    args = build_parser().parse_args(argv)
    try:
        records = telemetry.read_records(args.log)
    except (OSError, ValueError) as exc:
        print(f"fleet-summary: unreadable log {args.log}: {exc}", file=sys.stderr)
        return 1
    print(render_report(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
