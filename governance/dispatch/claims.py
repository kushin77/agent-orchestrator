"""The claim ledger: claim records, the single-claim lock, and the audit.

* **Claim record** — one JSON object per line in the append-only
  ``.board/claims.jsonl`` (agent, lane, base commit, reason, snapshot hash,
  timestamp, TTL). The record is the evidence that a claim was order-checked.
* **Single-claim lock** — ``.board/locks/<issue>.lock`` is created
  ``O_CREAT|O_EXCL``; a second claim on an in-flight issue fails loudly. A claim
  whose TTL has expired may be taken over (a dead agent cannot wedge the chain).
* **Audit** — replays the ledger against the committed snapshot and reports every
  structural violation: malformed record, duplicate active claim, release without
  claim, claim on a closed issue, and a recorded reason the snapshot does not
  justify. ``self_control`` proves the audit can fail (anti-formality).
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import order
from model import (
    ALLOWED_CLAIM_REASONS,
    REASON_CHILD_OF_CLAIM,
    REASON_NEXT_IN_MILESTONE,
    REASON_SUCCESSOR_OF_CLAIM,
    ClaimEvent,
    Issue,
    Snapshot,
    parse_claim_event,
)
from snapshot import now_iso, parse_iso

DEFAULT_LEDGER = Path(".board/claims.jsonl")
DEFAULT_LOCK_DIR = Path(".board/locks")
DEFAULT_TTL_HOURS = 24


class ClaimRefused(Exception):
    """A claim was refused. ``reason`` is the machine-readable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def read_ledger(path: Path | str = DEFAULT_LEDGER) -> list[ClaimEvent]:
    """Parse the ledger. Raises ValueError naming the offending line."""
    target = Path(path)
    if not target.exists():
        return []
    events: list[ClaimEvent] = []
    for lineno, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{target}:{lineno}: invalid JSON ({exc.msg})") from exc
        events.append(parse_claim_event(obj, where=f"{target}:{lineno}"))
    return events


