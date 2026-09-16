"""The owner's committed dispatch queue (issue #928).

`order.py`/`claims.py` already refuse an out-of-order `claim` when
`Issue.blocked_by` is populated (`order.py:120` `REASON_BLOCKED`,
`claims.py:487` `raise ClaimRefused(REASON_BLOCKED, ...)`). But nothing
populated that edge from the owner's actual intended order (#878 comment
"Owner queue 2026-09-16") — this module is the missing edge source.

`governance/dispatch/queue.yaml` is the single committed source of truth: an
ordered list of waves, each an ordered list of issue numbers. `overlay()`
unions the queue's IMPLIED order into `Issue.blocked_by` before the existing
refusal path runs:

* every issue is blocked_by every EARLIER issue in the same wave's list that
  is still open;
* every issue is blocked_by every issue of an EARLIER wave that is still
  open;
* explicit `blocked_by` overrides in the file are unioned in on top.

Only OPEN blockers are added (a closed blocker cannot re-open by force of the
queue file), so the chain shortens automatically as the owner's real queue is
worked. Nothing here invents a new refusal reason: this module only grows the
edge set `order.eligible`/`claims.arbitrate` already read.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

PKG_DIR = Path(__file__).resolve().parent
if str(PKG_DIR) not in sys.path:
    sys.path.insert(0, str(PKG_DIR))

from model import Issue, Snapshot  # noqa: E402

DEFAULT_PATH = PKG_DIR / "queue.yaml"


class QueueError(ValueError):
    """The queue file itself is malformed: duplicates, unknown numbers, a cycle."""


def load(path: Path | str = DEFAULT_PATH) -> dict[str, Any] | None:
    """Parse the queue file. Returns ``None`` when it is absent (queue is optional)."""
    path = Path(path)
    if not path.exists():
        return None
    import yaml  # local import: only the queue path needs it (#928)

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict) or "waves" not in data:
        raise QueueError(f"{path}: expected a mapping with a 'waves' key")
    return data


def _wave_lists(data: dict[str, Any]) -> list[list[int]]:
    waves = data.get("waves") or []
    if not isinstance(waves, list):
        raise QueueError("'waves' must be a list")
    out: list[list[int]] = []
    for wave in waves:
        if not isinstance(wave, dict) or "issues" not in wave:
            raise QueueError(f"each wave must be a mapping with an 'issues' key, got {wave!r}")
        issues = wave["issues"]
        if not isinstance(issues, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in issues):
            raise QueueError(f"wave {wave.get('name', '?')!r}: 'issues' must be a list of integers")
        out.append(list(issues))
    return out


def _overrides(data: dict[str, Any]) -> dict[int, list[int]]:
    raw = data.get("blocked_by") or {}
    if not isinstance(raw, dict):
        raise QueueError("'blocked_by' must be a mapping of issue -> [issue, ...]")
    out: dict[int, list[int]] = {}
    for key, value in raw.items():
        number = int(key)
        if not isinstance(value, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in value):
            raise QueueError(f"blocked_by[{key}] must be a list of integers")
        out[number] = list(value)
    return out


def validate(data: dict[str, Any], snapshot: Snapshot | None = None) -> list[str]:
    """Structural problems in the queue file: duplicates, unknown/closed numbers, cycles.

    ``snapshot`` is the cached board snapshot (``.board/snapshot.json``); when
    given, every queued number must both exist in it and be OPEN there — a
    number the owner queued that is already closed, or that never existed, is
    a stale entry the file should have dropped.
    """
    problems: list[str] = []
    try:
        wave_lists = _wave_lists(data)
        overrides = _overrides(data)
    except QueueError as exc:
        return [str(exc)]

    seen: dict[int, str] = {}
    for index, issues in enumerate(wave_lists):
        for number in issues:
            if number in seen:
                problems.append(f"#{number} is listed twice in the queue (wave {index} and {seen[number]})")
            else:
                seen[number] = f"wave {index}"

    if snapshot is not None:
        for number in sorted(seen):
            issue = snapshot.get(number)
            if issue is None:
                problems.append(f"#{number} is queued but not present in the board snapshot")
            elif issue.closed:
                problems.append(f"#{number} is queued but already closed — drop it from the queue")
        for number in overrides:
            if snapshot.get(number) is None:
                problems.append(f"#{number} has a blocked_by override but is not present in the board snapshot")

    # Cycle check over the FULL implied graph (wave edges + overrides), ignoring
    # openness — a cycle is a structural defect regardless of live issue state.
    edges = _edges(data, snapshot=None)
    for override_issue, blockers in overrides.items():
        edges.setdefault(override_issue, set()).update(blockers)
    visiting: set[int] = set()
    visited: set[int] = set()

    def _walk(node: int, path: list[int]) -> None:
        if node in visited:
            return
        if node in visiting:
            cycle = " -> ".join(f"#{n}" for n in path[path.index(node):] + [node])
            problems.append(f"cycle in the queue's blocked_by graph: {cycle}")
            return
        visiting.add(node)
        for blocker in sorted(edges.get(node, ())):
            _walk(blocker, path + [node])
        visiting.discard(node)
        visited.add(node)

    for node in sorted(edges):
        if node not in visited:
            _walk(node, [])

    return problems


def _edges(data: dict[str, Any], snapshot: Snapshot | None) -> dict[int, set[int]]:
    """The full implied blocked_by graph: {issue: {blocking issue, ...}}.

    When ``snapshot`` is given, only blockers that are OPEN there are kept
    (closed blockers drop out of the chain). When ``snapshot`` is ``None`` the
    unfiltered graph is returned (used by the cycle check, which must see the
    whole structure regardless of live state).
    """
    wave_lists = _wave_lists(data)
    overrides = _overrides(data)
    edges: dict[int, set[int]] = {}

    def _open(number: int) -> bool:
        if snapshot is None:
            return True
        issue = snapshot.get(number)
        return issue is not None and not issue.closed

    earlier_waves: list[int] = []
    for issues in wave_lists:
        for position, number in enumerate(issues):
            blockers = set(n for n in issues[:position] if _open(n))
            blockers.update(n for n in earlier_waves if _open(n))
            edges.setdefault(number, set()).update(blockers)
        earlier_waves.extend(issues)

    for number, blockers in overrides.items():
        edges.setdefault(number, set()).update(n for n in blockers if _open(n))

    return edges


def overlay(snapshot: Snapshot, data: dict[str, Any] | None) -> Snapshot:
    """Return a copy of ``snapshot`` with the queue's implied edges unioned into
    each queued issue's ``blocked_by``. ``data`` of ``None`` is a no-op (no
    queue file committed).
    """
    if not data:
        return snapshot
    edges = _edges(data, snapshot)
    if not edges:
        return snapshot
    issues = dict(snapshot.issues)
    for number, blockers in edges.items():
        issue = issues.get(number)
        if issue is None or not blockers:
            continue
        merged = tuple(sorted(set(issue.blocked_by) | blockers))
        if merged != issue.blocked_by:
            issues[number] = replace(issue, blocked_by=merged)
    return Snapshot(generated_at=snapshot.generated_at, source=snapshot.source, issues=issues)


def queue_blockers_open(issue_number: int, data: dict[str, Any] | None, snapshot: Snapshot) -> list[int]:
    """Open blockers of ``issue_number`` that come from the QUEUE (not GitHub edges).

    Used to build the ``queue:`` refusal detail: the queue-sourced subset of
    whatever ``snapshot.blockers_open()`` already reports as open.
    """
    if not data:
        return []
    edges = _edges(data, snapshot)
    return sorted(edges.get(issue_number, ()))


def queue_detail(issue_number: int, data: dict[str, Any] | None, snapshot: Snapshot) -> str | None:
    """A ``queue: ...`` detail suffix naming the blocking issue(s), or ``None``."""
    blockers = queue_blockers_open(issue_number, data, snapshot)
    if not blockers:
        return None
    listed = ", ".join(f"#{n}" for n in blockers)
    return f"queue: {listed} precede(s) #{issue_number} in governance/dispatch/queue.yaml"


def next_claimable(snapshot: Snapshot, data: dict[str, Any] | None) -> list[int]:
    """Every queued, open issue with no open queue-blocker left — the next claimable set."""
    if not data:
        return []
    wave_lists = _wave_lists(data)
    queued_order = [n for wave in wave_lists for n in wave]
    edges = _edges(data, snapshot)
    ready: list[int] = []
    for number in queued_order:
        issue = snapshot.get(number)
        if issue is None or issue.closed:
            continue
        if not edges.get(number):
            ready.append(number)
    return ready
