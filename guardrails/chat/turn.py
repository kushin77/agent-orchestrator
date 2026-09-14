"""guardrails.chat.turn — the composed chat-turn guard.

One place wires the three guards in the order a turn actually happens, so no
caller has to remember it:

```mermaid
flowchart LR
    R[retrieved fragments] --> G1{retrieval<br/>injection scan}
    G1 -- blocked --> Q[quarantine + BLOCK<br/>no prompt is built]
    G1 -- clean/suspicious --> P[grounding prefix<br/>untrusted delimiters]
    T[turn: prompt + prefix + tool args] --> G2{egress<br/>dlp scrub}
    P --> T
    G2 -- block rule --> A[abort the call<br/>dispatch_text empty]
    G2 -- redact --> S[dispatch redacted payload]
    G2 -- clean --> S
    S --> M[(model)]
    M --> G3{inbound<br/>output filter + citations}
    G3 -- unsupplied source / contradiction --> X[refuse: never lands in state]
    G3 -- uncited claim --> W[flagged]
    G3 -- grounded --> Y[accepted]
    C[policy controls registry] -. consulted by .-> G2
    C -. resolved by .-> G4{policy binding}
```

The turn's verdict is :func:`~guardrails.chat.verdict.aggregate` over every
guard's outcome — the strongest answer wins, and a guard that could not run
forces BLOCK. ``dispatch_text`` is non-empty **only** when nothing was refused,
and the record produced by :meth:`TurnOutcome.to_dict` carries rule ids, counts
and hashes instead of payload text, so it is safe to log or to emit on the
platform's telemetry feed.

Verdicts are correlated to a turn by the platform's existing rule (issue #345):
:meth:`ChatTurnGuard.attach_verdicts` attaches a verdict only on an exact
identifier match, and reports the rest as standalone rather than inventing an
owner.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .egress import ChatEgressGuard, EgressOutcome, OutboundTurn
from .envelope import GroundingEnvelope
from .inbound import InboundOutcome, InboundValidator
from .policy import ControlBinding
from .retrieval import RetrievalGuard, RetrievalOutcome
from .verdict import (
    AttachedVerdicts,
    DecisionLevel,
    GuardOutcome,
    aggregate,
    attach,
    decided,
    undecidable,
)


@dataclass(frozen=True)
class TurnOutcome:
    """Everything the guards decided about one turn."""

    turn_id: str
    decision: DecisionLevel
    aborted: bool
    dispatch_text: str
    retrieval: Optional[RetrievalOutcome]
    egress: Optional[EgressOutcome]
    policy: GuardOutcome
    inbound: Optional[InboundOutcome] = None

    def guards(self) -> Tuple[GuardOutcome, ...]:
        """Every guard outcome recorded for this turn, in run order."""
        outcomes: list = []
        if self.retrieval is not None:
            outcomes.append(self.retrieval.outcome)
        if self.egress is not None:
            outcomes.append(self.egress.outcome)
        outcomes.append(self.policy)
        if self.inbound is not None:
            outcomes.append(self.inbound.outcome)
        return tuple(outcomes)

    @property
    def allowed(self) -> bool:
        return not self.aborted and self.decision is not DecisionLevel.BLOCK

    @property
    def undecidable(self) -> bool:
        return any(not outcome.ran for outcome in self.guards())

    def to_dict(self) -> Dict[str, Any]:
        """A record safe to log: rule ids, counts, hashes — never input text."""
        return {
            "turn_id": self.turn_id,
            "decision": self.decision.value,
            "allowed": self.allowed,
            "aborted": self.aborted,
            "undecidable": self.undecidable,
            "dispatch_text": self.dispatch_text,
            "guards": [outcome.to_dict() for outcome in self.guards()],
            "retrieval": self.retrieval.to_dict() if self.retrieval is not None else None,
            "egress": self.egress.to_dict() if self.egress is not None else None,
            "policy": self.policy.to_dict(),
            "inbound": self.inbound.to_dict() if self.inbound is not None else None,
        }


class ChatTurnGuard:
    """Composes the retrieval, egress and inbound guards around one turn."""

    def __init__(
        self,
        *,
        retrieval: Optional[RetrievalGuard] = None,
        egress: Optional[ChatEgressGuard] = None,
        inbound: Optional[InboundValidator] = None,
        controls: Optional[ControlBinding] = None,
    ) -> None:
        self.controls = controls if controls is not None else ControlBinding.default()
        self.retrieval = retrieval if retrieval is not None else RetrievalGuard()
        self.egress = egress if egress is not None else ChatEgressGuard(controls=self.controls)
        self.inbound = inbound if inbound is not None else InboundValidator()

    # -- helpers -----------------------------------------------------------

    def _policy_outcome(self, binding: ControlBinding) -> GuardOutcome:
        """Resolve the controls registry the turn's guards consult."""
        if binding.undecidable:
            return undecidable("policy", f"controls registry unavailable: {binding.error}")
        missing = binding.bound_missing()
        if missing:
            return undecidable(
                "policy",
                "bound control(s) not registered in the controls registry: " + ", ".join(missing),
            )
        return decided(
            "policy",
            DecisionLevel.LOG,
            "controls registry resolved (bound controls default OFF unless enabled)",
            evidence=binding.describe(),
        )

    # -- the turn ----------------------------------------------------------

    def guard_turn(
        self,
        turn: OutboundTurn,
        *,
        envelope: Any = None,
        turn_id: str = "",
        controls: Optional[ControlBinding] = None,
    ) -> TurnOutcome:
        """Guard one turn from retrieved material to a dispatchable payload."""
        binding = controls if controls is not None else self.controls
        turn_id = turn_id or uuid.uuid4().hex

        retrieval = self.retrieval.guard(envelope)

        prefix = retrieval.prompt_prefix
        egress_turn = OutboundTurn(
            user_prompt=turn.user_prompt,
            grounding_prefix=prefix or turn.grounding_prefix,
            tool_arguments=turn.tool_arguments,
        )
        egress = self.egress.guard(egress_turn, controls=binding)
        policy = self._policy_outcome(binding)

        decision = aggregate((retrieval.outcome, egress.outcome, policy))
        aborted = decision is DecisionLevel.BLOCK
        return TurnOutcome(
            turn_id=turn_id,
            decision=decision,
            aborted=aborted,
            dispatch_text="" if aborted else egress.dispatch_text,
            retrieval=retrieval,
            egress=egress,
            policy=policy,
        )

    def validate_output(
        self,
        turn: TurnOutcome,
        output: Any,
        *,
        envelope: Any = None,
    ) -> InboundOutcome:
        """Re-validate the model's answer for a turn that was dispatched.

        An answer for an **aborted** turn is refused as undecidable: the guard
        has nothing honest to validate against, and "there should not be an
        answer here" is never a reason to accept one.
        """
        if turn.aborted:
            return InboundOutcome(
                outcome=undecidable(
                    "inbound",
                    "the turn was refused before dispatch; no model output can be validated",
                )
            )
        parsed: Optional[GroundingEnvelope] = None
        if isinstance(envelope, GroundingEnvelope):
            parsed = envelope
        elif isinstance(envelope, Mapping):
            try:
                parsed = GroundingEnvelope.from_mapping(envelope)
            except ValueError as exc:
                return InboundOutcome(
                    outcome=undecidable("inbound", f"grounding envelope is not readable: {exc}")
                )
        elif envelope is None:
            # No envelope was handed in: the grounding the turn actually admitted
            # is the only authority the answer may cite.
            parsed = self._admitted_envelope(turn)
        return self.inbound.validate(output, envelope=parsed)

    @staticmethod
    def _admitted_envelope(turn: TurnOutcome) -> Optional[GroundingEnvelope]:
        """The envelope the retrieval guard admitted for this turn, if any."""
        if turn.retrieval is None:
            return None
        fragments = [
            {"source_id": fragment.source_id, "text": _unwrap(fragment.text)}
            for fragment in turn.retrieval.fragments
            if fragment.admitted and fragment.text
        ]
        return GroundingEnvelope.from_fragments(fragments) if fragments else None

    def attach_verdicts(
        self, turn: TurnOutcome, records: Iterable[Mapping[str, Any]]
    ) -> AttachedVerdicts:
        """Attach verdict records to this turn — exact identifier match only."""
        return attach(records, turn_id=turn.turn_id)

    def record(self, turn: TurnOutcome, inbound: Optional[InboundOutcome] = None) -> Dict[str, Any]:
        """The full turn record, including the inbound verdict when it ran."""
        document = turn.to_dict()
        if inbound is not None:
            document["inbound"] = inbound.to_dict()
            document["inbound_decision"] = inbound.decision.value
        return document


def _unwrap(wrapped: str) -> str:
    """Reverse :func:`dlp.injection.wrap_untrusted` for prompt-prefix re-reading."""
    body = wrapped
    for token in ("<untrusted>\n", "\n</untrusted>", "<untrusted>", "</untrusted>"):
        body = body.replace(token, "")
    return body
