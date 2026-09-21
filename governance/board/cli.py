"""Operator CLI for the governance board enforcement gate (issue #143) and the
cross-repo execution boundary (issue #388).

Usage:
    python3 governance/board/cli.py check
    python3 governance/board/cli.py exceptions
    python3 governance/board/cli.py export-boundary [--repo OWNER/REPO] [--out PATH]
    python3 governance/board/cli.py boundary-check [--snapshot PATH] [--baseline PATH]

Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

``export-boundary`` is the only network-touching path here: it refreshes the
committed ``.board/boundary-snapshot.json`` via ``gh``. ``boundary-check`` is the
offline tri-state gate the verify script runs — it reads the committed snapshot,
applies the legacy quarantine baseline, and never calls the network.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import boundary  # noqa: E402
import board_selfheal  # noqa: E402
from gate import EXCEPTIONS_RELPATH, REPORT_RELPATH, load_exceptions, run_gate, write_report  # noqa: E402
from model import STATUS_CANNOT_ASSESS, STATUS_EXCEPTED, STATUS_NOT_OK, STATUS_OK, ExceptionInvalid  # noqa: E402

# --- boundary snapshot / baseline constants ----------------------------------
DEFAULT_REPO = "kushin77/agent-orchestrator"
BOUNDARY_SNAPSHOT_RELPATH = ".board/boundary-snapshot.json"
BOUNDARY_BASELINE_RELPATH = "governance/board/boundary-baseline.json"

# --- boundary snapshot freshness (issue #1631) -------------------------------
# The boundary gate never checked the committed snapshot's own age: a rotted
# ``.board/boundary-snapshot.json`` (tracker liveness resolved against a
# point-in-time export) reads as a clean OK even after its live truth flipped
# (measured: #358 closed 14h before the committed export, and the gate kept
# reporting OK). Mirrors governance/dispatch/queue_freshness.py's shape
# (Finding/assess/self-heal) — duplicated in miniature rather than imported:
# importing a sibling package's flat "cli"/"model" modules from here collides
# their bare basenames (see governance/board_selfheal.py's docstring).
BOUNDARY_MAX_AGE_HOURS = 72
CODE_BOUNDARY_STALE = "boundary-snapshot-stale"
BOUNDARY_REFRESH_COMMAND = "python3 governance/board/cli.py export-boundary"


@dataclass(frozen=True)
class _BoundaryFinding:
    code: str
    subject: str
    detail: str


@dataclass(frozen=True)
class _BoundaryFreshness:
    ok: bool
    findings: tuple = ()


def _parse_boundary_iso(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def assess_boundary_freshness(
    path: Any, *, max_age_hours: float | None = None, now: datetime | None = None
) -> _BoundaryFreshness:
    """Assert the committed boundary snapshot's ``generated_at`` is inside the
    tolerance. Never raises: an unreadable/missing/unaged snapshot is a named
    finding, same as a too-old one — the caller (``run_boundary_check``)
    already owns the missing/unreadable CANNOT-ASSESS wording for the case
    where no freshness check is even reachable.
    """
    tolerance = BOUNDARY_MAX_AGE_HOURS if max_age_hours is None else float(max_age_hours)
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    subject = str(path)
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _BoundaryFreshness(
            False, (_BoundaryFinding(CODE_BOUNDARY_STALE, subject, f"unreadable: {exc}"),)
        )
    raw = payload.get("generated_at") if isinstance(payload, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return _BoundaryFreshness(
            False,
            (_BoundaryFinding(CODE_BOUNDARY_STALE, subject, "the snapshot carries no generated_at"),),
        )
    try:
        generated = _parse_boundary_iso(raw)
    except ValueError as exc:
        return _BoundaryFreshness(
            False,
            (_BoundaryFinding(CODE_BOUNDARY_STALE, subject, f"generated_at {raw!r} is not a timestamp ({exc})"),),
        )
    age_hours = max(0.0, (moment - generated).total_seconds() / 3600.0)
    if age_hours <= tolerance:
        return _BoundaryFreshness(True)
    return _BoundaryFreshness(
        False,
        (
            _BoundaryFinding(
                CODE_BOUNDARY_STALE,
                subject,
                f"generated_at {raw} is {age_hours:.1f}h old, beyond the {tolerance:g}h tolerance "
                f"(refresh with: {BOUNDARY_REFRESH_COMMAND})",
            ),
        ),
    )

# A quarantine entry may excuse exactly one of the detector's three finding
# kinds; a code outside this vocabulary is reported as a stale quarantine.
BOUNDARY_VALID_CODES = frozenset(
    {
        boundary.FINDING_SELF_PARENT,
        boundary.FINDING_FOREIGN_REPO_ISSUE,
        boundary.FINDING_FOREIGN_REPO_DECLARATION,
    }
)

# The `Parent: #N` / `Blocked-by: #N` marker convention, duplicated from
# governance/dispatch/snapshot.py (the canonical producer of .board/snapshot.json).
# Duplicated deliberately: importing governance.dispatch.snapshot would drag the
# dispatch model and the lease policy into this module and create a fragile
# cross-lane import. If the convention changes there, mirror it here.
_PARENT_RE = re.compile(r"^\s*(?:parent|part[-_ ]of)\s*:\s*#?([0-9]+(?:\s*,\s*#?[0-9]+)*)", re.I | re.M)
_BLOCKED_RE = re.compile(r"^\s*blocked[-_ ]by\s*:\s*#?([0-9]+(?:\s*,\s*#?[0-9]+)*)", re.I | re.M)


def _find_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return here.parents[2]


def cmd_check(args: argparse.Namespace) -> int:
    root = _find_root()
    report = run_gate(root)
    write_report(report, root / REPORT_RELPATH)

    for check in report.checks:
        marker = {
            STATUS_OK: "OK",
            STATUS_NOT_OK: "FAIL",
            STATUS_CANNOT_ASSESS: "CANNOT-ASSESS",
            STATUS_EXCEPTED: "EXCEPTED",
        }[check.status]
        line = "  [%s] %s (%s)" % (marker, check.name, check.command)
        if check.exception_applied:
            line += " — exception: %s" % check.exception_applied
        print(line)
        if check.status in (STATUS_NOT_OK, STATUS_CANNOT_ASSESS) and check.output_tail:
            for out_line in check.output_tail.splitlines():
                print("      %s" % out_line)

    if report.expired_exceptions:
        print(
            "board-gate: expired exception(s), no longer suppressing failures: %s"
            % ", ".join(report.expired_exceptions)
        )
    for esc in report.escalations:
        print(
            "board-gate: ESCALATION — %s failed %d time(s): %s"
            % (esc["check"], esc["occurrences"], esc["reason"])
        )

    print("board-gate: status=%s (report: %s)" % (report.status, REPORT_RELPATH))

    if report.status == STATUS_OK:
        return 0
    if report.status == STATUS_CANNOT_ASSESS:
        return 2
    return 1


def cmd_exceptions(args: argparse.Namespace) -> int:
    root = _find_root()
    try:
        exceptions = load_exceptions(root / EXCEPTIONS_RELPATH)
    except ExceptionInvalid as exc:
        print("board-gate: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return 2
    if not exceptions:
        print("no active exceptions declared")
        return 0
    for exc in exceptions:
        active = "active" if exc.is_active() else "EXPIRED"
        print(
            "%s: %s (approved by %s, expires %s, %s)"
            % (exc.check, exc.reason, exc.approved_by, exc.expires, active)
        )
    return 0


def _numbers(blob: str) -> list[int]:
    return [int(part) for part in re.findall(r"[0-9]+", blob)]


def parse_edges(body: str) -> tuple[int | None, tuple[int, ...]]:
    """Extract (parent, blocked_by) from an issue body using the marker convention."""
    parent: int | None = None
    match = _PARENT_RE.search(body or "")
    if match:
        numbers = _numbers(match.group(1))
        if numbers:
            parent = numbers[0]
    blocked: list[int] = []
    for match in _BLOCKED_RE.finditer(body or ""):
        for number in _numbers(match.group(1)):
            if number not in blocked and number != parent:
                blocked.append(number)
    return parent, tuple(sorted(blocked))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_boundary_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project GitHub-shaped issue records into the boundary snapshot shape.

    Each record carries number, title, state, labels, milestone, parent,
    blocked_by, closed_at and body — the main .board/snapshot.json shape plus
    the issue body, which is what the detector reads. Parent/blocked_by are
    parsed from the body with the same marker convention as the main snapshot.
    """
    out: list[dict[str, Any]] = []
    for record in records:
        body = str(record.get("body", "") or "")
        parent, blocked = parse_edges(body)
        milestone = record.get("milestone") or {}
        milestone_title = (
            milestone.get("title", "") if isinstance(milestone, dict) else str(milestone or "")
        )
        labels = record.get("labels") or []
        label_names = sorted(
            str(label.get("name", "")) if isinstance(label, dict) else str(label)
            for label in labels
        )
        out.append(
            {
                "number": int(record["number"]),
                "title": str(record.get("title", "") or ""),
                "state": str(record.get("state", "open") or "open"),
                "labels": label_names,
                "milestone": milestone_title,
                "parent": parent,
                "blocked_by": list(blocked),
                "closed_at": str(record.get("closedAt", "") or ""),
                "body": body,
            }
        )
    return out


