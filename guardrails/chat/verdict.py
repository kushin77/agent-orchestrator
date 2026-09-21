"""guardrails.chat.verdict — the chat guard's enforcement tri-state.

Every chat guard in this lane answers with the *enforcement* tri-state the
platform already speaks — ``BLOCK`` / ``WARN`` / ``LOG``
(``policy.decision.DecisionLevel``, AO-GR-19) — and the turn's verdict is the
strongest answer any guard gave. The vocabulary is **consumed** from
``guardrails/policy`` (issue #26): one vocabulary, one ordering, no second enum
to drift against.

Two properties keep the tri-state honest (issue #507 acceptance #4):

* **fail-closed on undecidable.** A guard that could not run does not get to
  report ``LOG``. :class:`GuardOutcome` carries ``ran``; a guard that raised, or
  whose input could not be validated, is recorded with ``ran=False`` and
  :func:`aggregate` promotes the turn to ``BLOCK``. "We could not tell" is never
  "we allowed it".
* **no LOG on a path that never ran.** A guard sets ``ran=True`` only after its
  body completed its inspection, so ``LOG`` always means *inspected, nothing to
  report* — never *skipped*.

Findings are built by :func:`finding`, which has no parameter for a matched
value: a finding names the rule, its class, its action, its match count and the
offsets it matched at, so a blocked secret can never be echoed back into a
verdict, a log line or a console.

Verdict attachment follows the platform's existing correlation rule (the live
telemetry feed, issue #345): a verdict rides a turn only on an **exact
identifier match** and stands alone otherwise. :func:`attach` never guesses an
owner — no prefix, suffix or case-insensitive correlation is invented.


---knowledge---
module_id: guardrails.chat.verdict
system: guardrails
app: chat
solution_class: enterprise
patterns: [closed-vocabulary, tri-state-exit, fail-closed, consume-never-restate]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [GuardOutcome, decided, undecidable, aggregate, all_ran, finding, AttachedVerdicts, identifier_of]
invariants: "a guard that could not run is recorded with ran=False and reports undecidable; it never gets to report LOG"
gotchas: "the BLOCK/WARN/LOG vocabulary is consumed from policy.decision, so there is no second enum to drift against"
related: ["#507", "#26"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from policy.decision import DecisionLevel, strongest  # noqa: F401  (re-exported)

#: The guards a chat turn runs, in the order the turn runs them.
GUARDS = ("retrieval", "egress", "policy", "inbound")

#: Keys a verdict record may use to name the turn it belongs to.
TURN_ID_KEYS = ("turn_id", "call_id", "requestId", "request_id")


@dataclass(frozen=True)
class GuardOutcome:
    """What one guard decided about one turn.

    ``ran`` is the honesty bit: it is True only when the guard body completed
    its inspection, so a ``LOG`` decision can never describe a guard that was
    skipped or that failed open.
    """

    guard: str
    decision: DecisionLevel
    reason: str
    ran: bool = True
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def blocks(self) -> bool:
        """True when this guard's answer denies the turn."""
        return self.decision is DecisionLevel.BLOCK

    def to_dict(self) -> Dict[str, Any]:
        return {
            "guard": self.guard,
            "decision": self.decision.value,
            "reason": self.reason,
            "ran": self.ran,
            "evidence": dict(self.evidence),
        }


def decided(
    guard: str,
    decision: DecisionLevel,
    reason: str,
    *,
    evidence: Optional[Mapping[str, Any]] = None,
) -> GuardOutcome:
    """Record a guard that ran to completion and reached ``decision``."""
    if not guard:
        raise ValueError("a guard outcome must name its guard")
    return GuardOutcome(
        guard=guard,
        decision=decision,
        reason=reason,
        ran=True,
        evidence=dict(evidence or {}),
    )


def undecidable(
    guard: str,
    reason: str,
    *,
    evidence: Optional[Mapping[str, Any]] = None,
) -> GuardOutcome:
    """Record a guard that could **not** assess the turn (fail-closed).

    The decision is ``BLOCK`` (never ``LOG``) and ``ran`` is False, so a caller
    reading the record can tell *refused because it was bad* from *refused
    because we could not tell* — while both keep the turn from proceeding.
    """
    if not guard:
        raise ValueError("a guard outcome must name its guard")
    return GuardOutcome(
        guard=guard,
        decision=DecisionLevel.BLOCK,
        reason=reason,
        ran=False,
        evidence=dict(evidence or {}),
    )


def aggregate(outcomes: Sequence[GuardOutcome]) -> DecisionLevel:
    """The turn's verdict: the strongest decision any guard reached.

    Fail-closed in two ways: any guard that did not run forces ``BLOCK``
    regardless of what the others said, and an empty outcome set is refused —
    a verdict with no guard behind it is undecidable, not a pass.
    """
    if not outcomes:
        raise ValueError("refusing to aggregate an empty guard set (undecidable)")
    if any(not outcome.ran for outcome in outcomes):
        return DecisionLevel.BLOCK
    return strongest(outcome.decision for outcome in outcomes) or DecisionLevel.LOG


def all_ran(outcomes: Sequence[GuardOutcome]) -> bool:
    """True when every guard in ``outcomes`` completed its inspection."""
    return all(outcome.ran for outcome in outcomes)


def finding(
    *,
    guard: str,
    rule_id: str,
    action: str,
    klass: str = "",
    count: int = 0,
    spans: Sequence[Tuple[int, int]] = (),
) -> str:
    """Build a redaction-safe finding string.

    There is deliberately no parameter for the matched value: the finding names
    the rule, its class, its action, the number of matches and the offsets they
    were found at, and nothing that was matched.
    """
    parts = [f"{guard}:{rule_id}", f"action={action}"]
    if klass:
        parts.append(f"class={klass}")
    parts.append(f"matches={count}")
    if spans:
        parts.append("spans=" + ",".join(f"{start}-{end}" for start, end in spans))
    return " ".join(parts)


@dataclass(frozen=True)
class AttachedVerdicts:
    """Verdict records split by whether they ride a turn."""

    attached: Tuple[Mapping[str, Any], ...] = ()
    standalone: Tuple[Mapping[str, Any], ...] = ()

    def __len__(self) -> int:
        return len(self.attached) + len(self.standalone)


def identifier_of(record: Mapping[str, Any]) -> Optional[str]:
    """The identifier a verdict record carries, without coercion.

    A non-string, empty or missing identifier is ``None`` — a verdict that
    names nothing stands alone, it is never attached to a guess.
    """
    for key in TURN_ID_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def attach(records: Iterable[Mapping[str, Any]], *, turn_id: str) -> AttachedVerdicts:
    """Split verdict records into those riding ``turn_id`` and the rest.

    Attachment is an **exact** string comparison (the rule the live telemetry
    feed already applies, issue #345): a record whose identifier differs in any
    way — case, padding, a prefix, a suffix — is *not* attached to this turn.
    """
    if not isinstance(turn_id, str) or not turn_id:
        raise ValueError(f"a turn id must be a non-empty string, got {turn_id!r}")
    attached: list = []
    standalone: list = []
    for record in records:
        target = identifier_of(record)
        if target is not None and target == turn_id:
            attached.append(record)
        else:
            standalone.append(record)
    return AttachedVerdicts(tuple(attached), tuple(standalone))
