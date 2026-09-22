"""Deterministic SLA ageing for support issues (issue #650, acceptance criterion 2).

Acceptance criterion 2 requires that "a support issue ages into SLA-breach state
**deterministically**". Determinism is not a style preference here, it is the
whole property: an SLA board that ages against the wall clock cannot be asserted
on, cannot be reproduced from an incident, and reports a different answer every
time it is read. So:

* **the clock is a parameter.** :func:`age` takes ``now`` as an ISO-8601 UTC
  timestamp string. No function in this module calls ``datetime.now``.
* **the window is a declaration.** The policy named by the issue's own
  ``priority`` field is read from the definition set — response and resolution
  windows in whole minutes, plus the warning threshold as an integer percentage.
  All arithmetic is integer arithmetic, so no floating-point threshold can flip a
  state between two runs.
* **a clock that has stopped is still judged.** If the issue carries
  ``responded_at`` / ``resolved_at``, the clock's elapsed time is measured to
  that instant rather than to ``now`` — a response that arrived after the window
  is a breach even though the issue is closed, because otherwise closing an
  issue would retroactively erase the breach.

The three states are ordered ``ok`` < ``due`` < ``breached``, and an issue's
overall state is the worse of its two clocks: a breached response window is a
breach even while the resolution window is still comfortable.

Two refusals, each naming the offender: ``unknown-policy`` (the issue's priority
names a policy the declaration set does not have — an undeclared policy is
never defaulted to a safest-case window) and ``clock-regression`` (``now`` is
earlier than the instant the clock started, which means one of the two
timestamps is wrong and the ageing cannot be trusted).

---knowledge---
module_id: integrations.erp.crm.sla
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [parse_timestamp, format_timestamp, add_minutes, minutes_between, Clock, SLAState, worst, policy_for, (+1 more)]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .definitions import DefinitionSet, SLAPolicy
from .model import Document, Refused

#: The one accepted timestamp form. Strict on purpose: an unparseable or
#: timezone-less string is refused rather than guessed at.
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

STATE_OK = "ok"
STATE_DUE = "due"
STATE_BREACHED = "breached"

#: Worst-first ordering. `state` of an issue is the worse of its two clocks.
SEVERITY = (STATE_BREACHED, STATE_DUE, STATE_OK)

PHASE_RUNNING = "running"
PHASE_STOPPED = "stopped"


def parse_timestamp(value: object, *, where: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp, or refuse naming the field."""
    if not isinstance(value, str):
        raise Refused(
            "invalid-timestamp", f"{where}: expected an ISO-8601 UTC string, got {value!r}"
        )
    try:
        return datetime.strptime(value, TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise Refused(
            "invalid-timestamp",
            f"{where}: {value!r} is not in the accepted form {TS_FORMAT} ({exc})",
        ) from exc


def format_timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TS_FORMAT)


def add_minutes(start: datetime, minutes: int) -> datetime:
    return start + timedelta(minutes=minutes)


