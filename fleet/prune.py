#!/usr/bin/env python3
"""fleet/prune.py — bounded retention for the fleet's own runtime state.

WHY this exists (issue #280)
    ``.fleet/`` is the session fleet's mailbox and audit surface, and every part
    of it was append-only: thousands of ``outbox`` entries, dozens of ``sent``
    and ``done``, a ~786 KB ``slog.jsonl`` and an unbounded ``runs.jsonl``.
    Nothing rotated or pruned it — ``scripts/prune-worktrees.sh`` reclaims lane
    *worktrees*, not the mailbox — so the directory grew without bound while the
    brain only ever read the newest ``--limit`` replies. This is the missing
    retention job, code-native so cron owns it and no human is involved.

WHAT it prunes (safe by construction)
    * ``outbox/``, ``sent/``, ``done/`` and the brain rung's same three
      directories: entries **older than** ``--retention-days`` whose message is
      not referenced by a live run or a live claim.
    * ``slog.jsonl`` and ``runs.jsonl``: rotated by size (``--max-log-bytes``),
      keeping a bounded number of generations (``--keep-generations``).

WHAT it never touches (the safety floor — enforced here, proved in the tests)
    * ``inbox/`` (and ``brain/inbox/``) — an un-consumed directive is pending
      work, not garbage. Reporting consumes it; only then may it age out.
    * ``runs/`` — the live run markers themselves.
    * any mailbox entry referenced by a **live run marker** (``runs/<id>.json``
      whose ``pid`` is still alive) — deleting a live run's artifact breaks a
      dispatch in flight.
    * any mailbox entry referenced by a **live claim** (``.board/claims.jsonl``
      replayed: a ``claim``/``take-over`` holds its issue until a
      ``release``/``reap``).
    * ``reported/``, ``waves/``, ``lifecycle/``, ``lanes/`` and every
      heartbeat/lock file — not retention's business.
    * a file that cannot be read: fail closed, keep it.

Fail-closed rule: when liveness cannot be *determined* (an unreadable run
marker, an unreadable claim ledger) the pruner keeps the artifact and reports
the uncertainty. Keeping a few stale bytes is cheap; deleting a live run's
artifact is not.

Runtime output lives entirely under the gitignored ``.fleet/``; this module is
tracked. Dry-run by default — ``--apply`` performs the plan.

    python3 fleet/prune.py status                     # what .fleet holds now
    python3 fleet/prune.py run                        # dry-run: what it WOULD do
    python3 fleet/prune.py run --apply                # do it

Exit codes (guardrails/honesty tri-state): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import runtime

ROOT = Path(__file__).resolve().parent.parent
FLEET_DIR = runtime.FLEET_DIR
RUNS_DIR = FLEET_DIR / "runs"
CLAIMS_LEDGER = ROOT / ".board" / "claims.jsonl"

# Mailbox directories whose entries may age out. `inbox` is deliberately absent:
# it is keyed by pending work, not by age.
PRUNABLE_DIRS = ("outbox", "sent", "done", "brain/sent", "brain/done", "brain/outbox")
# Named so a reader sees the policy without reading the code: never pruned.
PROTECTED_DIRS = ("inbox", "brain/inbox", "runs", "reported", "waves", "lifecycle", "lanes")

# The append-only ledgers, rotated by size rather than deleted.
LOGS = (FLEET_DIR / "slog.jsonl", FLEET_DIR / "runs.jsonl")

DEFAULT_RETENTION_DAYS = 7.0
DEFAULT_MAX_LOG_BYTES = 2 * 1024 * 1024
DEFAULT_KEEP_GENERATIONS = 3

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

# Reasons an artifact is kept, in the order they are checked.
KEEP_LIVE_RUN = "live-run"
KEEP_LIVE_CLAIM = "live-claim"
KEEP_UNREADABLE = "unreadable"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pid_alive(pid: int) -> bool:
    """Is ``pid`` still running? A PID we may not signal still counts as alive."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def live_run_ids(runs_dir: Path) -> set[str]:
    """Directive ids whose run marker is *live* — its ``pid`` is still running.

    Fail-closed: a marker that cannot be read or parsed is reported as live, so
    an unreadable marker protects its artifacts instead of exposing them.
    """
    live: set[str] = set()
    if not runs_dir.is_dir():
        return live
    for marker in sorted(runs_dir.glob("*.json")):
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
            pid = int(record.get("pid"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
            live.add(marker.stem)
            continue
        if _pid_alive(pid):
            live.add(marker.stem)
    return live


def live_claims(ledger: Path) -> tuple[set[str], set[int]] | None:
    """``(directive ids, issue numbers)`` held by live claims, or ``None``.

    The ledger is the claim tool's append-only ``.board/claims.jsonl``. We replay
    only what retention needs — a ``claim``/``take-over`` holds its issue until a
    ``release``/``reap`` clears it (``governance/dispatch/model.py``) — and read a
    missing ledger as "no claims". An unreadable or malformed ledger returns
    ``None`` (unknown) so the caller fails closed rather than guessing.

    A claim whose TTL has elapsed is still reported as held: over-protecting is
    the safe direction, and the claim tool itself reaps the record.
    """
    if not ledger.exists():
        return set(), set()
    try:
        text = ledger.read_text(encoding="utf-8")
    except OSError:
        return None
    held: dict[int, dict] = {}
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict):
            return None
        issue = event.get("issue")
        if not isinstance(issue, int):
            continue
        kind = event.get("event", "")
        if kind in ("claim", "take-over"):
            held[issue] = event
        elif kind in ("release", "reap"):
            held.pop(issue, None)
    directives = {str(e["directive_id"]) for e in held.values() if e.get("directive_id")}
    return directives, set(held)


def _message_issue(message: object) -> int | None:
    """The issue a mailbox message names, whether top-level or in its task block."""
    if not isinstance(message, dict):
        return None
    issue = message.get("issue")
    if issue is None and isinstance(message.get("task"), dict):
        issue = message["task"].get("issue")
    try:
        return int(issue) if issue is not None else None
    except (TypeError, ValueError):
        return None


def keep_reason(
    path: Path,
    live_runs: set[str],
    claim_directives: set[str],
    claim_issues: set[int],
) -> str | None:
    """Why ``path`` must be kept, or ``None`` when it is safe to prune."""
    if path.stem in live_runs:
        return KEEP_LIVE_RUN
    if path.stem in claim_directives:
        return KEEP_LIVE_CLAIM
    try:
        message = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return KEEP_UNREADABLE
    except OSError:
        return KEEP_UNREADABLE
    reference = str(message.get("correlation_id") or "") if isinstance(message, dict) else ""
    if reference and reference in live_runs:
        return KEEP_LIVE_RUN
    if reference and reference in claim_directives:
        return KEEP_LIVE_CLAIM
    issue = _message_issue(message)
    if issue is not None and issue in claim_issues:
        return KEEP_LIVE_CLAIM
    return None


@dataclass(frozen=True)
class Removal:
    """One file the plan would delete."""

    path: Path
    size: int


@dataclass(frozen=True)
class Rotation:
    """One log the plan would rotate: newest content becomes ``<name>.1``."""

    path: Path
    size: int
    keep: int
    threshold: int

    def drop(self) -> Path:
        """The oldest generation, which falls off the end of the window."""
        return self.path.with_name(f"{self.path.name}.{self.keep}")

    def shift(self) -> list[tuple[Path, Path]]:
        """Generation renames, oldest first, so no step overwrites its source."""
        return [
            (self.path.with_name(f"{self.path.name}.{n}"), self.path.with_name(f"{self.path.name}.{n + 1}"))
            for n in range(self.keep - 1, 0, -1)
        ]


@dataclass
class Plan:
    """What a prune run would do, computed before anything is touched."""

    removals: list[Removal] = field(default_factory=list)
    kept: dict[str, int] = field(default_factory=dict)
    rotations: list[Rotation] = field(default_factory=list)
    per_dir: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    claims_unknown: bool = False

    @property
    def removed_bytes(self) -> int:
        return sum(item.size for item in self.removals)


def build_plan(
    *,
    fleet_dir: Path,
    ledger: Path,
    retention_days: float,
    max_log_bytes: int,
    keep_generations: int,
    now: datetime | None = None,
) -> Plan:
    """Compute the prune plan. Reads only; nothing is modified."""
    moment = now or _now()
    cutoff = moment - timedelta(days=retention_days)

    live_runs = live_run_ids(fleet_dir / "runs")
    claims = live_claims(ledger)
    plan = Plan()
    if claims is None:
        plan.claims_unknown = True
        claim_directives: set[str] = set()
        claim_issues: set[int] = set()
        plan.notes.append(
            "claim ledger unreadable — mailbox entries are KEPT (fail-closed); "
            "log rotation still runs"
        )
    else:
        claim_directives, claim_issues = claims

    plan.notes.append(f"live run marker(s): {len(live_runs)}")
    plan.notes.append("live claim(s): unknown" if claims is None else f"live claim(s): {len(claim_issues)}")

    for relative in PRUNABLE_DIRS:
        directory = fleet_dir / relative
        if not directory.is_dir():
            continue
        candidates = 0
        prunable: list[Removal] = []
        for path in sorted(directory.glob("*.json")):
            if not path.is_file():
                continue
            candidates += 1
            # A live claim's identity is unknowable when the ledger is unreadable,
            # so every mailbox entry is kept and only logs rotate.
            if claims is None:
                plan.kept[KEEP_LIVE_CLAIM] = plan.kept.get(KEEP_LIVE_CLAIM, 0) + 1
                continue
            reason = keep_reason(path, live_runs, claim_directives, claim_issues)
            if reason is not None:
                plan.kept[reason] = plan.kept.get(reason, 0) + 1
                continue
            try:
                stat = path.stat()
            except OSError:
                plan.kept[KEEP_UNREADABLE] = plan.kept.get(KEEP_UNREADABLE, 0) + 1
                continue
            if datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc) >= cutoff:
                continue
            prunable.append(Removal(path=path, size=stat.st_size))
        plan.removals.extend(prunable)
        plan.per_dir[relative] = (len(prunable), candidates, sum(item.size for item in prunable))

    for log in LOGS:
        if not log.is_file():
            continue
        try:
            size = log.stat().st_size
        except OSError:
            continue
        if size >= max_log_bytes and keep_generations > 0:
            plan.rotations.append(
                Rotation(path=log, size=size, keep=keep_generations, threshold=max_log_bytes)
            )

    return plan


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def render(plan: Plan, *, applied: bool) -> str:
    """A greppable report: counts and bytes, dry-run or applied."""
    verb = "APPLIED" if applied else "DRY-RUN (nothing changed — pass --apply to act)"
    lines = [f"fleet-prune: {verb}"]
    for note in plan.notes:
        lines.append(f"  {note}")
    for relative, (count, candidates, share) in sorted(plan.per_dir.items()):
        lines.append(f"  prune {relative:<14} {count}/{candidates} entry(ies)  {_human(share)}")
    if plan.kept:
        detail = " ".join(f"{reason}={count}" for reason, count in sorted(plan.kept.items()))
        lines.append(f"  protected      {sum(plan.kept.values())} entr(ies): {detail}")
    for rotation in plan.rotations:
        lines.append(
            f"  rotate {rotation.path.name:<14} {_human(rotation.size)} >= "
            f"{_human(rotation.threshold)} → {rotation.path.name}.1 "
            f"({rotation.keep} generation(s) kept)"
        )
    lines.append(
        f"  TOTAL {'removed' if applied else 'would remove'} {len(plan.removals)} file(s), "
        f"{_human(plan.removed_bytes)}; {'rotated' if applied else 'would rotate'} "
        f"{len(plan.rotations)} log(s)"
    )
    return "\n".join(lines)


