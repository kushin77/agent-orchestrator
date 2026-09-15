#!/usr/bin/env python3
"""Landing command line (#764) — one green lane, landed, with no human step.

    python3 governance/landing/cli.py land --issue 764            # DRY RUN (default)
    python3 governance/landing/cli.py land --issue 764 --apply    # land it
    AO_LAND_APPLY=1 python3 governance/landing/cli.py land --issue 764

`make land ISSUE=764` is the declared entry point (the code-native automation
path run by the ops runner and cron — never a workflow file, GR-15).

**Dry run is the default.** Without an explicit opt-in (``--apply`` or
``AO_LAND_APPLY=1``) the driver reads the lane, consults the merge verdict, and
prints exactly what it *would* do — it pushes nothing, opens nothing, merges
nothing and deletes nothing, and it writes no file at all.

Exit-code contract, the repo's honesty tri-state (issue #28): 0 OK /
1 NOT-OK / 2 CANNOT-ASSESS. A refusal always names what it checked.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.landing.engine import (  # noqa: E402
    EXIT_CANNOT_ASSESS,
    LandingEngine,
    LandingRequest,
    describe,
)
from governance.landing.ports import GitHubOps, PortError, RecordingOps  # noqa: E402
from governance.landing.verdict import MergeSeamError  # noqa: E402

#: The AI-assistance line the driver declares when the caller supplies none.
#: It states what is true about the landing itself (a code-native driver landed
#: it) rather than guessing which runtime authored the change; a lane that knows
#: its authoring runtime passes ``--ai-assistance`` (or AO_AI_ASSISTANCE).
DEFAULT_AI_ASSISTANCE = "agent-orchestrator land driver (code-native/cron)"


def _git_config_name() -> str:
    try:
        proc = subprocess.run(
            ["git", "config", "user.name"], capture_output=True, text=True, cwd=str(ROOT)
        )
    except OSError:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def resolve_author(explicit: str) -> str:
    """Who is landing: the flag, the session identity, git's name, or a default."""
    for candidate in (explicit, os.environ.get("AO_AGENT", ""), _git_config_name()):
        if candidate and candidate.strip():
            return candidate.strip()
    return "landing-driver"


def resolve_ai_assistance(explicit: str) -> str:
    for candidate in (explicit, os.environ.get("AO_AI_ASSISTANCE", "")):
        if candidate and candidate.strip():
            return candidate.strip()
    return DEFAULT_AI_ASSISTANCE


def cmd_land(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else ROOT
    request = LandingRequest(
        issue=args.issue,
        root=root,
        branch=args.branch or "",
        base=args.base,
        author=resolve_author(args.author),
        title=args.title or "",
        subject=args.title or args.branch or f"issue-{args.issue}",
        ai_assistance=resolve_ai_assistance(args.ai_assistance),
        notes_file=Path(args.notes_file).resolve() if args.notes_file else None,
        attestation_file=Path(args.attestation).resolve() if args.attestation else None,
        apply=bool(args.apply or os.environ.get("AO_LAND_APPLY") == "1"),
        owner_carve_out=not args.no_owner_carve_out,
        baseline_file=Path(args.baseline).resolve() if args.baseline else None,
        lane_record_file=Path(args.lane_record).resolve() if args.lane_record else None,
        baseline_rev=args.baseline_rev or "",
    )
    reads = GitHubOps(root, base=request.base)
    ops = reads if request.apply else RecordingOps(reads=reads)
    result = LandingEngine(ops, request).land()
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(describe(result))
        if result.report_path:
            print(f"record: {result.report_path}")
    return result.rc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="landing", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    land_cmd = sub.add_parser("land", help="land one issue's lane, or report its terminal state")
    land_cmd.add_argument("--issue", type=int, required=True, help="the issue whose lane is being landed")
    land_cmd.add_argument("--root", default="", help="the checkout/worktree to land from (default: this repository)")
    land_cmd.add_argument("--branch", default="", help="the lane branch (default: issue-<n>)")
    land_cmd.add_argument("--base", default="master", help="the base branch the PR targets (default: master)")
    land_cmd.add_argument("--attestation", default="", help="the pre-flight attestation to judge (default: <root>/.verify/merge-attestation.json)")
    land_cmd.add_argument("--title", default="", help="the PR title (default: the lane's latest commit subject)")
    land_cmd.add_argument("--author", default="", help="who is landing (default: AO_AGENT, then git user.name)")
    land_cmd.add_argument("--notes-file", default="", help="extra prose for the PR body's What-changed section")
    land_cmd.add_argument("--ai-assistance", default="", help="the PR body's AI-assistance declaration")
    land_cmd.add_argument("--apply", action="store_true", help="actually land it (default: dry run, no remote change)")
    land_cmd.add_argument(
        "--no-owner-carve-out",
        action="store_true",
        help="disable the owner autonomous-merge carve-out (a self-merge is then refused by governance/merge's rule)",
    )
    land_cmd.add_argument("--json", action="store_true", help="emit the landing record as JSON")
    land_cmd.add_argument(
        "--baseline",
        default="",
        help=(
            "a recorded clean-master sweep to attribute pre-existing suite reds against "
            "(default: measure origin/master; or set AO_LAND_BASELINE_RECORD)"
        ),
    )
    land_cmd.add_argument(
        "--lane-record",
        default="",
        help="the lane's own sweep record (default: <root>/.verify/test-results.json)",
    )
    land_cmd.add_argument(
        "--baseline-rev",
        default="",
        help="the rev the baseline is measured at (default: origin/master)",
    )
    land_cmd.set_defaults(func=cmd_land)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.issue <= 0:
        print("land: CANNOT-ASSESS — --issue must be a positive issue number", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    try:
        return args.func(args)
    except MergeSeamError as exc:
        print(f"land: CANNOT-ASSESS — the merge verdict could not be consulted ({exc})", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    except PortError as exc:
        print(f"land: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS


if __name__ == "__main__":
    raise SystemExit(main())
