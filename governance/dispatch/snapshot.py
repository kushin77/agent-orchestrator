"""Board snapshot: the committed board state a claim is validated against.

The gate of record runs offline, so it cannot call GitHub. Instead the board
state is a tracked artifact, `.board/snapshot.json`, refreshed explicitly with
``python3 governance/dispatch/cli.py snapshot --from-github`` (the only
network-touching path in this package).

Dependency edges are read from an explicit, documented convention in the issue
body, so a chain is declared rather than guessed:

    Parent: #152          -> this issue is a child of #152
    Part-of: #152         -> same edge, alternate spelling
    Blocked-by: #9, #10   -> this issue cannot start until those are closed

An issue with no declared edges is still eligible as the milestone frontier
(rule "next-in-milestone"); it is never eligible merely because it is visible.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from model import Issue, Snapshot

DEFAULT_PATH = Path(".board/snapshot.json")

_PARENT_RE = re.compile(r"^\s*(?:parent|part[-_ ]of)\s*:\s*#?([0-9]+(?:\s*,\s*#?[0-9]+)*)", re.I | re.M)
_BLOCKED_RE = re.compile(r"^\s*blocked[-_ ]by\s*:\s*#?([0-9]+(?:\s*,\s*#?[0-9]+)*)", re.I | re.M)


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


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp (``2026-09-13T17:36:28Z``); naive input is UTC."""
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_snapshot(records: Iterable[dict[str, Any]], source: str, generated_at: str | None = None) -> Snapshot:
    """Build a Snapshot from GitHub-shaped issue records (``gh issue list --json``)."""
    issues: dict[int, Issue] = {}
    for record in records:
        number = int(record["number"])
        parent, blocked = parse_edges(str(record.get("body", "") or ""))
        milestone = record.get("milestone") or {}
        milestone_title = milestone.get("title", "") if isinstance(milestone, dict) else str(milestone or "")
        labels = record.get("labels") or []
        label_names = tuple(
            sorted(str(label.get("name", "")) if isinstance(label, dict) else str(label) for label in labels)
        )
        issues[number] = Issue(
            number=number,
            title=str(record.get("title", "") or ""),
            state=str(record.get("state", "open") or "open"),
            milestone=milestone_title,
            labels=label_names,
            parent=parent,
            blocked_by=blocked,
        )
    return Snapshot(generated_at=generated_at or now_iso(), source=source, issues=issues)


def github_records(repo: str, runner: Callable[..., subprocess.CompletedProcess] | None = None) -> list[dict[str, Any]]:
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
        raise RuntimeError(f"gh issue list failed ({result.returncode}): {result.stderr.strip()}")
    try:
        records = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"gh issue list returned invalid JSON: {exc}") from exc
    if not isinstance(records, list):  # pragma: no cover - defensive
        raise RuntimeError("gh issue list returned a non-list payload")
    return records


def save(snapshot: Snapshot, path: Path | str = DEFAULT_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot.to_json(), indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return target


def load(path: Path | str = DEFAULT_PATH) -> Snapshot:
    """Load the committed snapshot. Raises FileNotFoundError/ValueError if unusable."""
    target = Path(path)
    raw = target.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        raise ValueError(f"{target}: snapshot must be an object with an 'issues' list")
    issues: dict[int, Issue] = {}
    for entry in data["issues"]:
        if not isinstance(entry, dict) or "number" not in entry:
            raise ValueError(f"{target}: every snapshot issue needs a 'number'")
        number = int(entry["number"])
        blocked = entry.get("blocked_by") or []
        issues[number] = Issue(
            number=number,
            title=str(entry.get("title", "") or ""),
            state=str(entry.get("state", "open") or "open"),
            milestone=str(entry.get("milestone", "") or ""),
            labels=tuple(str(label) for label in (entry.get("labels") or [])),
            parent=int(entry["parent"]) if entry.get("parent") is not None else None,
            blocked_by=tuple(sorted(int(number) for number in blocked)),
        )
    return Snapshot(
        generated_at=str(data.get("generated_at", "") or ""),
        source=str(data.get("source", "") or ""),
        issues=issues,
    )


def content_sha256(path: Path | str = DEFAULT_PATH) -> str:
    """Hash of the snapshot file, recorded on every claim as provenance."""
    digest = hashlib.sha256()
    digest.update(Path(path).read_bytes())
    return digest.hexdigest()
