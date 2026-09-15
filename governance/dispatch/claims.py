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
* **A2A arbitration** (issue #726) — ``arbitrate`` proves issue -> epic -> lane
  ownership from *named evidence* before a unit is dispatched, and refuses a
  closed issue, a closed epic, a unit another lane already holds, an unowned unit
  or a stale board. ``claim`` runs it, so the refusal bites at the mutation point
  as well as at the read-only ``dispatch`` seam, and
  ``arbitration_self_control`` provokes every refusal (a refusal that cannot fire
  is a formality).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import tempfile
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
    ARBITRATION_REFUSALS,
    REASON_ACTIVE_EPIC_CHILD,
    REASON_ALREADY_CLAIMED,
    REASON_BLOCKED,
    REASON_BRAIN_DIRECTED,
    REASON_CHILD_OF_CLAIM,
    REASON_EPIC_CLOSED,
    REASON_EPIC_NOT_WORKABLE,
    REASON_ISSUE_CLOSED,
    REASON_NEXT_IN_MILESTONE,
    REASON_OUT_OF_EPIC_POOLED,
    REASON_PROVENANCE_MISMATCH,
    REASON_SNAPSHOT_STALE,
    REASON_SUCCESSOR_OF_CLAIM,
    REASON_UNKNOWN_ISSUE,
    REASON_UNOWNED,
    Arbitration,
    ClaimEvent,
    Issue,
    Provenance,
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


# --- A2A dispatch arbitration (issue #726) -----------------------------------
#
# A directive names an issue, but nothing bound it to the epic that owns the work
# or to the lane that holds it: a directive could be dispatched for a closed issue
# or under a closed epic, and two directives could drive one issue. Arbitration
# proves the chain issue -> epic -> lane from evidence it names before a unit is
# routed, so the receiver can see *who owns this* rather than trusting the sender.


def _board_evidence(snapshot: Snapshot, snapshot_path: str = "", snapshot_sha256: str = "") -> str:
    """Name the board a verdict was judged against (source, generation, digest)."""
    digest = f", sha256 {snapshot_sha256[:12]}" if snapshot_sha256 else ""
    where = snapshot_path or "<in-memory snapshot>"
    return f"{where} (source {snapshot.source or 'unknown'}, generated {snapshot.generated_at or 'unknown'}{digest})"


def _issue_evidence(issue: Issue) -> str:
    """Name the fields of one issue a verdict read, so a refusal can quote them."""
    fields = [f"issue #{issue.number} state={issue.state or 'unknown'}"]
    if issue.closed_at:
        fields.append(f"closed_at={issue.closed_at}")
    if issue.parent is not None:
        fields.append(f"parent=#{issue.parent}")
    if issue.milestone:
        fields.append(f"milestone={issue.milestone}")
    if issue.labels:
        fields.append(f"labels={','.join(issue.labels)}")
    return " ".join(fields)


def _directive_task(directive: dict | None) -> dict:
    task = (directive or {}).get("task")
    return task if isinstance(task, dict) else {}


def _directive_lane(directive: dict | None) -> str:
    return str(_directive_task(directive).get("lane") or "")


def _directive_epic(directive: dict | None) -> int | None:
    value = _directive_task(directive).get("epic")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _directive_ref(directive_id: str) -> str:
    return str(SENT_DIR / f"{directive_id}.json")


def arbitrate(
    issue_number: int,
    agent: str,
    lane: str,
    snapshot: Snapshot,
    ledger: Path | str = DEFAULT_CLAIMS_DIR,
    lock_dir: Path | str = DEFAULT_LOCK_DIR,
    directive_id: str = "",
    require_lane: bool = False,
    snapshot_path: str = "",
    snapshot_sha256: str = "",
    now: datetime | None = None,
    stale_minutes: int = DEFAULT_STALENESS_MINUTES,
) -> Arbitration:
    """Prove issue -> epic -> lane ownership before a unit is dispatched (#726).

    Returns the granted arbitration, or raises ``ClaimRefused`` naming the reason
    *and the evidence it checked*: the board snapshot (source, generation, digest)
    and the issue fields it read, the epic the issue declares, the live claim
    record and lock that hold a unit, or the directive envelope that declared a
    provenance the board does not corroborate.

    ``require_lane`` is for the dispatch seam, where a unit no lane owns must be
    refused. The claim path leaves it off so its documented ``--lane`` default is
    unchanged: one arbitration serves both, and neither can grant a unit the other
    would refuse.
    """
    moment = now or datetime.now(timezone.utc)
    board = _board_evidence(snapshot, snapshot_path, snapshot_sha256)

    issue = snapshot.get(issue_number)
    if issue is None:
        raise ClaimRefused(REASON_UNKNOWN_ISSUE, f"#{issue_number} is absent from the board — evidence: {board}")
    if issue.closed:
        raise ClaimRefused(
            REASON_ISSUE_CLOSED,
            f"#{issue_number} is closed — evidence: {board} {_issue_evidence(issue)}",
        )

    # The epic half of the provenance: an issue whose declared epic is closed has
    # no owner to work under, so the unit cannot prove issue -> epic -> lane.
    epic_number = issue.parent
    epic = snapshot.get(epic_number) if epic_number is not None else None
    if epic is not None and epic.closed:
        raise ClaimRefused(
            REASON_EPIC_CLOSED,
            f"#{issue_number}'s epic is closed — evidence: {board} {_issue_evidence(issue)}; "
            f"epic {_issue_evidence(epic)}",
        )
    if issue.is_epic:
        raise ClaimRefused(
            REASON_EPIC_NOT_WORKABLE,
            f"#{issue_number} is an epic: it closes with its children and is not a unit of work "
            f"— evidence: {board} {_issue_evidence(issue)}",
        )

    open_blockers = snapshot.blockers_open(issue)
    if open_blockers:
        listed = ", ".join(f"#{number}" for number in open_blockers)
        raise ClaimRefused(REASON_BLOCKED, f"#{issue_number} is blocked by {listed} — evidence: {board}")

    # The lane half: a second live claim on a held unit is refused, naming the
    # holder and the records that prove it.
    held = active_claims(read_ledger(ledger), moment).get(issue_number)
    if held is not None:
        if held.agent == agent:
            detail = f"{agent} already holds #{issue_number}: release it before dispatching again"
        else:
            detail = (
                f"#{issue_number} is held by another lane: {held.agent} (lane {held.lane or 'unstated'})"
            )
        raise ClaimRefused(
            REASON_ALREADY_CLAIMED,
            f"{detail}, live since {held.at} (reason {held.reason or 'unstated'}) — evidence: live claim in "
            f"ledger {ledger} for #{issue_number}, lock {lock_path(issue_number, lock_dir)}",
        )

    directive: dict | None = None
    if directive_id:
        directive = _load_brain_directive(directive_id, issue_number)
    directive_ref = _directive_ref(directive_id) if directive_id else "<no directive>"

    # A directive's own provenance must be corroborated by the board: a declared
    # epic or lane the board cannot confirm is an unproven claim of ownership.
    declared_epic = _directive_epic(directive)
    if declared_epic is not None and declared_epic != epic_number:
        recorded = f"#{epic_number}" if epic_number is not None else "no Parent edge"
        raise ClaimRefused(
            REASON_PROVENANCE_MISMATCH,
            f"directive {directive_id} declares epic #{declared_epic} for #{issue_number} but the board records "
            f"{recorded} — evidence: {directive_ref} task.epic={declared_epic}; {board} {_issue_evidence(issue)}",
        )

    declared_lane = _directive_lane(directive)
    if declared_lane and lane.strip() and declared_lane != lane.strip():
        raise ClaimRefused(
            REASON_PROVENANCE_MISMATCH,
            f"directive {directive_id} declares lane {declared_lane!r} but this dispatch names lane {lane!r} "
            f"— evidence: {directive_ref} task.lane={declared_lane}; requested lane={lane}",
        )

    claimed_lane = (lane or declared_lane).strip()
    if require_lane and not claimed_lane:
        declared = f"; {directive_ref} declares no task.lane" if directive_id else ""
        raise ClaimRefused(
            REASON_UNOWNED,
            f"no lane owns #{issue_number}: the dispatch names no lane and no directive declares one "
            f"— evidence: requested lane=<empty>{declared}",
        )

    if is_stale(snapshot, stale_minutes, moment):
        age = age_minutes(snapshot, moment)
        raise ClaimRefused(
            REASON_SNAPSHOT_STALE,
            f"snapshot is {age:.1f}m old (threshold {stale_minutes}m) — evidence: {board}; "
            "refresh first: python3 governance/dispatch/cli.py snapshot --from-github",
        )

    lane_evidence = (
        f"lane {claimed_lane!r} named by the dispatch"
        if lane.strip()
        else f"lane {claimed_lane!r} declared by {directive_ref}"
    )
    provenance = Provenance(
        issue=issue_number,
        epic=epic_number,
        lane=claimed_lane,
        evidence=(
            f"{board} {_issue_evidence(issue)}",
            f"epic {_issue_evidence(epic)}" if epic is not None else "epic: the issue declares no Parent edge",
            lane_evidence,
            f"directive {directive_ref}" if directive_id else "directive: none (claimed as part of the active chain)",
            f"ledger {ledger} holds no live claim on #{issue_number}",
        ),
    )
    return Arbitration(
        issue=issue_number,
        agent=agent,
        lane=claimed_lane,
        epic=epic_number,
        directive_id=directive_id,
        provenance=provenance,
    )


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
    """Claim an issue after arbitrating ownership, then order.

    Raises ``ClaimRefused`` when the unit cannot prove issue -> epic -> lane
    ownership (#726) or is not the next step in the active chain.

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

    # Arbitration first: it is the refusal that names the evidence, so a closed
    # issue, a closed epic, a unit another lane already holds, an unowned unit or a
    # stale board is refused with the board state it was judged against. A live
    # claim blocks anyone else; an expired one may be taken over, so a dead agent
    # cannot wedge the chain forever.
    arbitration = arbitrate(
        issue_number,
        agent,
        lane,
        snapshot,
        ledger=ledger,
        lock_dir=lock_dir,
        directive_id=directive_id,
        snapshot_sha256=snapshot_sha256,
        now=moment,
        stale_minutes=stale_minutes,
    )
    lane = arbitration.lane
    takeover = latest is not None and is_expired(latest, moment) and latest.agent != agent

    # Arbitration already validated the envelope; re-reading it here keeps the
    # claim's own reason decided from the directive this claim names.
    directive: dict | None = None
    if directive_id:
        directive = _load_brain_directive(directive_id, issue_number)

    if directive is not None:
        reason = REASON_BRAIN_DIRECTED
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
        reason = verdict.reason

    if not _acquire_lock(issue_number, lock_dir, takeover=takeover):
        raise ClaimRefused(
            REASON_ALREADY_CLAIMED,
            f"#{issue_number} lock {lock_path(issue_number, lock_dir)} is held by another agent",
        )

    event_name = "take-over" if takeover else "claim"
    event = ClaimEvent(
        event=event_name,
        issue=issue_number,
        agent=agent,
        at=now_iso() if now is None else moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        lane=lane,
        base_commit=base_commit,
        snapshot_sha256=snapshot_sha256,
        reason=reason,
        ttl_hours=ttl_hours,
        directive_id=directive_id,
        directive_from="brain" if directive is not None else "",
        provenance=arbitration.provenance,
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


def _still_live(event: ClaimEvent, events: list[ClaimEvent]) -> bool:
    """Whether no later release/reap settled this claim (its issue is still held)."""
    return not any(later.event in ("release", "reap") and later.issue == event.issue for later in events)


def _provenance_problems(event: ClaimEvent, issue: Issue) -> list[str]:
    """A recorded provenance must agree with the board and with the claim itself.

    Absence is not judged: pre-#726 records carry none and are frozen history, and
    the requirement that a dispatched unit *has* a provenance is enforced where it
    can bite — at dispatch (``arbitration_self_control``) and, for a routed unit,
    by the ``brain-directed`` check below. A recorded provenance that contradicts
    the board, though, is wrong however old it is.
    """
    recorded = event.provenance
    if recorded is None:
        return []
    problems: list[str] = []
    if recorded.issue != event.issue:
        problems.append(f"#{event.issue}: provenance records issue #{recorded.issue}, not the claimed unit")
    if recorded.epic != issue.parent:
        recorded_epic = f"#{recorded.epic}" if recorded.epic is not None else "none"
        board_epic = f"#{issue.parent}" if issue.parent is not None else "none"
        problems.append(
            f"#{event.issue}: provenance records epic {recorded_epic} but the board's Parent edge is {board_epic}"
        )
    if recorded.lane != event.lane:
        problems.append(
            f"#{event.issue}: provenance records lane {recorded.lane or '<unstated>'} "
            f"but the claim records lane {event.lane or '<unstated>'}"
        )
    return problems


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
    problems.extend(_provenance_problems(event, issue))

    if issue.closed:
        # Settled unless the claim is still live — a live claim on a closed issue
        # is a real problem; one ended by a release OR a reap is finished history.
        # When the snapshot carries closed_at the judgement is chronological: a
        # claim that predates closure was legitimate at the time.
        still_live = _still_live(event, events)
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
        elif event.provenance is None and _still_live(event, events):
            # A routed unit is exactly the A2A case: while it is live it must record
            # which epic and which lane own it, not merely that a directive named it
            # (#726). A settled record is history — the committed ledger carries
            # pre-#726 brain-directed claims, and an audit that retro-blames them
            # cannot be green on its own repository.
            problems.append(
                f"#{event.issue}: brain-directed via {event.directive_id} records no issue -> epic -> lane provenance"
            )
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
    #603 open and out of order, #604 closed, #605 a child of #601, #607 the active
    epic, #610 a child of it, #611 a second (non-active) epic with #612 its child,
    and — for the A2A arbitration controls (#726) — #606 an open child of the
    closed epic #613, plus #614 as the unit the ledger fixture holds.
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
        606: Issue(606, "child of a closed epic", milestone="CONTROL", parent=613),
        613: Issue(613, "closed epic", state="closed", milestone="CONTROL", labels=("type:epic",),
                   closed_at=earlier.strftime("%Y-%m-%dT%H:%M:%SZ")),
        614: Issue(614, "held by another lane", milestone="CONTROL"),
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

    def record(
        issue: int,
        reason: str,
        agent: str = "control-agent",
        event: str = "claim",
        provenance: Provenance | None = None,
    ) -> ClaimEvent:
        return ClaimEvent(
            event=event,
            issue=issue,
            agent=agent,
            at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
            lane="control",
            reason=reason,
            ttl_hours=DEFAULT_TTL_HOURS,
            provenance=provenance,
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
                provenance=Provenance(issue=603, lane="control", evidence=("self-control fixture",)),
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
    # Provenance mutants (#726): recorded ownership that the board contradicts,
    # and a routed unit that recorded none.
    expect_rejected(
        "provenance-epic-mismatch",
        [record(601, REASON_NEXT_IN_MILESTONE, provenance=Provenance(issue=601, epic=999, lane="control"))],
    )
    expect_rejected(
        "provenance-lane-mismatch",
        [record(601, REASON_NEXT_IN_MILESTONE, provenance=Provenance(issue=601, lane="another-lane"))],
    )
    expect_rejected(
        "provenance-issue-mismatch",
        [record(601, REASON_NEXT_IN_MILESTONE, provenance=Provenance(issue=602, lane="control"))],
    )
    expect_rejected(
        "brain-directed-without-provenance",
        [
            ClaimEvent(
                event="claim",
                issue=601,
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
    expect_clean(
        "provenance-agrees",
        [
            record(601, REASON_NEXT_IN_MILESTONE, provenance=Provenance(issue=601, lane="control")),
            record(605, REASON_CHILD_OF_CLAIM, provenance=Provenance(issue=605, epic=601, lane="control")),
        ],
    )
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


def arbitration_self_control(now: datetime | None = None) -> list[str]:
    """Prove every dispatch refusal can bite (anti-formality, GR-12, issue #726).

    ``self_control`` proves the *ledger* rules can fail. These prove the
    *dispatch* refusals can: a refusal that cannot fire would let a directive be
    routed for a closed issue or under a closed epic, or let a second lane drive a
    unit another lane already holds. Every reason in ``ARBITRATION_REFUSALS`` must
    be provoked with the evidence it is supposed to name, and a valid unit must
    still be granted — otherwise the gate fails.

    The fixtures are in a temporary directory and the sent mailbox is rebound for
    the duration, so this control touches no repository state.
    """
    moment = now or datetime.now(timezone.utc)
    snapshot = control_snapshot(moment)
    stale = Snapshot(
        generated_at=(moment - timedelta(minutes=DEFAULT_STALENESS_MINUTES + 5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        source="self-control",
        issues=snapshot.issues,
    )
    problems: list[str] = []
    provoked: list[str] = []

    global SENT_DIR  # the sent mailbox is rebound only for this control
    original_sent = SENT_DIR
    with tempfile.TemporaryDirectory(prefix="dispatch-arbitration.") as tmp:
        root = Path(tmp)
        ledger = root / "claims"
        locks = root / "locks"
        sent = root / "sent"
        sent.mkdir(parents=True)
        SENT_DIR = sent
        try:
            # A unit held by another lane, in the ledger and lock the refusal names.
            append_event(
                ClaimEvent(
                    event="claim",
                    issue=614,
                    agent="holder-agent",
                    at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    lane="holder-lane",
                    reason=REASON_NEXT_IN_MILESTONE,
                    ttl_hours=DEFAULT_TTL_HOURS,
                ),
                ledger,
            )

            def write_directive(directive_id: str, task: dict) -> None:
                (sent / f"{directive_id}.json").write_text(
                    json.dumps(
                        {
                            "from": "brain",
                            "to": "lane",
                            "type": "directive",
                            "id": directive_id,
                            "task": task,
                        }
                    ),
                    encoding="utf-8",
                )

            write_directive("d-epic", {"issue": 601, "epic": 999})
            write_directive("d-lane", {"issue": 601, "lane": "other-lane"})

            def refuse(
                name: str,
                reason: str,
                issue: int,
                *,
                lane: str = "control-lane",
                agent: str = "control-agent",
                directive_id: str = "",
                require_lane: bool = False,
                board: Snapshot = snapshot,
                must_contain: tuple[str, ...] = (),
            ) -> None:
                try:
                    arbitrate(
                        issue,
                        agent,
                        lane,
                        board,
                        ledger=ledger,
                        lock_dir=locks,
                        directive_id=directive_id,
                        require_lane=require_lane,
                        now=moment,
                    )
                except ClaimRefused as exc:
                    provoked.append(exc.reason)
                    if exc.reason != reason:
                        problems.append(f"arbitration['{name}']: expected {reason}, got {exc.reason}")
                    if "evidence:" not in exc.detail:
                        problems.append(f"arbitration['{name}']: the refusal names no evidence ({exc.detail})")
                    for marker in must_contain:
                        if marker not in exc.detail:
                            problems.append(
                                f"arbitration['{name}']: the refusal does not name {marker!r} ({exc.detail})"
                            )
                else:
                    problems.append(f"arbitration['{name}']: the refusal did not bite ({reason})")

            refuse("unknown-issue", REASON_UNKNOWN_ISSUE, 999, must_contain=("#999",))
            refuse("closed-issue", REASON_ISSUE_CLOSED, 604, must_contain=("state=closed",))
            refuse("closed-epic", REASON_EPIC_CLOSED, 606, must_contain=("#613", "state=closed"))
            refuse("epic-is-not-work", REASON_EPIC_NOT_WORKABLE, 607, must_contain=("type:epic",))
            refuse("blocked", REASON_BLOCKED, 602, must_contain=("#603",))
            refuse(
                "held-by-another-lane",
                REASON_ALREADY_CLAIMED,
                614,
                must_contain=("holder-agent", "holder-lane"),
            )
            refuse(
                "directive-epic-mismatch",
                REASON_PROVENANCE_MISMATCH,
                601,
                directive_id="d-epic",
                must_contain=("task.epic=999", "no Parent edge"),
            )
            refuse(
                "directive-lane-mismatch",
                REASON_PROVENANCE_MISMATCH,
                601,
                lane="control-lane",
                directive_id="d-lane",
                must_contain=("other-lane", "requested lane=control-lane"),
            )
            refuse("unowned", REASON_UNOWNED, 601, lane="", require_lane=True, must_contain=("no lane owns",))
            refuse("stale-board", REASON_SNAPSHOT_STALE, 601, board=stale, must_contain=("refresh first",))

            try:
                granted = arbitrate(
                    601,
                    "control-agent",
                    "control-lane",
                    snapshot,
                    ledger=ledger,
                    lock_dir=locks,
                    now=moment,
                )
            except ClaimRefused as exc:
                # A control that refuses a unit it must grant is itself a failure:
                # report it rather than letting the exception escape the gate.
                granted = None
                problems.append(f"arbitration['grant']: a valid unit was refused ({exc.reason} — {exc.detail})")
        finally:
            SENT_DIR = original_sent

    if granted is not None and (
        granted.lane != "control-lane" or granted.epic is not None or not granted.provenance.evidence
    ):
        problems.append(
            "arbitration['grant']: a valid unit did not record its issue -> epic -> lane provenance "
            f"({granted.provenance.to_json()})"
        )
    unprovoked = [reason for reason in ARBITRATION_REFUSALS if reason not in set(provoked)]
    if unprovoked:
        problems.append(
            "arbitration control: no provoked refusal for "
            + ", ".join(unprovoked)
            + " (an unprovoked refusal may not fire)"
        )
    return problems
