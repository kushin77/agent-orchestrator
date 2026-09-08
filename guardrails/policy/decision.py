"""Decision levels and structured evidence for the policy-gate engine.

The engine answers one question about an action — *what should happen to it* —
with an honest tri-state decision (AO-GR-19): ``BLOCK`` (deny), ``WARN``
(allow but flag) or ``LOG`` (allow and observe).  Every decision carries
structured evidence so a downstream audit record can answer "which policy,
which rule, why" without re-parsing prose, and a BLOCK always explains itself.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Iterable, Optional


class DecisionLevel(enum.Enum):
    """Tri-state decision severity; higher severity dominates lower.

    Ordering used by the engine and the controls registry is
    ``BLOCK > WARN > LOG`` (AO-GR-19 guard honesty: a consulted action always
    resolves to one of the three — nothing "passes silently").
    """

    BLOCK = "block"  # deny the action
    WARN = "warn"    # allow, but surface a warning
    LOG = "log"      # allow and observe

    def __str__(self) -> str:
        return self.value

    @property
    def severity(self) -> int:
        """BLOCK=3, WARN=2, LOG=1 — used for strongest-decision aggregation."""
        return _SEVERITY[self]

    @property
    def blocks(self) -> bool:
        """True when this level denies the action."""
        return self is DecisionLevel.BLOCK

    @classmethod
    def from_token(cls, token: Any) -> "DecisionLevel":
        """Map a serialized token (or level) to a level.

        Raises ``ValueError`` for anything that is not one of the three
        levels, so an unknown decision token can never be coerced into a
        permissive value.
        """
        if isinstance(token, cls):
            return token
        if isinstance(token, str):
            try:
                return cls(token.lower())
            except ValueError:
                pass
        raise ValueError(f"unknown decision level: {token!r}")


_SEVERITY = {
    DecisionLevel.LOG: 1,
    DecisionLevel.WARN: 2,
    DecisionLevel.BLOCK: 3,
}


def strongest(levels: Iterable[DecisionLevel]) -> Optional[DecisionLevel]:
    """Return the strongest decision among *levels* (None when empty)."""
    best: Optional[DecisionLevel] = None
    for level in levels:
        if best is None or level.severity > best.severity:
            best = level
    return best


@dataclass(frozen=True)
class RuleHit:
    """A single rule that fired for an action — one evidence leaf."""

    policy_id: str
    policy_version: int
    rule_id: str
    decision: DecisionLevel
    reason: str
    action: str
    subject: Optional[str] = None
    tenant: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "rule_id": self.rule_id,
            "decision": self.decision.value,
            "reason": self.reason,
            "action": self.action,
            "subject": self.subject,
            "tenant": self.tenant,
        }


@dataclass(frozen=True)
class DecisionResult:
    """Outcome of one :meth:`PolicyEngine.evaluate` call.

    ``decision`` is the tri-state answer; ``matched_rules`` and
    ``policies_consulted`` are the structured evidence; ``uncovered`` is True
    when no active policy governed the action; ``error`` is set when the
    decision was forced by a fail-closed evaluation error.
    """

    decision: DecisionLevel
    action: str
    subject: Optional[str] = None
    tenant: Optional[str] = None
    uncovered: bool = False
    error: Optional[str] = None
    matched_rules: tuple[RuleHit, ...] = ()
    policies_consulted: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return self.decision is DecisionLevel.BLOCK

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "action": self.action,
            "subject": self.subject,
            "tenant": self.tenant,
            "uncovered": self.uncovered,
            "error": self.error,
            "matched_rules": [hit.to_dict() for hit in self.matched_rules],
            "policies_consulted": list(self.policies_consulted),
        }