def fetch_boundary_records(repo: str, runner: Any = None) -> list[dict[str, Any]]:
    """Fetch issue records with ``gh`` (network). Raises RuntimeError on failure."""
    run = runner or subprocess.run
    cmd = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "all",
        "--limit",
        "1000",
        "--json",
        "number,title,state,milestone,labels,body,closedAt",
    ]
    result = run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "gh issue list failed (%s): %s" % (result.returncode, result.stderr.strip())
        )
    try:
        records = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise RuntimeError("gh issue list returned invalid JSON: %s" % exc) from exc
    if not isinstance(records, list):  # pragma: no cover - defensive
        raise RuntimeError("gh issue list returned a non-list payload")
    return records


def write_boundary_snapshot(
    records: Iterable[dict[str, Any]],
    path: Any,
    source: str = DEFAULT_REPO,
    generated_at: str | None = None,
) -> Path:
    """Write the boundary snapshot (an ``{"items": [...]}`` payload, the wrapper
    the detector's ``load_issues`` accepts, with generated_at/source metadata)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": generated_at or _now_iso(),
        "source": source,
        "items": build_boundary_records(records),
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return target


def _resolve_from_root(path: Any) -> Path:
    target = Path(path)
    if target.is_absolute():
        return target
    return _find_root() / target


def cmd_export_boundary(args: argparse.Namespace) -> int:
    try:
        records = fetch_boundary_records(args.repo)
    except (RuntimeError, OSError) as exc:
        print("export-boundary: CANNOT-ASSESS — %s" % exc, file=sys.stderr)
        return 2
    target = write_boundary_snapshot(records, _resolve_from_root(args.out), source=args.repo)
    print("export-boundary: wrote %d issue(s) to %s" % (len(records), target))
    return 0


def load_boundary_baseline(path: Any) -> list[dict[str, str]]:
    """Read the declared legacy quarantine from the boundary baseline document.

    Returns a list of ``{code, subject, tracked_by, reason}`` entries. Raises
    ValueError (never exits) when the document is not a quarantine baseline.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("boundary baseline must be an object")
    entries: list[dict[str, str]] = []
    for entry in raw.get("quarantine", []) or []:
        if not isinstance(entry, dict):
            raise ValueError("boundary baseline quarantine entry is not an object")
        code = entry.get("code")
        subject = entry.get("subject")
        tracked_by = entry.get("tracked_by")
        if not code or not subject or not tracked_by:
            raise ValueError(
                "boundary baseline quarantine entry needs code, subject and tracked_by"
            )
        entries.append(
            {
                "code": str(code),
                "subject": str(subject),
                "tracked_by": str(tracked_by),
                "reason": str(entry.get("reason", "")),
            }
        )
    return entries


