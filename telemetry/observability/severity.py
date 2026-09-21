"""telemetry/observability — alert severity state machine (issue #342).

---knowledge---
module_id: telemetry.observability.severity
system: telemetry
app: observability
solution_class: enterprise
patterns: [state-machine, bidirectional-escalation-recovery, named-reason]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [STATES, STATE_RANK, STATE_SEVERITY, STATE_DESCRIPTIONS, SEVERITIES, REASON_*]
invariants: "severity is a state, not a verdict: a breach escalates immediately and recovery is only after a sustained window"
gotchas: "the transition is deliberately not one-directional; it records since-when and what moved the subject"
related: ["#342", "#1510"]
do_not_duplicate: null
---knowledge---


WHY this exists: ``breach.py`` answers *one* question per evaluation — "is an
SLO out of budget **right now**" — and answers it from the current window
alone. That is a *verdict*, not a *state*: an operator surface needs to know
which severity a subject (a tenant, or one agent inside it) is in, since when,
what moved it there, and what would move it back.

This module is that state machine, and it is deliberately not one-directional:

* **breach** escalates immediately — ``OK`` -> ``WARNING`` (an error budget at
  risk) -> ``ALERT`` (an SLO target missed);
* **recovery** de-escalates — a subject that stops breaching returns to
  ``WARNING``/``OK`` and the *recovery* transition is recorded in the history.
  A state machine that can only go up is an alarm, not a state machine: an
  operator who cannot see a subject recover learns to ignore it.
* two states are **off the ladder** because they are not measures of health:
  ``NO_DATA`` (nothing was measured — no telemetry in the window, or no serve
  attempts at all) and ``PAUSED`` (an operator deliberately stopped
  evaluating a subject). Neither is ``OK``: *no data must never read as
  healthy*, and a pause must never hide what it paused over.

Honesty rules, inherited from the obs lane and enforced here:

* a subject with **no results at all** is ``NO_DATA``, never ``OK`` — "we
  measured nothing" is not "all good";
* ``PAUSED`` keeps the state it displaced (``previousState``) **and** the state
  the current verdicts justify (``observedState``), so a paused-and-breaching
  subject is visibly *both*;
* every transition carries its reason code and the SLO names that drove it, so
  the served surface can explain itself instead of just colouring a badge.

The machine is in-memory by design: :meth:`AlertStateMachine.snapshot` and
``history`` are serializable, so a durable store is a caller away, and the
serving adapter (``portal/server/ops_health.py``) re-evaluates on every read —
a process restart re-derives the ladder from the first observation
(``reason: first_observation``) rather than pretending to remember.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from telemetry.observability.model import now_utc_iso
from telemetry.observability.slos import (
    VERDICT_AT_RISK,
    VERDICT_BREACHED,
    VERDICT_NO_DATA,
    SloResult,
)

# --------------------------------------------------------------------------- #
# States, severities, ranks
# --------------------------------------------------------------------------- #
STATE_OK = "OK"
STATE_WARNING = "WARNING"
STATE_ALERT = "ALERT"
STATE_NO_DATA = "NO_DATA"
STATE_PAUSED = "PAUSED"

#: Every state a subject can be in (the closed vocabulary the shell renders).
STATES = (STATE_OK, STATE_WARNING, STATE_ALERT, STATE_NO_DATA, STATE_PAUSED)

#: Escalation rank of the *measured* states.  ``NO_DATA`` outranks
#: ``WARNING``: a silently-silent subject is more dangerous than a noisy one
#: (the obs lane's outcome-not-liveness doctrine — a tenant missing its window
#: pages, it does not whisper).  ``PAUSED`` is off-ladder and absent here.
STATE_RANK = {
    STATE_OK: 0,
    STATE_WARNING: 1,
    STATE_NO_DATA: 2,
    STATE_ALERT: 3,
}

SEVERITY_OK = "ok"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"
SEVERITY_PAUSED = "paused"

#: Badge severity per state (``NO_DATA`` is critical, matching AlertPolicy).
STATE_SEVERITY = {
    STATE_OK: SEVERITY_OK,
    STATE_WARNING: SEVERITY_WARNING,
    STATE_ALERT: SEVERITY_CRITICAL,
    STATE_NO_DATA: SEVERITY_CRITICAL,
    STATE_PAUSED: SEVERITY_PAUSED,
}

#: One-line meaning per state, so a client renders the vocabulary, not a guess.
STATE_DESCRIPTIONS = {
    STATE_OK: "every SLO within budget, over measured data",
    STATE_WARNING: "at least one SLO is consuming its error budget (at risk)",
    STATE_ALERT: "at least one SLO target is breached",
    STATE_NO_DATA: "no telemetry for the window (or no serve attempts): unknown, never healthy",
    STATE_PAUSED: "evaluation paused by an operator; the observed state is still reported",
}

# --------------------------------------------------------------------------- #
# Reason codes
# --------------------------------------------------------------------------- #
REASON_INITIAL = "first_observation"
REASON_BREACHED = "slo_target_breached"
REASON_AT_RISK = "error_budget_at_risk"
REASON_NO_DATA = "no_window_data"
REASON_NO_SLOS = "no_slos_declared"
REASON_RECOVERED = "recovered"
REASON_PAUSED = "paused_by_operator"
REASON_RESUMED = "resumed_by_operator"

#: The reason an escalation to each state carries.
_ESCALATION_REASON = {
    STATE_ALERT: REASON_BREACHED,
    STATE_NO_DATA: REASON_NO_DATA,
    STATE_WARNING: REASON_AT_RISK,
    STATE_OK: REASON_RECOVERED,
}


def _reason_for(target: str, results: Sequence[SloResult]) -> str:
    """Why a subject escalated to ``target``: an SLO drove it, or nothing exists."""
    if not results:
        return REASON_NO_SLOS
    return _ESCALATION_REASON[target]


def _first_reason(results: Sequence[SloResult]) -> str:
    """Why a subject entered the ladder the first time it was observed."""
    return REASON_INITIAL if results else REASON_NO_SLOS


def rank(state: str) -> int:
    """Escalation rank of a measured state (``PAUSED`` is off-ladder: -1)."""
    return STATE_RANK.get(state, -1)


def severity_for(state: str) -> str:
    """The badge severity for a state (fail closed on an unknown state)."""
    try:
        return STATE_SEVERITY[state]
    except KeyError:
        raise ValueError(f"unknown severity state: {state!r}") from None


# --------------------------------------------------------------------------- #
# Classification (pure: verdicts in, state out)
# --------------------------------------------------------------------------- #
def classify(results: Sequence[SloResult]) -> Tuple[str, dict[str, tuple[str, ...]]]:
    """The state a set of SLO verdicts justifies, plus the SLOs that drove it.

    Pure and total — no history, no clock.  The ladder is *worst wins*: a
    single ``BREACHED`` result is ``ALERT`` even when every other SLO is
    healthy, and the ``noData``/``atRisk`` groups stay on the result so the
    caller can see everything the state summarises.  An empty result set is
    ``NO_DATA`` (nothing measured), never ``OK``.
    """
    breached = tuple(r.name for r in results if r.verdict == VERDICT_BREACHED)
    no_data = tuple(r.name for r in results if r.verdict == VERDICT_NO_DATA)
    at_risk = tuple(r.name for r in results if r.verdict == VERDICT_AT_RISK)
    groups = {"breached": breached, "noData": no_data, "atRisk": at_risk}
    if breached:
        return STATE_ALERT, groups
    if no_data:
        return STATE_NO_DATA, groups
    if at_risk:
        return STATE_WARNING, groups
    if not results:
        return STATE_NO_DATA, groups
    return STATE_OK, groups


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StateTransition:
    """One recorded move of a subject's state (the audit trail of the ladder)."""

    at: str
    to_state: str
    reason: str
    from_state: Optional[str] = None
    drivers: Tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.to_state not in STATES:
            raise ValueError(f"unknown transition target state: {self.to_state!r}")
        if self.from_state is not None and self.from_state not in STATES:
            raise ValueError(f"unknown transition source state: {self.from_state!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "fromState": self.from_state,
            "toState": self.to_state,
            "reason": self.reason,
            "drivers": list(self.drivers),
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class SeverityState:
    """A subject's severity state: where it is, why, and what it observed.

    ``state`` is the machine's memory (it survives a single flapping window);
    ``observed_state`` is what the *latest* verdicts justify.  They differ
    exactly when de-escalation is waiting for confirmation
    (``pending_recovery``) or when the subject is paused — in both cases the
    divergence is visible rather than smoothed away.
    """

    subject: str
    state: str
    severity: str
    since: str
    observed_state: str
    observed_severity: str
    breached: Tuple[str, ...] = ()
    at_risk: Tuple[str, ...] = ()
    no_data: Tuple[str, ...] = ()
    previous_state: Optional[str] = None
    paused: Optional[Mapping[str, Any]] = None
    pending_recovery: int = 0
    recovery_confirmations: int = 1
    transitions: int = 0

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"unknown severity state: {self.state!r}")
        if self.observed_state not in STATES:
            raise ValueError(f"unknown observed state: {self.observed_state!r}")
        if not self.subject:
            raise ValueError("subject is required")

    @property
    def is_ok(self) -> bool:
        """True only for a genuinely measured-healthy subject."""
        return self.state == STATE_OK

    @property
    def is_alert(self) -> bool:
        return self.state == STATE_ALERT

    @property
    def is_paused(self) -> bool:
        return self.state == STATE_PAUSED

    @property
    def observed_differs(self) -> bool:
        """True when the live verdicts disagree with the remembered state."""
        return self.observed_state != self.state

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "state": self.state,
            "severity": self.severity,
            "since": self.since,
            "observedState": self.observed_state,
            "observedSeverity": self.observed_severity,
            "breached": list(self.breached),
            "atRisk": list(self.at_risk),
            "noData": list(self.no_data),
            "previousState": self.previous_state,
            "paused": dict(self.paused) if self.paused else None,
            "pendingRecovery": self.pending_recovery,
            "recoveryConfirmations": self.recovery_confirmations,
            "transitions": self.transitions,
            "description": STATE_DESCRIPTIONS[self.state],
        }