def apply_plan(plan: Plan) -> tuple[list[str], list[str]]:
    """Perform the plan. Returns ``(failures, done)`` — one entry per action.

    A failed action is reported and skipped, never retried blindly: the pruner
    must not turn a permission error into a half-deleted mailbox.
    """
    failures: list[str] = []
    done: list[str] = []
    for removal in plan.removals:
        try:
            removal.path.unlink()
        except OSError as exc:
            failures.append(f"{removal.path}: {exc}")
        else:
            done.append(f"removed {removal.path}")
    for rotation in plan.rotations:
        try:
            _rotate(rotation)
        except OSError as exc:
            failures.append(f"{rotation.path}: {exc}")
        else:
            done.append(f"rotated {rotation.path} (kept {rotation.keep} generation(s))")
    return failures, done


def _rotate(rotation: Rotation) -> None:
    """Shift generations, move the live log to ``.1`` and recreate it empty.

    Rename-then-recreate, not truncate-in-place: every writer here opens the log
    with ``O_APPEND`` per write (``channel._slog``, ``telemetry.append_record``),
    so the next write lands in the fresh file, and the rotated generation holds
    the complete previous content — nothing is lost.
    """
    drop = rotation.drop()
    if drop.exists():
        drop.unlink()
    for source, target in rotation.shift():
        if source.exists():
            os.replace(source, target)
    os.replace(rotation.path, rotation.path.with_name(f"{rotation.path.name}.1"))
    rotation.path.touch()


