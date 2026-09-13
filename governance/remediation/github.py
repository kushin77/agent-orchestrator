"""Routing remediation issues to GitHub (issue #142).

The only network-touching path in this package, matching the convention in
`governance/dispatch/snapshot.py`: an injectable ``runner`` (defaulting to
``subprocess.run``) so the routing logic is testable without a real ``gh``
call, and a `RuntimeError` on any non-zero exit rather than a silent no-op
(no-false-green).

Suppressing duplicates against *GitHub's* existing issues (as opposed to the
in-run merge in `generator.merge`) works by searching open issues carrying the
`remediation:auto` label for one whose body embeds the same
``<!-- remediation-key: ... -->`` marker `RemediationIssue.body()` writes. A
match gets a comment with the fresh evidence instead of a new issue —
"suppresses duplicates and merges repeated findings into an actionable
issue" (issue #142 acceptance criterion). No match creates one.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from remediation_model import ESCALATION_LABEL, REMEDIATION_LABEL, RemediationIssue

Runner = Callable[..., "subprocess.CompletedProcess"]

GOVERNANCE_BOARD_LABEL = "governance-board"
MARKER_PREFIX = "<!-- remediation-key: "


@dataclass
class RoutingResult:
    """What happened to one RemediationIssue when routed."""

    key: str
    action: str  # "created", "commented", "dry-run"
    number: Optional[int] = None
    escalated: bool = False


def _run(cmd: Sequence[str], runner: Optional[Runner] = None) -> subprocess.CompletedProcess:
    run = runner or subprocess.run
    return run(list(cmd), capture_output=True, text=True)


def existing_remediation_issues(
    repo: str, *, runner: Optional[Runner] = None
) -> List[dict]:
    """Open issues already carrying the auto-remediation label, for dedup
    matching. Raises RuntimeError on a `gh` failure (the caller decides
    whether CANNOT-ASSESS applies; this module never swallows an error into a
    false "no duplicates")."""
    cmd = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--label",
        REMEDIATION_LABEL,
        "--state",
        "open",
        "--limit",
        "500",
        "--json",
        "number,title,body,labels",
    ]
    result = _run(cmd, runner)
    if result.returncode != 0:
        raise RuntimeError(
            "gh issue list failed (%d): %s" % (result.returncode, result.stderr.strip())
        )
    try:
        records = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise RuntimeError("gh issue list returned invalid JSON: %s" % exc) from exc
    if not isinstance(records, list):  # pragma: no cover - defensive
        raise RuntimeError("gh issue list returned a non-list payload")
    return records


def _marker_key(body: str) -> Optional[str]:
    idx = body.find(MARKER_PREFIX)
    if idx < 0:
        return None
    start = idx + len(MARKER_PREFIX)
    end = body.find("-->", start)
    if end < 0:
        return None
    return body[start:end].strip()


def find_existing(records: Sequence[dict], key: str) -> Optional[dict]:
    for record in records:
        if _marker_key(str(record.get("body", "") or "")) == key:
            return record
    return None


def create_issue(
    issue: RemediationIssue, repo: str, *, runner: Optional[Runner] = None
) -> int:
    """Create a new GitHub issue for a RemediationIssue. Returns the issue
    number. Raises RuntimeError on failure."""
    labels = list(issue.labels)
    cmd = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        issue.title,
        "--body",
        issue.body(),
    ]
    for label in labels:
        cmd.extend(["--label", label])
    result = _run(cmd, runner)
    if result.returncode != 0:
        raise RuntimeError(
            "gh issue create failed (%d): %s" % (result.returncode, result.stderr.strip())
        )
    return _number_from_create_output(result.stdout)


def _number_from_create_output(stdout: str) -> int:
    """``gh issue create`` prints the new issue's URL; the trailing path
    segment is its number."""
    text = (stdout or "").strip().splitlines()
    last = text[-1] if text else ""
    tail = last.rstrip("/").rsplit("/", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        raise RuntimeError("could not parse an issue number from: %r" % last) from None


def comment_issue(
    number: int, comment: str, repo: str, *, runner: Optional[Runner] = None
) -> None:
    cmd = [
        "gh",
        "issue",
        "comment",
        str(number),
        "--repo",
        repo,
        "--body",
        comment,
    ]
    result = _run(cmd, runner)
    if result.returncode != 0:
        raise RuntimeError(
            "gh issue comment failed (%d): %s" % (result.returncode, result.stderr.strip())
        )


def escalate_issue(number: int, repo: str, *, runner: Optional[Runner] = None) -> None:
    """Add the escalation + governance-board labels to a breaching issue."""
    cmd = [
        "gh",
        "issue",
        "edit",
        str(number),
        "--repo",
        repo,
        "--add-label",
        ESCALATION_LABEL,
        "--add-label",
        GOVERNANCE_BOARD_LABEL,
    ]
    result = _run(cmd, runner)
    if result.returncode != 0:
        raise RuntimeError(
            "gh issue edit failed (%d): %s" % (result.returncode, result.stderr.strip())
        )


def route(
    issues: Sequence[RemediationIssue],
    repo: str,
    *,
    runner: Optional[Runner] = None,
    dry_run: bool = True,
) -> List[RoutingResult]:
    """Route each RemediationIssue: dedup against GitHub, create or comment,
    escalate when it qualifies. ``dry_run=True`` (the default) performs no
    network calls and reports what *would* happen — the safe default for a
    scheduled audit hook that should not spam issues on every run without an
    explicit ``--apply``."""
    results: List[RoutingResult] = []
    if dry_run:
        for issue in issues:
            results.append(
                RoutingResult(key=issue.key, action="dry-run", escalated=issue.escalate)
            )
        return results

    existing = existing_remediation_issues(repo, runner=runner)
    for issue in issues:
        match = find_existing(existing, issue.key)
        if match is not None:
            number = int(match["number"])
            comment_issue(
                number,
                "New occurrence(s) detected (%d total). Evidence:\n%s"
                % (issue.occurrences, "\n".join("- %s" % e for e in issue.evidence)),
                repo,
                runner=runner,
            )
            action = "commented"
        else:
            number = create_issue(issue, repo, runner=runner)
            action = "created"

        if issue.escalate:
            escalate_issue(number, repo, runner=runner)

        results.append(
            RoutingResult(key=issue.key, action=action, number=number, escalated=issue.escalate)
        )
    return results
