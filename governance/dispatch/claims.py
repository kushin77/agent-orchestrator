"""The claim ledger: claim records, the single-claim lock, and the audit.

* **Claim record** — one JSON object per event. New events are written one file
  per event into ``.board/claims/`` (atomic, collision-proof, one path per
  event), so two concurrent lanes never share a file and a git conflict on the
  ledger is impossible by construction. The pre-#170 single-file ledger
  ``.board/claims.jsonl`` is frozen history, read first so replay stays
  time-ordered.
* **Single-claim lock** — ``.board/locks/<issue>.lock`` is created
  ``O_CREAT|O_EXCL``; a second claim on an in-flight issue fails loudly. A claim
  whose TTL has expired may be taken over (a dead agent cannot wedge the chain).
* **Snapshot staleness** — a claim validated against a snapshot older than
  ``DEFAULT_STALENESS_MINUTES`` is refused with ``snapshot-stale`` (fail closed),
  never judged against stale board state.
* **Audit** — replays the ledger against the committed snapshot and reports every
  structural violation: malformed record, duplicate active claim, release without
  claim, claim on a closed issue, and a recorded reason the snapshot does not
  justify. ``self_control`` proves the audit can fail (anti-formality).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The claim gate validates a directive against the claiming fleet's OWN sent
# mailbox, which `fleet/channel.py` writes under the namespaced runtime dir
# (`AO_FLEET_DIR`). Reading the one source keeps a second fleet's claims from
# being refused as "invalid-directive" (#363). The ledger/lock/snapshot stay at
# `.board/` and shared across fleets.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "fleet"))

from governance.policy import lease  # noqa: E402
import runtime  # noqa: E402

import order
import focus
import pool
from model import (
    ALLOWED_CLAIM_REASONS,
    REASON_ACTIVE_EPIC_CHILD,
    REASON_BLOCKED,
    REASON_BRAIN_DIRECTED,
    REASON_CHILD_OF_CLAIM,
    REASON_ISSUE_CLOSED,
    REASON_NEXT_IN_MILESTONE,
    REASON_OUT_OF_EPIC_POOLED,
    REASON_SUCCESSOR_OF_CLAIM,
    REASON_UNKNOWN_ISSUE,
    ClaimEvent,
    Eligibility,
    Issue,
    Snapshot,
    parse_claim_event,
)
from snapshot import DEFAULT_STALENESS_MINUTES, age_minutes, is_stale, now_iso, parse_iso

DEFAULT_LEDGER = Path(".board/claims.jsonl")
DEFAULT_CLAIMS_DIR = Path(".board/claims")
DEFAULT_LOCK_DIR = Path(".board/locks")
#: The claim lease and the reap threshold are declared once in
#: governance/policy/lease.py: the claim TTL must exceed the session TTL, and the
#: reap threshold must not release a claim whose lane is still beating.
DEFAULT_TTL_HOURS = lease.CLAIM_TTL_HOURS
DEFAULT_REAP_MINUTES = lease.CLAIM_REAP_MINUTES
SENT_DIR = runtime.FLEET_DIR / "sent"


class ClaimRefused(Exception):
    """A claim was refused. ``reason`` is the machine-readable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _slug(text: str) -> str:
    """A filename-safe form of an agent id (path separators are the hazard)."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", text)
    return sanitized or "agent"


def _read_file_ledger(target: Path) -> list[ClaimEvent]:
    """Parse the legacy single-file ledger. Raises ValueError naming the line."""
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


def _read_dir_ledger(target: Path) -> list[ClaimEvent]:
    """Parse the one-file-per-event ledger. Sorted by name == write order."""
    if not target.is_dir():
        return []
    events: list[ClaimEvent] = []
    for path in sorted(target.glob("*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path}: unreadable claim record ({exc})") from exc
        events.append(parse_claim_event(obj, where=str(path)))
    return events


def read_ledger(path: Path | str = DEFAULT_CLAIMS_DIR) -> list[ClaimEvent]:
    """Replay the full claim history in temporal order.

    Two sources are concatenated: the legacy single-file ledger
    (``.board/claims.jsonl``, frozen before #170) and the one-file-per-event
    directory (``.board/claims/``, the only place new events are written). Every
    legacy event predates every directory event, so (legacy, then directory) is
    time-ordered; within the directory, filenames embed the nanosecond write
    time, so a lexical sort reproduces write order.

    ``path`` may name either source — the sibling source is merged in — so a
    reader that still points at the legacy file (fleet health/report) keeps
    seeing new claims, and the claim directory gets the frozen history too.
    """
    target = Path(path)
    if target.is_file():
        events = _read_file_ledger(target)
        sibling_dir = target.parent / DEFAULT_CLAIMS_DIR.name
        if sibling_dir.is_dir():
            events.extend(_read_dir_ledger(sibling_dir))
        return events
    # Directory (existing or not yet created): the frozen legacy file comes
    # first, then the one-file-per-event directory. This also covers a fresh
    # checkout where the claims directory has not been created yet.
    events: list[ClaimEvent] = []
    sibling_file = target.parent / DEFAULT_LEDGER.name
    if sibling_file.is_file():
        events.extend(_read_file_ledger(sibling_file))
    if target.is_dir():
        events.extend(_read_dir_ledger(target))
    return events


def _write_event_file(event: ClaimEvent, claims_dir: Path) -> Path:
    """Write one event as its own file, atomically and collision-proof.

    The file is created ``O_CREAT|O_EXCL`` under a name that embeds the
    nanosecond write time, so two lanes never share a path and a same-nanosecond
    collision is retried with a suffix rather than overwriting. The name's
    nanosecond prefix makes a lexical sort reproduce write order, which is what
    keeps replay deterministic.
    """
    target = Path(claims_dir)
    target.mkdir(parents=True, exist_ok=True)
    stem = f"{time.time_ns():020d}-{event.issue:05d}-{_slug(event.agent)}-{event.event}"
    for attempt in range(1_000_000):
        name = f"{stem}.json" if attempt == 0 else f"{stem}-{attempt}.json"
        path = target / name
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(event.to_json(), handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return path
    raise OSError("could not allocate a unique claim record path")


def append_event(event: ClaimEvent, path: Path | str = DEFAULT_CLAIMS_DIR) -> None:
    """Record one event.

    A ``*.jsonl`` path appends to the legacy single-file ledger (one line under
    an exclusive lock); any other path is a claims directory and gets one
    collision-proof file per event. Production writes go to the directory.
    """
    target = Path(path)
    if target.suffix == ".jsonl":
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(event.to_json(), sort_keys=False) + "\n"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, payload.encode("utf-8"))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    else:
        _write_event_file(event, target)


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
        elif event.event in ("release", "reap"):
            raw.pop(event.issue, None)
    return raw


def reap(
    older_than_minutes: int,
    ledger: Path | str = DEFAULT_CLAIMS_DIR,
    lock_dir: Path | str = DEFAULT_LOCK_DIR,
    now: datetime | None = None,
    issue: int | None = None,
    reaper: str = "brain",
) -> list[ClaimEvent]:
    """Release claims older than the threshold whose holder is gone.

    A subagent that dies holding a claim wedges its issue until the 24h TTL.
    The brain reaps it: a `reap` event names the reaped agent, clears the claim
    and releases the lock, so the issue can be dispatched again.
    """
    moment = now or datetime.now(timezone.utc)
    events = read_ledger(ledger)
    live = active_claims(events, moment)
    reaped: list[ClaimEvent] = []
    for number, holder in sorted(live.items()):
        if issue is not None and number != issue:
            continue
        if moment - parse_iso(holder.at) < timedelta(minutes=older_than_minutes):
            continue
        event = ClaimEvent(
            event="reap",
            issue=number,
            agent=reaper,
            at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
            lane=holder.lane,
            reason="reaped: holder gone past threshold",
            reaped_agent=holder.agent,
        )
        append_event(event, ledger)
        _release_lock(number, lock_dir)
        reaped.append(event)
    return reaped


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


def _load_brain_directive(directive_id: str, issue_number: int) -> dict:
    """Verify the brain authorized this issue through a sent directive.

    The brain is the chain: a directive recorded in .fleet/sent is a chain edge
    that authorizes off-frontier work. It must be a real, brain-issued directive
    naming exactly this issue — anything else raises ClaimRefused.
    """
    target = SENT_DIR / f"{directive_id}.json"
    if not target.exists():
        raise ClaimRefused("invalid-directive", f"no sent directive {directive_id} in .fleet/sent")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ClaimRefused("invalid-directive", f"directive {directive_id} is unreadable ({exc})")
    if data.get("from") != "brain" or data.get("type") != "directive":
        raise ClaimRefused("invalid-directive", f"{directive_id} is not a brain directive")
    task = data.get("task") or {}
    if task.get("issue") != issue_number:
        raise ClaimRefused(
            "invalid-directive", f"directive {directive_id} authorizes #{task.get('issue')}, not #{issue_number}"
        )
    return data


def _resolve_focus(focus_path: Path | str | None) -> Path | str:
    """Resolve the focus path at CALL time, never as a frozen default argument.

    ``focus.DEFAULT_PATH`` is a module constant that a caller (or a test) may
    repoint; a default argument would have captured its value at import and kept
    judging against the stale file while appearing to honour the override.
    """
    return focus.DEFAULT_PATH if focus_path is None else focus_path


def drain_pool_when_no_focus(
    snapshot: Snapshot,
    focus_path: Path | str | None = None,
    pool_path: Path | str = pool.POOL_PATH,
) -> list[int]:
    """Drain the out-of-epic pool when the resolver returns ``None`` (#707, F6).

    "The pool empties when the focus does" is only true if something empties it.
    The resolver returning ``None`` means no epic is driving, so nothing can be
    out-of-epic: every parked issue is un-parked, and the returned numbers are the
    evidence of what left. A caller that wants a human-readable line uses
    ``pool.drain_and_report``.

    Called on the claim path (every claim re-checks the focus) and exposed on the
    focus CLI, so the board-drain is driven by the same resolver that pools it.
    """
    if focus.active(snapshot, _resolve_focus(focus_path)) is not None:
        return []
    return pool.drain(pool_path)


def claim(
    issue_number: int,
    agent: str,
    lane: str,
    snapshot: Snapshot,
    ledger: Path | str = DEFAULT_CLAIMS_DIR,
    lock_dir: Path | str = DEFAULT_LOCK_DIR,
    base_commit: str = "",
    snapshot_sha256: str = "",
    ttl_hours: int = DEFAULT_TTL_HOURS,
    now: datetime | None = None,
    directive_id: str = "",
    stale_minutes: int = DEFAULT_STALENESS_MINUTES,
    focus_path: Path | str | None = None,
    pool_path: Path | str = pool.POOL_PATH,
) -> ClaimEvent:
    """Claim an issue after checking order. Raises ClaimRefused when it is not the next step.

    ``focus_path``/``pool_path`` keep the epic-focus edges resolvable offline
    against fixtures: the focus decides eligibility, the pool records the deferral
    (#707 lane F6). When the resolver returns ``None`` — no active epic — an
    out-of-epic refusal cannot happen, so any pool left over from a previous focus
    is DRAINED here and reported, never silently carried forward.
    """
    focus_path = _resolve_focus(focus_path)
    moment = now or datetime.now(timezone.utc)
    drain_pool_when_no_focus(snapshot, focus_path, pool_path)
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

    # A stale snapshot cannot be trusted to judge order: refuse before using it.
    if is_stale(snapshot, stale_minutes, moment):
        age = age_minutes(snapshot, moment)
        raise ClaimRefused(
            "snapshot-stale",
            f"snapshot is {age:.1f}m old (threshold {stale_minutes}m) — "
            "refresh first: python3 governance/dispatch/cli.py snapshot --from-github",
        )

    # Structural checks hold for every path, directive or not.
    issue = snapshot.get(issue_number)
    if issue is None:
        raise ClaimRefused(REASON_UNKNOWN_ISSUE, f"#{issue_number} is absent from the snapshot")
    if issue.closed:
        raise ClaimRefused(REASON_ISSUE_CLOSED, f"#{issue_number} is closed")
    open_blockers = snapshot.blockers_open(issue)
    if open_blockers:
        listed = ", ".join(f"#{number}" for number in open_blockers)
        raise ClaimRefused(REASON_BLOCKED, f"#{issue_number} is blocked by {listed}")

    directive: dict | None = None
    if directive_id:
        directive = _load_brain_directive(directive_id, issue_number)

    if directive is not None:
        verdict = Eligibility(issue_number, True, REASON_BRAIN_DIRECTED, f"brain-directed via {directive_id}")
    else:
        others = frozenset(number for number, holder in live.items() if holder.agent != agent)
        verdict = order.eligible(
            snapshot,
            issue_number,
            active_claims=frozenset(number for number, holder in live.items() if holder.agent == agent),
            agent_history=frozenset(history),
            claimed_by_others=others,
            focus_path=focus_path,
        )
        if not verdict.eligible:
            # Epic focus (#707, lane F6): out-of-epic work is parked, not dropped.
            # The refusal still stands — this only records WHY the issue is
            # waiting, so the pool is a decision log and the issue can be found
            # again. A drain (focus == None) is what takes it back out.
            if verdict.reason == REASON_OUT_OF_EPIC_POOLED:
                pool.note(issue_number, pool.REASON_OUT_OF_EPIC, path=pool_path)
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
        directive_id=directive_id,
        directive_from="brain" if directive is not None else "",
    )
    append_event(event, ledger)
    return event


def release(
    issue_number: int,
    agent: str,
    ledger: Path | str = DEFAULT_CLAIMS_DIR,
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


def audit(
    events: list[ClaimEvent],
    snapshot: Snapshot,
    now: datetime | None = None,
    focus_path: Path | str | None = None,
) -> list[str]:
    """Replay the ledger against the snapshot; return every structural problem.

    The audit re-derives the *time-stable* invariants: record schema, declared
    chain edges, milestone membership, blockers, the single-claim lock and
    release pairing. Strict frontier ordering is enforced live at claim time by
    ``order.eligible`` because a historical frontier cannot be recomputed from
    today's snapshot — see ``README.md``.
    """
    problems: list[str] = []
    moment = now or datetime.now(timezone.utc)
    focus_path = _resolve_focus(focus_path)
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
                f"{where}: {problem}" for problem in _claim_problems(event, events, snapshot, focus_path)
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
        elif event.event == "reap":
            if not event.reaped_agent:
                problems.append(f"{where}: reap of #{event.issue} does not name the reaped agent")
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


def _claim_problems(
    event: ClaimEvent,
    events: list[ClaimEvent],
    snapshot: Snapshot,
    focus_path: Path | str | None = None,
) -> list[str]:
    """Structural problems with one claim record (schema-valid but unjustified).

    Time-aware by necessity: this replays *history* against *present* truth, so a
    claim that predates the issue's closure is legitimate record, not a violation.
    Only a claim still live at audit time is judged against the current snapshot —
    otherwise an agent that completes an issue is retroactively blamed for having
    claimed it.
    """
    problems: list[str] = []
    focus_path = _resolve_focus(focus_path)
    issue = snapshot.get(event.issue)
    if issue is None:
        return [f"#{event.issue}: claimed but absent from the snapshot"]

    if issue.closed:
        # Settled unless the claim is still live — a live claim on a closed issue
        # is a real problem; one ended by a release OR a reap is finished history.
        # When the snapshot carries closed_at the judgement is chronological: a
        # claim that predates closure was legitimate at the time.
        still_live = not any(
            later.event in ("release", "reap") and later.issue == event.issue
            for later in events
        )
        if still_live:
            if issue.closed_at:
                try:
                    claimed = parse_iso(event.at)
                except ValueError:
                    claimed = None
                if claimed is None:
                    # Cannot judge the chronology of an unparseable timestamp, but
                    # a live claim on a closed issue is still a real problem.
                    problems.append(f"#{event.issue}: claim on a closed issue was never released")
                elif claimed > parse_iso(issue.closed_at):
                    problems.append(
                        f"#{event.issue}: claimed after it was closed "
                        f"(claim {event.at}, closed_at {issue.closed_at})"
                    )
                # else: the claim predates closure — legitimate at the time, no problem.
            else:
                problems.append(f"#{event.issue}: claim on a closed issue was never released")
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
    if event.reason == REASON_BRAIN_DIRECTED:
        if not event.directive_id:
            problems.append(f"#{event.issue}: reason 'brain-directed' but no directive_id is recorded")
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
    elif event.reason == REASON_ACTIVE_EPIC_CHILD:
        problems.extend(_active_epic_child_problems(event, issue, snapshot, focus_path))
    return problems


def _active_epic_child_problems(
    event: ClaimEvent,
    issue: Issue,
    snapshot: Snapshot,
    focus_path: Path | str,
) -> list[str]:
    """Justify an ``active-epic-child`` claim: the parent IS the active epic (#707).

    Epic focus is an *additive* chain edge, so it must be held to the same bar as
    every other reason: the parent must exist in the snapshot, still be open, be
    an epic, and be the epic the fleet is currently driving. Anything else is an
    unjustified claim and is reported, never silently accepted.
    """
    problems: list[str] = []
    if issue.parent is None:
        problems.append(f"#{event.issue}: reason 'active-epic-child' but the issue declares no Parent: edge")
        return problems
    parent = snapshot.get(issue.parent)
    if parent is None:
        problems.append(
            f"#{event.issue}: reason 'active-epic-child' but parent #{issue.parent} is absent from the snapshot"
        )
        return problems
    if parent.closed:
        problems.append(f"#{event.issue}: reason 'active-epic-child' but parent #{issue.parent} is closed")
        return problems
    if not parent.is_epic:
        problems.append(f"#{event.issue}: reason 'active-epic-child' but parent #{issue.parent} is not an epic")
        return problems
    active = focus.active(snapshot, focus_path)
    if active is None or active.number != issue.parent:
        active_text = f"#{active.number}" if active is not None else "none"
        problems.append(
            f"#{event.issue}: reason 'active-epic-child' but #{issue.parent} is not the active epic "
            f"(active epic: {active_text})"
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


def _collect_file_events(path: Path, problems: list[str]) -> list[ClaimEvent]:
    """Parse a legacy ledger file, reporting malformed lines as problems."""
    events: list[ClaimEvent] = []
    if not path.exists():
        return events
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            events.append(parse_claim_event(json.loads(line), where=f"{path}:{lineno}"))
        except (json.JSONDecodeError, ValueError) as exc:
            problems.append(f"{path}:{lineno}: malformed record ({exc})")
    return events


def _collect_dir_events(path: Path, problems: list[str]) -> list[ClaimEvent]:
    """Parse a claims directory, reporting malformed records as problems."""
    events: list[ClaimEvent] = []
    if not path.is_dir():
        return events
    for child in sorted(path.glob("*.json")):
        try:
            obj = json.loads(child.read_text(encoding="utf-8"))
            events.append(parse_claim_event(obj, where=str(child)))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            problems.append(f"{child}: malformed record ({exc})")
    return events


def audit_ledger(
    path: Path | str = DEFAULT_CLAIMS_DIR,
    snapshot: Snapshot | None = None,
    now: datetime | None = None,
    focus_path: Path | str | None = None,
) -> list[str]:
    """Audit the full ledger (legacy file + claims directory) against the snapshot.

    Malformed records are reported as problems rather than raised, so the gate
    names them instead of crashing — the same contract as ``audit_text``, but
    over both storage forms in temporal order.
    """
    focus_path = _resolve_focus(focus_path)
    problems: list[str] = []
    events: list[ClaimEvent] = []
    target = Path(path)
    if target.is_file():
        events.extend(_collect_file_events(target, problems))
        sibling_dir = target.parent / DEFAULT_CLAIMS_DIR.name
        if sibling_dir.is_dir():
            events.extend(_collect_dir_events(sibling_dir, problems))
    else:
        # Directory (existing or not yet created): the frozen legacy file comes
        # first, then the one-file-per-event directory.
        sibling_file = target.parent / DEFAULT_LEDGER.name
        if sibling_file.is_file():
            events.extend(_collect_file_events(sibling_file, problems))
        if target.is_dir():
            events.extend(_collect_dir_events(target, problems))
    if snapshot is not None:
        problems.extend(audit(events, snapshot, now, focus_path))
    return problems


def audit_text(
    text: str,
    snapshot: Snapshot,
    now: datetime | None = None,
    focus_path: Path | str | None = None,
) -> list[str]:
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
    problems.extend(audit(events, snapshot, now, focus_path))
    return problems


def control_snapshot(now: datetime | None = None) -> Snapshot:
    """Deterministic fixture the self-control mutants are evaluated against.

    Shape: milestone ``CONTROL`` with #601 the frontier, #602 blocked by #603,
    #603 open and out of order, #604 closed, #605 a child of #601.
    """
    moment = now or datetime.now(timezone.utc)
    later = moment + timedelta(hours=1)
    earlier = moment - timedelta(hours=1)
    issues = {
        601: Issue(601, "frontier", milestone="CONTROL", labels=("type:task",)),
        602: Issue(602, "blocked", milestone="CONTROL", blocked_by=(603,)),
        603: Issue(603, "out of order", milestone="CONTROL"),
        604: Issue(604, "closed", state="closed", milestone="CONTROL"),
        605: Issue(605, "child", milestone="CONTROL", parent=601),
        607: Issue(607, "epic", milestone="CONTROL", labels=("type:epic",)),
        608: Issue(608, "closed later", state="closed", milestone="CONTROL",
                   closed_at=later.strftime("%Y-%m-%dT%H:%M:%SZ")),
        609: Issue(609, "closed earlier", state="closed", milestone="CONTROL",
                   closed_at=earlier.strftime("%Y-%m-%dT%H:%M:%SZ")),
        610: Issue(610, "child of the active epic", milestone="CONTROL", parent=607),
        611: Issue(611, "a second epic", milestone="CONTROL", labels=("type:epic",)),
        612: Issue(612, "child of the non-active epic", milestone="CONTROL", parent=611),
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
    expect_clean(
        "brain-directed",
        [
            ClaimEvent(
                event="claim",
                issue=603,
                agent="control-agent",
                at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                lane="control",
                reason=REASON_BRAIN_DIRECTED,
                ttl_hours=DEFAULT_TTL_HOURS,
                directive_id="d-1",
                directive_from="brain",
            )
        ],
    )
    expect_rejected("scavenging", [record(603, REASON_NEXT_IN_MILESTONE)])
    expect_rejected(
        "brain-directed-without-ref",
        [
            ClaimEvent(
                event="claim",
                issue=603,
                agent="control-agent",
                at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                lane="control",
                reason=REASON_BRAIN_DIRECTED,
                ttl_hours=DEFAULT_TTL_HOURS,
            )
        ],
    )
    expect_rejected("epic", [record(607, REASON_NEXT_IN_MILESTONE)])
    expect_rejected("blocked", [record(602, REASON_NEXT_IN_MILESTONE)])
    expect_rejected("closed", [record(604, REASON_NEXT_IN_MILESTONE)])
    # Epic focus (#707): the edge must name the ACTIVE epic, not just any parent.
    expect_clean("active-epic-child", [record(610, REASON_ACTIVE_EPIC_CHILD)])
    expect_rejected("out-of-epic-child", [record(612, REASON_ACTIVE_EPIC_CHILD)])
    expect_clean("claim-before-closure", [record(608, REASON_NEXT_IN_MILESTONE)])
    expect_rejected("claim-after-closure", [record(609, REASON_NEXT_IN_MILESTONE)])
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