def append_event(event: ClaimEvent, path: Path | str = DEFAULT_LEDGER) -> None:
    """Append one record under an exclusive lock (parallel agents share this file)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(event.to_json(), sort_keys=False) + "\n"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, payload.encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def active_claims(events: list[ClaimEvent], now: datetime | None = None) -> dict[int, ClaimEvent]:
    """Replay the ledger into ``issue -> live claim``.

    A ``release`` clears the issue. Expired claims are dropped: the TTL is what
    keeps a dead agent from wedging the chain forever.
    """
    moment = now or datetime.now(timezone.utc)
    return {issue: claim for issue, claim in replay(events).items() if not is_expired(claim, moment)}


def is_expired(claim: ClaimEvent, now: datetime | None = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    try:
        claimed_at = parse_iso(claim.at)
    except ValueError:
        return True
    return claimed_at + timedelta(hours=claim.ttl_hours) <= moment


def replay(events: list[ClaimEvent]) -> dict[int, ClaimEvent]:
    """Issue -> the claim record that currently holds it, ignoring TTL."""
    raw: dict[int, ClaimEvent] = {}
    for event in events:
        if event.is_claim:
            raw[event.issue] = event
        elif event.event == "release":
            raw.pop(event.issue, None)
    return raw


def expired_claims(events: list[ClaimEvent], now: datetime | None = None) -> dict[int, ClaimEvent]:
    """Claims whose TTL has elapsed and that were never released."""
    moment = now or datetime.now(timezone.utc)
    return {issue: claim for issue, claim in replay(events).items() if is_expired(claim, moment)}


def lock_path(issue: int, lock_dir: Path | str = DEFAULT_LOCK_DIR) -> Path:
    return Path(lock_dir) / f"{issue}.lock"


def _acquire_lock(issue: int, lock_dir: Path | str, takeover: bool) -> bool:
    """Create the exclusive lock file. Returns False when a live lock exists."""
    target = lock_path(issue, lock_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if not takeover:
            return False
        target.unlink(missing_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    os.close(fd)
    return True


def _release_lock(issue: int, lock_dir: Path | str) -> None:
    lock_path(issue, lock_dir).unlink(missing_ok=True)


def claim(
    issue_number: int,
    agent: str,
    lane: str,
    snapshot: Snapshot,
    ledger: Path | str = DEFAULT_LEDGER,
    lock_dir: Path | str = DEFAULT_LOCK_DIR,
    base_commit: str = "",
    snapshot_sha256: str = "",
    ttl_hours: int = DEFAULT_TTL_HOURS,
    now: datetime | None = None,
) -> ClaimEvent:
    """Claim an issue after checking order. Raises ClaimRefused when it is not the next step."""
    moment = now or datetime.now(timezone.utc)
    events = read_ledger(ledger)
    live = active_claims(events, moment)
    latest = replay(events).get(issue_number)
    history = {event.issue for event in events if event.is_claim and event.agent == agent}

    # A live claim blocks anyone else; an expired one may be taken over, so a
    # dead agent cannot wedge the chain forever.
    held = live.get(issue_number)
    takeover = False
    if held is not None:
        if held.agent == agent:
            raise ClaimRefused("already-claimed", f"{agent} already holds #{issue_number} (release it first)")
        raise ClaimRefused("already-claimed", f"#{issue_number} is held by {held.agent} until its TTL elapses")
    if latest is not None and is_expired(latest, moment) and latest.agent != agent:
        takeover = True

    others = frozenset(number for number, holder in live.items() if holder.agent != agent)
    verdict = order.eligible(
        snapshot,
        issue_number,
        active_claims=frozenset(number for number, holder in live.items() if holder.agent == agent),
        agent_history=frozenset(history),
        claimed_by_others=others,
    )
    if not verdict.eligible:
        raise ClaimRefused(verdict.reason, verdict.detail)

    if not _acquire_lock(issue_number, lock_dir, takeover=takeover):
        raise ClaimRefused("already-claimed", f"#{issue_number} lock is held by another agent")

    event_name = "take-over" if takeover else "claim"
    event = ClaimEvent(
        event=event_name,
        issue=issue_number,
        agent=agent,
        at=now_iso() if now is None else moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        lane=lane,
        base_commit=base_commit,
        snapshot_sha256=snapshot_sha256,
        reason=verdict.reason,
        ttl_hours=ttl_hours,
    )
    append_event(event, ledger)
    return event


def release(
    issue_number: int,
    agent: str,
    ledger: Path | str = DEFAULT_LEDGER,
    lock_dir: Path | str = DEFAULT_LOCK_DIR,
    now: datetime | None = None,
) -> ClaimEvent:
    """Release a claim. Raises ClaimRefused if this agent does not hold it."""
    moment = now or datetime.now(timezone.utc)
    events = read_ledger(ledger)
    held = active_claims(events, moment).get(issue_number)
    if held is None:
        raise ClaimRefused("not-claimed", f"#{issue_number} has no live claim")
    if held.agent != agent:
        raise ClaimRefused("not-owner", f"#{issue_number} is held by {held.agent}, not {agent}")
    event = ClaimEvent(
        event="release",
        issue=issue_number,
        agent=agent,
        at=now_iso() if now is None else moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    append_event(event, ledger)
    _release_lock(issue_number, lock_dir)
    return event


# --- audit ------------------------------------------------------------------


def audit(events: list[ClaimEvent], snapshot: Snapshot, now: datetime | None = None) -> list[str]:
    """Replay the ledger against the snapshot; return every structural problem.

    The audit re-derives the *time-stable* invariants: record schema, declared
    chain edges, milestone membership, blockers, the single-claim lock and
    release pairing. Strict frontier ordering is enforced live at claim time by
    ``order.eligible`` because a historical frontier cannot be recomputed from
    today's snapshot — see ``README.md``.
    """
    problems: list[str] = []
    moment = now or datetime.now(timezone.utc)
    holder: dict[int, ClaimEvent] = {}

    for index, event in enumerate(events):
        where = f"ledger[{index}]"
        if event.is_claim:
            current = holder.get(event.issue)
            if current is not None and current.agent != event.agent and not is_expired(current, _claimed_at(event, moment)):
                problems.append(
                    f"{where}: claim on #{event.issue} while it is held by {current.agent} "
                    "(single-claim lock violated)"
                )
            problems.extend(
                f"{where}: {problem}" for problem in _claim_problems(event, events, snapshot)
            )
            holder[event.issue] = event
        elif event.event == "release":
            current = holder.get(event.issue)
            if current is None:
                problems.append(f"{where}: release of #{event.issue} with no prior claim")
            elif current.agent != event.agent:
                problems.append(
                    f"{where}: release of #{event.issue} by {event.agent} but it is held by {current.agent}"
                )
            else:
                holder.pop(event.issue, None)

    for issue, stale in sorted(expired_claims(events, moment).items()):
        issue_obj = snapshot.get(issue)
        if issue_obj is not None and not issue_obj.closed:
            problems.append(
                f"#{issue}: claim by {stale.agent} expired at TTL with the issue still open "
                "(chain stalled — release it or take it over)"
            )

    return problems


def _claimed_at(event: ClaimEvent, fallback: datetime) -> datetime:
    try:
        return parse_iso(event.at)
    except ValueError:
        return fallback


def _claim_problems(event: ClaimEvent, events: list[ClaimEvent], snapshot: Snapshot) -> list[str]:
    """Structural problems with one claim record (schema-valid but unjustified).

    Time-aware by necessity: this replays *history* against *present* truth, so a
    claim that predates the issue's closure is legitimate record, not a violation.
    Only a claim still live at audit time is judged against the current snapshot —
    otherwise an agent that completes an issue is retroactively blamed for having
    claimed it.
    """
    problems: list[str] = []
    issue = snapshot.get(event.issue)
    if issue is None:
        return [f"#{event.issue}: claimed but absent from the snapshot"]

    if issue.closed:
        # Settled unless the claim is still open (never released) — a live claim on
        # a closed issue is a real problem; a released one is finished history.
        still_live = not any(
            later.event == "release" and later.issue == event.issue
            for later in events
        )
        if still_live:
            problems.append(f"#{event.issue}: claimed after it was closed")
        return problems

    if issue.is_epic:
        problems.append(f"#{event.issue}: claimed an epic (an epic closes with its children, it is not work)")
    open_blockers = snapshot.blockers_open(issue)
    if open_blockers:
        listed = ", ".join(f"#{number}" for number in open_blockers)
        problems.append(f"#{event.issue}: claimed while blocked by {listed}")
    if event.reason not in ALLOWED_CLAIM_REASONS:
        problems.append(f"#{event.issue}: reason '{event.reason or '<empty>'}' is not a chain reason")
        return problems

    earlier = {
        prior.issue
        for prior in events
        if prior.is_claim and prior.agent == event.agent
    }
    if event.reason == REASON_CHILD_OF_CLAIM:
        if issue.parent is None:
            problems.append(f"#{event.issue}: reason 'child-of-claim' but the issue declares no Parent: edge")
        elif issue.parent not in earlier:
            problems.append(
                f"#{event.issue}: reason 'child-of-claim' but parent #{issue.parent} was never claimed by {event.agent}"
            )
    elif event.reason == REASON_SUCCESSOR_OF_CLAIM:
        if not (set(issue.blocked_by) & earlier):
            problems.append(
                f"#{event.issue}: reason 'successor-of-claim' but none of its blockers were claimed by {event.agent}"
            )
    elif event.reason == REASON_NEXT_IN_MILESTONE:
        if not issue.milestone:
            problems.append(f"#{event.issue}: reason 'next-in-milestone' but the issue has no milestone")
        elif _released(events, event):
            # The frontier is a point-in-time property: once this agent released the
            # issue, later work legitimately advances the frontier past it, so
            # comparing against today's frontier would blame completed work.
            pass
        else:
            candidate = order.frontier(snapshot, issue.milestone)
            if candidate is None or candidate.number != event.issue:
                frontier_text = f"#{candidate.number}" if candidate is not None else "none"
                problems.append(
                    f"#{event.issue}: reason 'next-in-milestone' but the frontier of {issue.milestone!r} "
                    f"is {frontier_text}"
                )
    return problems


def _released(events: list[ClaimEvent], event: ClaimEvent) -> bool:
    """Whether this claim was later released (finished, not abandoned)."""
    return any(
        later.event == "release"
        and later.issue == event.issue
        and later.agent == event.agent
        for later in events
    )


def audit_text(text: str, snapshot: Snapshot, now: datetime | None = None) -> list[str]:
    """Audit raw ledger text (the gate path): malformed lines become problems."""
    problems: list[str] = []
    events: list[ClaimEvent] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            events.append(parse_claim_event(json.loads(line), where=f"claims.jsonl:{lineno}"))
        except (json.JSONDecodeError, ValueError) as exc:
            problems.append(f"claims.jsonl:{lineno}: malformed record ({exc})")
    problems.extend(audit(events, snapshot, now))
    return problems


def control_snapshot(now: datetime | None = None) -> Snapshot:
    """Deterministic fixture the self-control mutants are evaluated against.

    Shape: milestone ``CONTROL`` with #601 the frontier, #602 blocked by #603,
    #603 open and out of order, #604 closed, #605 a child of #601.
    """
    moment = now or datetime.now(timezone.utc)
    issues = {
        601: Issue(601, "frontier", milestone="CONTROL", labels=("type:task",)),
        602: Issue(602, "blocked", milestone="CONTROL", blocked_by=(603,)),
        603: Issue(603, "out of order", milestone="CONTROL"),
        604: Issue(604, "closed", state="closed", milestone="CONTROL"),
        605: Issue(605, "child", milestone="CONTROL", parent=601),
        607: Issue(607, "epic", milestone="CONTROL", labels=("type:epic",)),
    }
    return Snapshot(
        generated_at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        source="self-control",
        issues=issues,
    )


def self_control(now: datetime | None = None) -> list[str]:
    """Prove the audit can fail (anti-formality, GR-12 / AO-GR-19).

    Each mutant must be rejected and each positive control must be clean. A
    mismatch is reported as a problem, which makes the gate fail: an audit that
    only ever passes is a formality.
    """
    moment = now or datetime.now(timezone.utc)
    snapshot = control_snapshot(moment)
    problems: list[str] = []

    def record(issue: int, reason: str, agent: str = "control-agent", event: str = "claim") -> ClaimEvent:
        return ClaimEvent(
            event=event,
            issue=issue,
            agent=agent,
            at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
            lane="control",
            reason=reason,
            ttl_hours=DEFAULT_TTL_HOURS,
        )

    def expect_clean(name: str, events: list[ClaimEvent]) -> None:
        found = audit(events, snapshot, moment)
        if found:
            problems.append(f"self-control['{name}']: valid ledger rejected ({found[0]})")

    def expect_rejected(name: str, events: list[ClaimEvent]) -> None:
        if not audit(events, snapshot, moment):
            problems.append(f"self-control['{name}']: mutant passed the audit (the audit cannot fail)")

    expect_clean("frontier", [record(601, REASON_NEXT_IN_MILESTONE)])
    expect_clean(
        "child-of-claim",
        [record(601, REASON_NEXT_IN_MILESTONE), record(605, REASON_CHILD_OF_CLAIM)],
    )
    expect_rejected("scavenging", [record(603, REASON_NEXT_IN_MILESTONE)])
    expect_rejected("epic", [record(607, REASON_NEXT_IN_MILESTONE)])
    expect_rejected("blocked", [record(602, REASON_NEXT_IN_MILESTONE)])
    expect_rejected("closed", [record(604, REASON_NEXT_IN_MILESTONE)])
    expect_rejected("unsupported-reason", [record(601, "because-i-felt-like-it")])
    expect_rejected(
        "duplicate-claim",
        [record(601, REASON_NEXT_IN_MILESTONE, agent="agent-a"), record(601, REASON_NEXT_IN_MILESTONE, agent="agent-b")],
    )
    expect_rejected(
        "double-release",
        [
            record(601, REASON_NEXT_IN_MILESTONE),
            record(601, "", event="release"),
            record(601, "", event="release"),
        ],
    )
    if not audit_text('{"event": "claim", "issue": 601}\n', snapshot, moment):
        problems.append("self-control['malformed']: a malformed record passed the audit")
    return problems