def apply_boundary_baseline(
    findings: Sequence[Any],
    entries: Sequence[dict[str, str]],
    tracking: dict[str, str],
) -> tuple[list[Any], list[str]]:
    """Apply the quarantine to the detector's findings.

    Returns ``(surviving, stale)``. A quarantined ``(code, subject)`` pair is
    excused only while its tracking issue is open; a quarantine whose tracking
    issue has closed is reported as stale, and a quarantine naming an unknown
    finding kind is reported as stale too. The quarantine is a lease on legacy
    debt, not a permanent exemption (mirrors governance/lifecycle/audit.py).
    """
    excused = {(entry["code"], entry["subject"]) for entry in entries}
    surviving = [f for f in findings if (f.finding, "#%d" % f.issue) not in excused]
    stale: list[str] = []
    for entry in entries:
        if entry["code"] not in BOUNDARY_VALID_CODES:
            stale.append(
                "%s quarantined for unknown finding kind %r; retire the entry"
                % (entry["subject"], entry["code"])
            )
            continue
        state = str(tracking.get(entry["tracked_by"], "unknown")).lower()
        if state != "open":
            stale.append(
                "%s excused for %s but its tracking issue %s is %s; retire the quarantine entry"
                % (entry["subject"], entry["code"], entry["tracked_by"], state)
            )
    return surviving, stale