def _resolve_fleet_dir(value: Path | None) -> Path:
    return Path(value) if value is not None else FLEET_DIR


def _resolve_ledger(value: Path | None) -> Path:
    return Path(value) if value is not None else CLAIMS_LEDGER


def cmd_run(args: argparse.Namespace) -> int:
    fleet_dir = _resolve_fleet_dir(args.fleet_dir)
    if not fleet_dir.is_dir():
        print(f"fleet-prune: CANNOT-ASSESS — no fleet directory at {fleet_dir}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    if args.keep_generations < 0:
        print("fleet-prune: REFUSED — --keep-generations must be >= 0", file=sys.stderr)
        return EXIT_NOT_OK

    plan = build_plan(
        fleet_dir=fleet_dir,
        ledger=_resolve_ledger(args.ledger),
        retention_days=args.retention_days,
        max_log_bytes=args.max_log_bytes,
        keep_generations=args.keep_generations,
    )

    if not args.apply:
        print(render(plan, applied=False))
        return EXIT_OK

    failures, done = apply_plan(plan)
    print(render(plan, applied=True))
    for entry in done:
        print(f"  done: {entry}")
    for failure in failures:
        print(f"  FAILED: {failure}", file=sys.stderr)
    if failures:
        print(f"fleet-prune: NOT-OK — {len(failures)} action(s) failed", file=sys.stderr)
        return EXIT_NOT_OK
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    fleet_dir = _resolve_fleet_dir(args.fleet_dir)
    if not fleet_dir.is_dir():
        print(f"fleet-prune: CANNOT-ASSESS — no fleet directory at {fleet_dir}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    print(f"fleet-prune: {fleet_dir}")
    for relative in PRUNABLE_DIRS + PROTECTED_DIRS:
        directory = fleet_dir / relative
        if not directory.is_dir():
            continue
        entries = [p for p in directory.iterdir() if p.is_file()]
        size = sum(p.stat().st_size for p in entries if p.exists())
        print(f"  {relative:<14} {len(entries):>6} file(s)  {_human(size)}")
    for log in LOGS:
        if log.is_file():
            size = log.stat().st_size
            generations = len(sorted(log.parent.glob(f"{log.name}.*")))
            print(f"  {log.name:<14} {_human(size)}  ({generations} generation(s))")
    # The honest total: everything under .fleet, including the surfaces the
    # per-directory table does not enumerate (the rung logs, `paused`, …) —
    # otherwise the number is smaller than `du` and reads as a false claim.
    total = sum(path.stat().st_size for path in fleet_dir.rglob("*") if path.is_file())
    print(f"  total          {_human(total)} (all of {fleet_dir.name})")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleet-prune",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="prune the mailboxes + rotate the ledgers (dry-run unless --apply)")
    run.add_argument("--apply", action="store_true", help="perform the plan (default: dry-run)")
    run.add_argument("--retention-days", type=float, default=DEFAULT_RETENTION_DAYS)
    run.add_argument("--max-log-bytes", type=int, default=DEFAULT_MAX_LOG_BYTES)
    run.add_argument("--keep-generations", type=int, default=DEFAULT_KEEP_GENERATIONS)
    run.add_argument("--fleet-dir", type=Path, default=None)
    run.add_argument("--ledger", type=Path, default=None)
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="report what .fleet holds (read-only)")
    status.add_argument("--fleet-dir", type=Path, default=None)
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