# --------------------------------------------------------------------------- #
# The machine
# --------------------------------------------------------------------------- #
class AlertStateMachine:
    """Per-subject alert severity with breach **and** recovery transitions.

    ``recovery_confirmations`` is how many consecutive *calmer* evaluations a
    subject must produce before it de-escalates (default 1 = immediate).  It
    only ever delays recovery — escalation is always immediate, so a flap can
    never hide a breach, only a recovery.
    """

    def __init__(
        self,
        *,
        recovery_confirmations: int = 1,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        if int(recovery_confirmations) < 1:
            raise ValueError("recovery_confirmations must be >= 1")
        self.recovery_confirmations = int(recovery_confirmations)
        self._clock = clock or now_utc_iso
        self._states: dict[str, SeverityState] = {}
        self._history: dict[str, list[StateTransition]] = {}

    # -- reads --------------------------------------------------------------
    def state(self, subject: str) -> Optional[SeverityState]:
        """The subject's current state, or ``None`` if never observed."""
        return self._states.get(subject)

    def subjects(self) -> list[str]:
        """Every subject the machine has observed, sorted."""
        return sorted(self._states)

    def history(self, subject: str) -> list[StateTransition]:
        """Every recorded transition for a subject, oldest first."""
        return list(self._history.get(subject, ()))

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Serializable state for every subject (states + transition counts)."""
        return {subject: state.to_dict() for subject, state in sorted(self._states.items())}

    # -- evaluation ---------------------------------------------------------
    def evaluate(
        self,
        subject: str,
        results: Sequence[SloResult],
        *,
        at: Optional[str] = None,
    ) -> SeverityState:
        """Feed one evaluation's verdicts for ``subject`` and return its state.

        Escalation is immediate; de-escalation needs ``recovery_confirmations``
        consecutive calmer reads.  A paused subject keeps ``PAUSED`` while its
        ``observed`` fields track the live verdicts, so the pause cannot be
        mistaken for health.
        """
        now = at or self._clock()
        target, groups = classify(results)
        previous = self._states.get(subject)

        if previous is not None and previous.state == STATE_PAUSED:
            # A pause stops *labelling*, never *measuring*: the observed state
            # keeps moving so the operator can see what is happening under it.
            state = replace(
                previous,
                observed_state=target,
                observed_severity=severity_for(target),
                breached=groups["breached"],
                at_risk=groups["atRisk"],
                no_data=groups["noData"],
            )
            self._states[subject] = state
            return state

        if previous is None:
            return self._commit(
                subject, previous=None, target=target, groups=groups,
                reason=_first_reason(results), at=now,
            )

        if target == previous.state:
            # Same rung: refresh the evidence, cancel any recovery countdown.
            return self._refresh(previous, groups, pending=0)

        if rank(target) > rank(previous.state):
            return self._commit(
                subject, previous=previous, target=target, groups=groups,
                reason=_reason_for(target, results), at=now,
            )

        # Calmer than the remembered state: de-escalate, but only once the
        # calm has been confirmed `recovery_confirmations` times.
        pending = previous.pending_recovery + 1
        if pending >= self.recovery_confirmations:
            return self._commit(
                subject, previous=previous, target=target, groups=groups,
                reason=REASON_RECOVERED, at=now,
            )
        return self._refresh(
            previous, groups, pending=pending, observed_state=target
        )

    # -- operator controls --------------------------------------------------
    def pause(
        self,
        subject: str,
        *,
        reason: str,
        actor: str = "",
        at: Optional[str] = None,
    ) -> SeverityState:
        """Stop evaluating a subject, recording who paused it and why.

        A subject paused before it was ever observed lands on ``PAUSED`` with
        ``observedState == NO_DATA``: pausing something unmeasured is not a
        claim that it is healthy.
        """
        now = at or self._clock()
        previous = self._states.get(subject)
        if previous is not None and previous.state == STATE_PAUSED:
            return previous
        pause = {"reason": reason, "actor": actor, "at": now}
        from_state = previous.state if previous is not None else None
        target = previous.observed_state if previous is not None else STATE_NO_DATA
        return self._commit(
            subject,
            previous=previous,
            target=STATE_PAUSED,
            groups={
                "breached": previous.breached if previous else (),
                "noData": previous.no_data if previous else (),
                "atRisk": previous.at_risk if previous else (),
            },
            reason=REASON_PAUSED,
            at=now,
            paused=pause,
            observed_state=target,
            from_state=from_state,
        )

    def resume(self, subject: str, *, at: Optional[str] = None) -> SeverityState:
        """Resume a paused subject: it lands on the state its verdicts justify.

        Anything that happened during the pause (a breach, a silent window) is
        therefore visible the moment evaluation resumes — a pause never buys a
        subject a clean bill of health.
        """
        now = at or self._clock()
        previous = self._states.get(subject)
        if previous is None:
            raise KeyError(f"no such subject: {subject!r}")
        if previous.state != STATE_PAUSED:
            return previous
        return self._commit(
            subject,
            previous=previous,
            target=previous.observed_state,
            groups={
                "breached": previous.breached,
                "noData": previous.no_data,
                "atRisk": previous.at_risk,
            },
            reason=REASON_RESUMED,
            at=now,
            paused=None,
            from_state=STATE_PAUSED,
        )

    # -- internals ----------------------------------------------------------
    def _refresh(
        self,
        previous: SeverityState,
        groups: Mapping[str, tuple[str, ...]],
        *,
        pending: int,
        observed_state: Optional[str] = None,
    ) -> SeverityState:
        """Same rung (or a held rung): update evidence without a transition."""
        observed = observed_state or previous.state
        state = replace(
            previous,
            observed_state=observed,
            observed_severity=severity_for(observed),
            breached=tuple(groups["breached"]),
            at_risk=tuple(groups["atRisk"]),
            no_data=tuple(groups["noData"]),
            pending_recovery=pending,
        )
        self._states[previous.subject] = state
        return state

    def _commit(
        self,
        subject: str,
        *,
        previous: Optional[SeverityState],
        target: str,
        groups: Mapping[str, tuple[str, ...]],
        reason: str,
        at: str,
        paused: Optional[Mapping[str, Any]] = None,
        observed_state: Optional[str] = None,
        from_state: Optional[str] = None,
    ) -> SeverityState:
        """Record a transition and install the new state."""
        observed = observed_state or (
            previous.observed_state if previous is not None else target
        )
        if target != STATE_PAUSED:
            observed = target
        source = from_state if from_state is not None else (
            previous.state if previous is not None else None
        )
        state = SeverityState(
            subject=subject,
            state=target,
            severity=severity_for(target),
            since=at,
            observed_state=observed,
            observed_severity=severity_for(observed),
            breached=tuple(groups["breached"]),
            at_risk=tuple(groups["atRisk"]),
            no_data=tuple(groups["noData"]),
            previous_state=source,
            paused=paused,
            pending_recovery=0,
            recovery_confirmations=self.recovery_confirmations,
            transitions=(previous.transitions + 1 if previous is not None else 1),
        )
        self._states[subject] = state
        self._history.setdefault(subject, []).append(
            StateTransition(
                at=at,
                from_state=source,
                to_state=target,
                reason=reason,
                drivers=tuple(groups["breached"] or groups["noData"] or groups["atRisk"]),
                detail=(
                    {"observedState": observed} if target == STATE_PAUSED else {}
                ),
            )
        )
        return state


__all__ = [
    "STATE_OK",
    "STATE_WARNING",
    "STATE_ALERT",
    "STATE_NO_DATA",
    "STATE_PAUSED",
    "STATES",
    "STATE_RANK",
    "STATE_SEVERITY",
    "STATE_DESCRIPTIONS",
    "SEVERITY_OK",
    "SEVERITY_WARNING",
    "SEVERITY_CRITICAL",
    "SEVERITY_PAUSED",
    "REASON_INITIAL",
    "REASON_BREACHED",
    "REASON_AT_RISK",
    "REASON_NO_DATA",
    "REASON_NO_SLOS",
    "REASON_RECOVERED",
    "REASON_PAUSED",
    "REASON_RESUMED",
    "rank",
    "severity_for",
    "classify",
    "StateTransition",
    "SeverityState",
    "AlertStateMachine",
]