def minutes_between(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


@dataclass(frozen=True)
class Clock:
    """One SLA clock: its window, its due instant, and its verdict."""

    phase: str
    state: str
    due_at: str
    elapsed_minutes: int
    remaining_minutes: int


@dataclass(frozen=True)
class SLAState:
    """An issue's ageing at one instant, as a value an SLA board can render."""

    issue: str
    policy: str
    opened_at: str
    now: str
    elapsed_minutes: int
    state: str
    response: Clock
    resolution: Clock

    def to_dict(self) -> dict:
        return {
            "issue": self.issue,
            "policy": self.policy,
            "openedAt": self.opened_at,
            "now": self.now,
            "elapsedMinutes": self.elapsed_minutes,
            "state": self.state,
            "response": {
                "phase": self.response.phase,
                "state": self.response.state,
                "dueAt": self.response.due_at,
                "elapsedMinutes": self.response.elapsed_minutes,
                "remainingMinutes": self.response.remaining_minutes,
            },
            "resolution": {
                "phase": self.resolution.phase,
                "state": self.resolution.state,
                "dueAt": self.resolution.due_at,
                "elapsedMinutes": self.resolution.elapsed_minutes,
                "remainingMinutes": self.resolution.remaining_minutes,
            },
        }


def _clock(
    *,
    start: datetime,
    window_minutes: int,
    warn_percent: int,
    stop: Optional[datetime],
    now: datetime,
    where: str,
) -> Clock:
    if now < start:
        raise Refused(
            "clock-regression",
            f"{where}: the reading instant {format_timestamp(now)} precedes the "
            f"clock's start {format_timestamp(start)}",
        )
    # A stop *later* than the reading instant has not happened yet at ``now``, so
    # the clock is still running then: judging it at the stop would answer a
    # question about the future.
    stopped = stop is not None and stop <= now
    measured_to = stop if stopped else now
    elapsed = minutes_between(start, measured_to)
    if elapsed < 0:
        raise Refused(
            "clock-regression",
            f"{where}: the clock stops at {format_timestamp(measured_to)} which precedes "
            f"its start {format_timestamp(start)}",
        )
    if elapsed >= window_minutes:
        state = STATE_BREACHED
    elif elapsed * 100 >= window_minutes * warn_percent:
        state = STATE_DUE
    else:
        state = STATE_OK
    return Clock(
        phase=PHASE_STOPPED if stopped else PHASE_RUNNING,
        state=state,
        due_at=format_timestamp(add_minutes(start, window_minutes)),
        elapsed_minutes=elapsed,
        remaining_minutes=max(0, window_minutes - elapsed),
    )


def worst(*states: str) -> str:
    """The most severe state in ``states``."""
    for candidate in SEVERITY:
        if candidate in states:
            return candidate
    return STATE_OK


def policy_for(document: Document, definitions: DefinitionSet) -> SLAPolicy:
    """The policy an issue names through its own ``priority`` field."""
    priority = document.fields.get("priority")
    if not isinstance(priority, str) or not priority:
        raise Refused(
            "unknown-policy",
            f"{document.id}: no policy can be resolved — the issue carries no "
            "usable priority field",
        )
    return definitions.policy(priority)


def age(document: Document, now: str, *, definitions: DefinitionSet) -> SLAState:
    """The issue's SLA ageing at ``now`` (an ISO-8601 UTC timestamp string)."""
    policy = policy_for(document, definitions)
    opened_raw = document.fields.get("opened_at")
    opened_at = parse_timestamp(opened_raw, where=f"{document.id}.opened_at")
    moment = parse_timestamp(now, where="now")

    responded_raw = document.fields.get("responded_at")
    responded_at = (
        parse_timestamp(responded_raw, where=f"{document.id}.responded_at")
        if isinstance(responded_raw, str) and responded_raw
        else None
    )
    resolved_raw = document.fields.get("resolved_at")
    resolved_at = (
        parse_timestamp(resolved_raw, where=f"{document.id}.resolved_at")
        if isinstance(resolved_raw, str) and resolved_raw
        else None
    )

    response = _clock(
        start=opened_at,
        window_minutes=policy.respond_minutes,
        warn_percent=policy.warn_percent,
        stop=responded_at,
        now=moment,
        where=f"{document.id}.respond",
    )
    resolution = _clock(
        start=opened_at,
        window_minutes=policy.resolve_minutes,
        warn_percent=policy.warn_percent,
        stop=resolved_at,
        now=moment,
        where=f"{document.id}.resolve",
    )
    return SLAState(
        issue=document.id,
        policy=policy.name,
        opened_at=format_timestamp(opened_at),
        now=format_timestamp(moment),
        elapsed_minutes=minutes_between(opened_at, moment),
        state=worst(response.state, resolution.state),
        response=response,
        resolution=resolution,
    )
