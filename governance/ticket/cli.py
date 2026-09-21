"""CLI for the ticket projection (issue #401).

``project`` writes the projection; ``verify`` re-derives it, proves the store is
rebuildable and fails on any difference. ``freshness`` states the age this
consumer tolerates on the committed board snapshot and refuses a snapshot outside
it (issue #1077) — it is deliberately *not* part of ``build()``, which must stay
rebuilt byte-identically and therefore never reads the clock. Exit codes follow
the fleet tri-state contract: ``0`` OK / ``1`` NOT-OK / ``2`` CANNOT-ASSESS
(never reported as a pass).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from builder import build, verify
from freshness import CODE_BOARD_STALE, DEFAULT_MAX_AGE_HOURS, assess, parse_iso
from model import (
    STORE_RELPATH,
    CannotAssess,
    Contribution,
)

# The self-heal orchestration (issue #1692) is shared with the dispatch-queue
# consumer via governance/board_selfheal.py — NOT imported from
# governance/dispatch directly: that package shares bare module basenames
# (model, cli, snapshot) with this one, so a direct cross-import collides the
# flat sys.modules namespace (see governance/board_selfheal.py's docstring).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from governance import board_selfheal  # noqa: E402


def _fail(violations, code: int = 1) -> int:
    for violation in violations:
        print(f"  FAIL  {violation.render()}", file=sys.stderr)
    return code


def _note(warnings) -> None:
    for warning in warnings:
        print(f"  NOTE  {warning.render()}")


def _load_negative_control(path: str) -> list[Contribution]:
    """Load extra contributions — the documented negative-control seam.

    The gate writes this file to provoke a two-writer field or an unbacked
    ticket; no real call site passes it, and the records only ever influence the
    projected output, never a ledger.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise CannotAssess("negative control must be a JSON list")
    contributions: list[Contribution] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict) or not isinstance(item.get("ticket"), str):
            raise CannotAssess(f"negative control entry {index} needs a ticket id")
        field_name = item.get("field")
        contributions.append(
            Contribution(
                ticket=item["ticket"],
                field=field_name if isinstance(field_name, str) else None,
                producer=str(item.get("producer") or ""),
                value=item.get("value"),
                where=str(item.get("where") or "negative-control"),
            )
        )
    return contributions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ticket-project",
        description="Deterministic, rebuildable ticket projection over the fleet ledgers.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    project = sub.add_parser("project", help="write the projected ticket store")
    project.add_argument("--root", default=".", help="repository root (default: .)")
    project.add_argument(
        "--out", default=None, help=f"projected store path (default: {STORE_RELPATH})"
    )
    project.add_argument(
        "--stamp",
        default=None,
        help=(
            "NEGATIVE-CONTROL SEAM: embed a generated_at stamp, which makes two "
            "builds differ. A real build never passes this."
        ),
    )
    project.add_argument(
        "--negative-control",
        default=None,
        metavar="FILE",
        help="NEGATIVE-CONTROL SEAM: extra contributions that provoke a refusal.",
    )

    check = sub.add_parser("verify", help="re-derive and fail on any difference")
    check.add_argument("--root", default=".", help="repository root (default: .)")
    check.add_argument(
        "--out", default=None, help=f"projected store path (default: {STORE_RELPATH})"
    )

    fresh = sub.add_parser(
        "freshness",
        help="assert the committed board snapshot's age is inside the tolerance",
    )
    fresh.add_argument("--root", default=".", help="repository root (default: .)")
    fresh.add_argument(
        "--max-age-hours",
        type=float,
        default=None,
        help=f"the tolerated age in hours (default: {DEFAULT_MAX_AGE_HOURS:g})",
    )
    fresh.add_argument(
        "--now",
        default=None,
        help=(
            "NEGATIVE-CONTROL SEAM: evaluate the age at this instant instead of the "
            "wall clock, so the gate can provoke the refusal deterministically. The "
            "gate's assertion on the real snapshot never passes it."
        ),
    )
    fresh.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "self-heal (issue #1692): on a stale snapshot, run the ONE bounded board "
            "refresh before failing — OFF by default, a READ verb must not reach the "
            "network unasked"
        ),
    )
    fresh.add_argument(
        "--repo",
        default=None,
        help="the GitHub board a --refresh reads (default: the refresh verb's own default)",
    )

    args = parser.parse_args(argv)

    try:
        if args.command == "project":
            extra = (
                _load_negative_control(args.negative_control)
                if args.negative_control
                else ()
            )
            projection = build(args.root, stamp=args.stamp, extra=extra)
            _note(projection.warnings)
            if not projection.ok:
                return _fail(projection.violations)
            out = Path(args.out) if args.out else Path(args.root) / STORE_RELPATH
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(projection.text, encoding="utf-8")
            print(
                f"ticket-projection: OK — {len(projection.tickets)} ticket(s), "
                f"sha256={projection.sha256}"
            )
            return 0

        if args.command == "freshness":
            moment = parse_iso(args.now) if args.now else None
            max_age = DEFAULT_MAX_AGE_HOURS if args.max_age_hours is None else args.max_age_hours
            if getattr(args, "refresh", False):
                report, healed, refresh_detail = board_selfheal.self_heal(
                    assess,
                    args.root,
                    max_age_hours=max_age,
                    now=moment,
                    repo=args.repo,
                    stale_code=CODE_BOARD_STALE,
                )
            else:
                report = assess(args.root, max_age_hours=max_age, now=moment)
                healed, refresh_detail = False, ""
            if not report.ok:
                code = _fail(report.violations)
                if refresh_detail:
                    print(f"  FAIL  self-heal refresh failed: {refresh_detail}", file=sys.stderr)
                elif not getattr(args, "refresh", False):
                    print("  FAIL  no self-heal attempted — run with --refresh to try it", file=sys.stderr)
                return code
            if healed:
                print(f"ticket-freshness: OK (self-healed — {refresh_detail}) — {report.render()}")
            else:
                print(f"ticket-freshness: OK — {report.render()}")
            return 0

        result = verify(args.root, store=args.out)
        _note(result.warnings)
        if not result.ok:
            return _fail(result.violations)
        print(
            f"ticket-projection: OK — {result.ticket_count} ticket(s) rebuilt from the "
            f"ledgers, store={result.store}, sha256={result.sha256}"
        )
        return 0
    except CannotAssess as exc:
        label = "ticket-freshness" if args.command == "freshness" else "ticket-projection"
        print(f"{label}: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