def run_boundary_check(
    snapshot_path: Any,
    baseline_path: Any,
    own_repo: str = DEFAULT_REPO,
    *,
    refresh: bool = False,
    now: datetime | None = None,
    max_age_hours: float | None = None,
) -> int:
    """The offline tri-state boundary gate. 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

    CANNOT-ASSESS covers every reason the check could not actually run — a
    missing snapshot, an unreadable snapshot, an empty snapshot, a snapshot
    whose records lack ``body`` (a missing body must never read as OK), a
    missing baseline, or (issue #1631) a snapshot older than the tolerance
    this gate honours whose ONE refresh verb (``export-boundary``) either was
    not attempted (no ``--refresh``) or failed. A rotted snapshot never
    silently reads as OK-with-zero-findings. NOT-OK covers any non-quarantined
    cross-repo child and any quarantine entry whose tracking issue is no
    longer open.
    """
    snapshot = Path(snapshot_path)

    if snapshot.is_file():
        if refresh:
            verdict, _healed, detail = board_selfheal.self_heal(
                assess_boundary_freshness,
                snapshot,
                max_age_hours=max_age_hours,
                now=now,
                stale_code=CODE_BOUNDARY_STALE,
                # --out targets the SAME path being assessed — a self-heal on a
                # scratch copy (the verify-time gate) never rewrites the
                # committed snapshot out from under git (issue #1631's own
                # scope note: a refresh "must not ride in a feature lane").
                command=[
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "export-boundary",
                    "--out",
                    str(snapshot),
                ],
            )
        else:
            verdict = assess_boundary_freshness(snapshot, max_age_hours=max_age_hours, now=now)
            detail = ""
        if not verdict.ok:
            why = "; ".join(f.detail for f in verdict.findings)
            if refresh:
                why = f"{why} (refresh attempted: {detail or 'no effect'})"
            else:
                why = f"{why} (no --refresh attempted)"
            print(
                "boundary: CANNOT-ASSESS — snapshot stale and refresh impossible: %s" % why,
                file=sys.stderr,
            )
            return boundary.EXIT_CANNOT_ASSESS

    try:
        issues = boundary.load_issues(snapshot)
    except FileNotFoundError:
        print(
            "boundary: CANNOT-ASSESS — boundary snapshot not found: %s" % snapshot,
            file=sys.stderr,
        )
        return boundary.EXIT_CANNOT_ASSESS
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            "boundary: CANNOT-ASSESS — boundary snapshot unreadable (%s): %s"
            % (snapshot, exc),
            file=sys.stderr,
        )
        return boundary.EXIT_CANNOT_ASSESS

    if not issues:
        print(
            "boundary: CANNOT-ASSESS — boundary snapshot is empty: %s" % snapshot,
            file=sys.stderr,
        )
        return boundary.EXIT_CANNOT_ASSESS

    # A snapshot that omits `body` cannot be assessed: the declaration check
    # reads the body, so a missing field must never read as OK.
    if any(issue.get("body") is None for issue in issues):
        print(
            "boundary: CANNOT-ASSESS — boundary snapshot lacks body — cannot assess",
            file=sys.stderr,
        )
        return boundary.EXIT_CANNOT_ASSESS

    try:
        entries = load_boundary_baseline(baseline_path)
    except FileNotFoundError:
        print(
            "boundary: CANNOT-ASSESS — boundary baseline not found: %s" % baseline_path,
            file=sys.stderr,
        )
        return boundary.EXIT_CANNOT_ASSESS
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            "boundary: CANNOT-ASSESS — boundary baseline unreadable (%s): %s"
            % (baseline_path, exc),
            file=sys.stderr,
        )
        return boundary.EXIT_CANNOT_ASSESS

    policy = boundary.BoundaryPolicy(own_repo=own_repo)
    findings = boundary.check_issues(policy, issues)

    tracking: dict[str, str] = {}
    for issue in issues:
        number = issue.get("number")
        if number is not None:
            tracking["#%s" % number] = str(issue.get("state", "") or "")

    surviving, stale = apply_boundary_baseline(findings, entries, tracking)
    problems = list(stale)
    problems.extend(
        "#%d [%s] %s" % (item.issue, item.finding, item.detail) for item in surviving
    )

    if problems:
        for message in problems:
            print("boundary: NOT-OK — %s" % message, file=sys.stderr)
        print(
            "boundary: NOT-OK — %d finding(s)/stale quarantine(s) across %d issue(s)"
            % (len(problems), len(issues)),
            file=sys.stderr,
        )
        return boundary.EXIT_NOT_OK

    print(
        "boundary: OK — %d issue(s), %d quarantined legacy finding(s), no boundary violation"
        % (len(issues), len(findings))
    )
    return boundary.EXIT_OK


