"""Data model for claim-time issue-order enforcement (issue #157).

The chronological-dispatch rule (`AGENTS.md` golden rule 14,
`docs/GOVERNANCE.md` 8, `docs/EXECUTION-PLAN.md` 5) is declared in docs and
gated by `scripts/check-chronological-dispatch.sh`. That gate is a *declaration*
gate: it fails when the docs stop declaring the rule. This package is the
*behavioural* half — it decides, at claim time, whether an issue is the next
eligible step in the active dependency chain.

Everything here is stdlib-only and offline: the board state is a committed
snapshot (`.board/snapshot.json`) and claims are an append-only ledger
(`.board/claims.jsonl`), so the gate can audit real state without the network.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.policy import lease  # noqa: E402

# Reasons that justify a claim. Anything else is kanban scavenging.
REASON_CHILD_OF_CLAIM = "child-of-claim"
REASON_SUCCESSOR_OF_CLAIM = "successor-of-claim"
REASON_NEXT_IN_MILESTONE = "next-in-milestone"
REASON_BRAIN_DIRECTED = "brain-directed"
# A child of the ACTIVE epic (epic focus, issue #707): stricter than the
# milestone frontier, it is a chain edge to the epic the fleet is driving.
REASON_ACTIVE_EPIC_CHILD = "active-epic-child"
ALLOWED_CLAIM_REASONS = (
    REASON_CHILD_OF_CLAIM,
    REASON_SUCCESSOR_OF_CLAIM,
    REASON_NEXT_IN_MILESTONE,
    REASON_BRAIN_DIRECTED,
    REASON_ACTIVE_EPIC_CHILD,
)

# Reasons a claim is refused.
REASON_UNKNOWN_ISSUE = "unknown-issue"
REASON_ISSUE_CLOSED = "issue-closed"
REASON_BLOCKED = "blocked"
REASON_ALREADY_CLAIMED = "already-claimed"
REASON_NO_CHAIN_EDGE = "no-chain-edge"
REASON_EPIC_NOT_WORKABLE = "epic-not-workable"
# Epic focus (#707): the issue is outside the active epic, so while a focus is
# active the fleet does not dispatch it. Distinct from `no-chain-edge` because
# the issue is not being rejected as scavenging — it is being WAITING, and it is
# parked in `.board/pool.jsonl` so it is never silently dropped.
REASON_OUT_OF_EPIC_POOLED = "out-of-epic-pooled"

CLAIM_EVENTS = ("claim", "release", "take-over", "reap")

MISSING = object()


@dataclass(frozen=True)
class Issue:
    """One issue as recorded in the board snapshot."""

    number: int
    title: str = ""
    state: str = "open"
    milestone: str = ""
    labels: tuple[str, ...] = ()
    parent: int | None = None
    blocked_by: tuple[int, ...] = ()
    # Cross-repo chain edges, e.g. ``kushin77/code-indexing#128`` (issue #181).
    # A foreign board is not in this snapshot, so these are captured but never
    # gated by the same-repo eligibility rules — they are surfaced for the
    # wave-bootstrap report and the triage lane to act on.
    cross_refs: tuple[str, ...] = ()
    closed_at: str = ""

    @property
    def closed(self) -> bool:
        return self.state.strip().lower() == "closed"

    @property
    def is_epic(self) -> bool:
        """An epic is closed by its children; it is never a unit of work."""
        return "type:epic" in self.labels

    def to_json(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "milestone": self.milestone,
            "labels": list(self.labels),
            "parent": self.parent,
            "blocked_by": list(self.blocked_by),
            "cross_refs": list(self.cross_refs),
            "closed_at": self.closed_at,
        }


@dataclass(frozen=True)
class Snapshot:
    """The board state a claim is validated against."""

    generated_at: str
    source: str
    issues: dict[int, Issue] = field(default_factory=dict)

    def get(self, number: int) -> Issue | None:
        return self.issues.get(number)

    def open_issues(self) -> list[Issue]:
        return [i for i in self.issues.values() if not i.closed]

    def blockers_open(self, issue: Issue) -> list[int]:
        """Blocker numbers that are still open (unknown blockers count as open)."""
        open_blockers = []
        for number in issue.blocked_by:
            blocker = self.issues.get(number)
            if blocker is None or not blocker.closed:
                open_blockers.append(number)
        return sorted(open_blockers)

    def children_of(self, number: int) -> list[Issue]:
        return sorted(
            (i for i in self.issues.values() if i.parent == number),
            key=lambda i: i.number,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "source": self.source,
            "issues": [i.to_json() for i in sorted(self.issues.values(), key=lambda i: i.number)],
        }


@dataclass(frozen=True)
class Eligibility:
    """The verdict for one (issue, agent) pair."""

    issue: int
    eligible: bool
    reason: str
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "issue": self.issue,
            "eligible": self.eligible,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ClaimEvent:
    """One line of the append-only claim ledger."""

    event: str
    issue: int
    agent: str
    at: str
    lane: str = ""
    base_commit: str = ""
    snapshot_sha256: str = ""
    reason: str = ""
    # The claim lease is declared once in governance/policy/lease.py.
    ttl_hours: int = lease.CLAIM_TTL_HOURS
    directive_id: str = ""
    directive_from: str = ""
    reaped_agent: str = ""

    @property
    def is_claim(self) -> bool:
        return self.event in ("claim", "take-over")

    def to_json(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "issue": self.issue,
            "agent": self.agent,
            "at": self.at,
            "lane": self.lane,
            "base_commit": self.base_commit,
            "snapshot_sha256": self.snapshot_sha256,
            "reason": self.reason,
            "ttl_hours": self.ttl_hours,
            "directive_id": self.directive_id,
            "directive_from": self.directive_from,
            "reaped_agent": self.reaped_agent,
        }


def _require(obj: Any, key: str, kind: type, where: str) -> Any:
    value = obj.get(key, MISSING)
    if value is MISSING:
        raise ValueError(f"{where}: missing required field '{key}'")
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{where}: field '{key}' must be an integer, got {type(value).__name__}")
    elif not isinstance(value, kind):
        raise ValueError(f"{where}: field '{key}' must be {kind.__name__}, got {type(value).__name__}")
    return value


def parse_claim_event(obj: Any, where: str = "ledger") -> ClaimEvent:
    """Parse and validate one ledger record. Raises ValueError with the reason."""
    if not isinstance(obj, dict):
        raise ValueError(f"{where}: record must be a JSON object")
    event = _require(obj, "event", str, where)
    if event not in CLAIM_EVENTS:
        raise ValueError(f"{where}: unknown event '{event}' (expected one of {', '.join(CLAIM_EVENTS)})")
    issue = _require(obj, "issue", int, where)
    agent = _require(obj, "agent", str, where)
    at = _require(obj, "at", str, where)
    if not agent.strip():
        raise ValueError(f"{where}: field 'agent' must not be empty")
    ttl = obj.get("ttl_hours", lease.CLAIM_TTL_HOURS)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
        raise ValueError(f"{where}: field 'ttl_hours' must be a positive integer")
    return ClaimEvent(
        event=event,
        issue=issue,
        agent=agent.strip(),
        at=at.strip(),
        lane=str(obj.get("lane", "") or ""),
        base_commit=str(obj.get("base_commit", "") or ""),
        snapshot_sha256=str(obj.get("snapshot_sha256", "") or ""),
        reason=str(obj.get("reason", "") or ""),
        ttl_hours=ttl,
        directive_id=str(obj.get("directive_id", "") or ""),
        directive_from=str(obj.get("directive_from", "") or ""),
        reaped_agent=str(obj.get("reaped_agent", "") or ""),
    )
