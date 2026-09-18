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

from governance.landing import evidence as evidence_mod  # noqa: E402
from governance.landing.engine import (  # noqa: E402
    EXIT_CANNOT_ASSESS,
    EXIT_NOT_OK,
    EXIT_OK,
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


def _rev_parse(root: Path, rev: str) -> Optional[str]:
    """A local ref/rev resolved to its full SHA — NO fetch, NO verify.

    Mirrors `fleet/brain.py`'s `current_master_head()` (same reasoning: a
    manual `gh pr merge` bypasses `land()` entirely, so master's head can move
    without any lane ever running this file — `git rev-parse` only reads
    whatever ref this checkout already has). `None` when the rev cannot be
    resolved at all, never an exception the caller has to guess about.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "-q", rev],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def cmd_write_master_attestation(args: argparse.Namespace) -> int:
    """Publish master's own health from a `scripts/verify.sh` attestation.

    RCA 2026-09-17 fix #5 follow-up (#1114): `governance/landing/engine.py`'s
    `land()` writes `.fleet/master-attestation.json` after every FLEET-DRIVEN
    merge, but an OPERATOR's manual `gh pr merge` never goes through `land()`
    at all — so after any manual merge, master's head moves and
    `fleet/brain.py`'s dispatch pre-check reads CANNOT-ASSESS (a stale-head
    attestation) until the next fleet land, which may be a long time. This
    subcommand closes that gap from the OTHER direction a verify can run from:
    a plain `make verify` (or `bash scripts/verify.sh verify`) at whatever
    commit is currently checked out.

    It is deliberately NOT wired into `scripts/verify.sh` itself — that file
    is currently held by an open PR (#1127, touching `scripts/verify.sh`), so
    editing it here would collide with a sibling lane's file. This
    subcommand + the `make master-attestation` target are the seam a
    follow-up can hook `scripts/verify.sh` into once #1127 lands (or an
    operator/cron job can call it directly, right after `make verify`, in the
    meantime).

    Publishes ONLY when the checkout's HEAD is `origin/master`'s current head
    (resolved locally, no fetch — a verify run on a lane head must never be
    mistaken for a measurement of master) AND the verify attestation it reads
    is green. Anything else is a no-op (exit 0, a named SKIPPED line) — this
    command deliberately never fails a caller's script merely because the
    condition to publish did not hold; a red verify or a non-master head is an
    ordinary, expected outcome, not an error in this subcommand's own
    execution.
    """
    root = Path(args.root).resolve() if args.root else ROOT
    attestation_path = Path(args.attestation)
    if not attestation_path.is_absolute():
        attestation_path = root / attestation_path
    try:
        payload = json.loads(attestation_path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"write-master-attestation: CANNOT-ASSESS — {attestation_path} unreadable ({exc})", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    except ValueError as exc:
        print(f"write-master-attestation: CANNOT-ASSESS — {attestation_path} is not valid JSON ({exc})", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    if not isinstance(payload, dict):
        print(f"write-master-attestation: CANNOT-ASSESS — {attestation_path} is not a JSON object", file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    exit_code = payload.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        print(f"write-master-attestation: CANNOT-ASSESS — {attestation_path} carries no exit_code", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    # scripts/verify.sh's own attestation names the commit `git_sha` (a
    # different shape from the merge-gate/master-attestation `commit` field);
    # a bare `commit` is accepted too so this also works against a
    # merge-attestation-shaped file, without a second reader.
    verified_sha = str(payload.get("git_sha") or payload.get("commit") or "").strip()

    head = _rev_parse(root, "HEAD")
    master_head = _rev_parse(root, "origin/master")
    if head is None or master_head is None:
        print(
            "write-master-attestation: SKIPPED — HEAD or origin/master could not be resolved locally "
            "(no fetch was run); nothing published",
        )
        return EXIT_OK
    if not evidence_mod.same_commit(head, master_head):
        print(
            f"write-master-attestation: SKIPPED — checkout HEAD ({head}) is not origin/master's head "
            f"({master_head}); a lane's own verify never publishes master's health",
        )
        return EXIT_OK
    if exit_code != 0:
        print(
            f"write-master-attestation: SKIPPED — verify is red (exit_code={exit_code}) at master's head "
            f"({master_head}); a red verify is not published — dispatch reading absence as CANNOT-ASSESS "
            "is the correct posture for a red master, not a regression",
        )
        return EXIT_OK

    attestation = evidence_mod.Attestation(
        path=attestation_path,
        state=evidence_mod.STATE_READ,
        result=str(payload.get("result") or ""),
        rc=exit_code,
        commit=verified_sha or master_head,
        branch=str(payload.get("branch") or ""),
        timestamp=str(payload.get("timestamp") or ""),
        verified_by=str(payload.get("verified_by") or ""),
    )
    target = Path(args.out).resolve() if args.out else (root / evidence_mod.MASTER_ATTESTATION_REL)
    try:
        written = evidence_mod.write_master_attestation(target, attestation, commit=master_head)
    except OSError as exc:
        print(f"write-master-attestation: CANNOT-ASSESS — could not write {target} ({exc})", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    print(f"write-master-attestation: wrote {written} (commit={master_head})")
    return EXIT_OK


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

    write_master_cmd = sub.add_parser(
        "write-master-attestation",
        help="publish master's own health from a verify attestation (RCA fix #5 follow-up, #1114)",
    )
    write_master_cmd.add_argument(
        "--attestation",
        default=str(Path(".verify") / "attestation.json"),
        help="the scripts/verify.sh attestation to read (default: .verify/attestation.json)",
    )
    write_master_cmd.add_argument("--root", default="", help="the checkout to check HEAD/origin-master in (default: this repository)")
    write_master_cmd.add_argument(
        "--out",
        default="",
        help="where to write master's attestation (default: <root>/.fleet/master-attestation.json)",
    )
    write_master_cmd.set_defaults(func=cmd_write_master_attestation)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "land" and args.issue <= 0:
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