def cmd_boundary_check(args: argparse.Namespace) -> int:
    return run_boundary_check(
        _resolve_from_root(args.snapshot),
        _resolve_from_root(args.baseline),
        own_repo=args.policy_own_repo,
        refresh=args.refresh,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="board-gate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("exceptions").set_defaults(func=cmd_exceptions)

    export_p = sub.add_parser(
        "export-boundary",
        help="refresh the committed .board/boundary-snapshot.json from GitHub (network)",
    )
    export_p.add_argument("--repo", default=DEFAULT_REPO, help="OWNER/REPO to export")
    export_p.add_argument(
        "--out",
        default=BOUNDARY_SNAPSHOT_RELPATH,
        help="snapshot path (default .board/boundary-snapshot.json)",
    )
    export_p.set_defaults(func=cmd_export_boundary)

    check_p = sub.add_parser(
        "boundary-check",
        help="offline tri-state boundary gate (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS)",
    )
    check_p.add_argument("--snapshot", default=BOUNDARY_SNAPSHOT_RELPATH)
    check_p.add_argument("--baseline", default=BOUNDARY_BASELINE_RELPATH)
    check_p.add_argument(
        "--policy-own-repo", default=DEFAULT_REPO, dest="policy_own_repo"
    )
    check_p.add_argument(
        "--refresh",
        action="store_true",
        help="self-heal a stale committed snapshot via export-boundary before failing (issue #1631)",
    )
    check_p.set_defaults(func=cmd_boundary_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
