"""guardrails.chat.inbound — re-validating what a model hands back.

Issue #507 scope 2: a model's output is **untrusted** until it has been
re-validated, so nothing it returns lands in conversation state unvalidated.
Two independent checks run over one answer:

1. **Output filter** — the existing ``guardrails/dlp`` detector's output side
   (:meth:`~dlp.injection.InjectionDetector.filter_output`, issue #27, consumed
   read-only) quarantines an answer that echoes its own system prompt, restates
   internal instructions, or smuggles role lines into the conversation;
2. **Citation accounting** — the answer's claims are checked against the
   grounding envelope the turn actually supplied:

   * a claim that cites **no** supplied source is *flagged* (WARN) — it is
     stated without provenance and must not be presented as grounded;
   * a claim that cites a source that was **never supplied** is refused
     (BLOCK): the model invented an authority;
   * a claim whose quoted span is **not in** the source it cites is refused
     (BLOCK): the answer contradicts the material it attributes itself to.

A malformed answer (not a mapping, claims not a list, a claim without text) is
*undecidable* — BLOCK with ``ran=False``, never a quiet pass. Claim quotes are
compared with whitespace collapsed on both sides, so re-wrapping a retrieved
paragraph does not read as a contradiction while a changed word still does.


---knowledge---
module_id: guardrails.chat.inbound
system: guardrails
app: chat
solution_class: enterprise
patterns: [fail-closed, untrusted-input, named-refusal]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [InboundError, Claim, ModelOutput, ClaimReview, InboundOutcome, InboundValidator]
invariants: "a model's answer is untrusted until re-validated, and a claim citing no supplied source is flagged rather than accepted"
gotchas: ""
related: ["#507", "#27"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from dlp.injection import InjectionDetector

from .envelope import GroundingEnvelope
from .verdict import DecisionLevel, GuardOutcome, decided, finding, undecidable


class InboundError(ValueError):
    """The model output is malformed and cannot be validated."""


def _normalized(text: str) -> str:
    """Collapse runs of whitespace so re-wrapped text still compares equal."""
    return " ".join(text.split())


@dataclass(frozen=True)
class Claim:
    """One factual claim the answer makes about a supplied source."""

    text: str
    source_id: str = ""
    quote: str = ""

    @classmethod
    def from_mapping(cls, raw: Any, index: int) -> "Claim":
        if not isinstance(raw, Mapping):
            raise InboundError(f"claim #{index} is not a mapping")
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise InboundError(f"claim #{index} has no non-empty string text")
        source_id = raw.get("source_id", "")
        if not isinstance(source_id, str):
            raise InboundError(f"claim #{index} has a non-string source_id")
        quote = raw.get("quote", "")
        if not isinstance(quote, str):
            raise InboundError(f"claim #{index} has a non-string quote")
        return cls(text=text, source_id=source_id, quote=quote)


@dataclass(frozen=True)
class ModelOutput:
    """A model's answer plus the citations it claims, untrusted until validated."""

    text: str
    claims: Tuple[Claim, ...] = ()

    @classmethod
    def from_mapping(cls, document: Any) -> "ModelOutput":
        if not isinstance(document, Mapping):
            raise InboundError(
                f"model output must be a mapping, got {type(document).__name__}"
            )
        text = document.get("text")
        if not isinstance(text, str):
            raise InboundError("model output has no string text")
        raw_claims = document.get("claims", [])
        if not isinstance(raw_claims, Sequence) or isinstance(raw_claims, (str, bytes)):
            raise InboundError("model output claims must be a sequence")
        return cls(
            text=text,
            claims=tuple(Claim.from_mapping(raw, index) for index, raw in enumerate(raw_claims)),
        )


@dataclass(frozen=True)
class ClaimReview:
    """The verdict on one claim."""

    index: int
    decision: DecisionLevel
    reason: str
    source_id: str = ""
    cited: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim": self.index,
            "decision": self.decision.value,
            "reason": self.reason,
            "source_id": self.source_id,
            "cited": self.cited,
        }


@dataclass(frozen=True)
class InboundOutcome:
    """The inbound gate's answer for one model response."""

    outcome: GuardOutcome
    claims: tuple = ()
    findings: tuple = field(default_factory=tuple)

    @property
    def decision(self) -> DecisionLevel:
        return self.outcome.decision

    @property
    def accepted(self) -> bool:
        return self.outcome.decision is not DecisionLevel.BLOCK

    @property
    def flagged_ids(self) -> tuple:
        return tuple(review.index for review in self.claims if review.decision is DecisionLevel.WARN)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "flagged_claims": list(self.flagged_ids),
            "claims": [review.to_dict() for review in self.claims],
            "findings": list(self.findings),
        }


class InboundValidator:
    """Re-validates model output before it can land in conversation state."""

    def __init__(self, *, detector: Optional[InjectionDetector] = None) -> None:
        self.detector = detector if detector is not None else InjectionDetector()

    def validate(self, output: Any, *, envelope: Optional[GroundingEnvelope] = None) -> InboundOutcome:
        """Validate one answer against the grounding the turn actually supplied."""
        try:
            parsed = output if isinstance(output, ModelOutput) else ModelOutput.from_mapping(output)
        except InboundError as exc:
            return InboundOutcome(outcome=undecidable("inbound", f"model output is not readable: {exc}"))

        report = self.detector.filter_output(parsed.text)
        if report.blocked:
            signal_ids = tuple(hit.signal_id for hit in report.hits)
            findings = tuple(
                finding(
                    guard="inbound",
                    rule_id=hit.signal_id,
                    action="quarantine",
                    klass=hit.category,
                    count=1,
                    spans=((hit.start, hit.start + len(hit.matched)),),
                )
                for hit in report.hits
            )
            return InboundOutcome(
                outcome=decided(
                    "inbound",
                    DecisionLevel.BLOCK,
                    "model output quarantined on signal(s) " + ", ".join(signal_ids),
                    evidence={"signals": list(signal_ids)},
                ),
                claims=(),
                findings=findings,
            )

        supplied = tuple(envelope.ids()) if envelope is not None else ()
        reviews: list = []
        findings: list = []
        for index, claim in enumerate(parsed.claims):
            if not claim.source_id:
                reviews.append(
                    ClaimReview(
                        index=index,
                        decision=DecisionLevel.WARN,
                        reason="claim cites no supplied source",
                    )
                )
                findings.append(
                    finding(guard="inbound", rule_id="citation.missing", action="flag", count=1)
                )
                continue

            fragment = envelope.get(claim.source_id) if envelope is not None else None
            if fragment is None:
                reviews.append(
                    ClaimReview(
                        index=index,
                        decision=DecisionLevel.BLOCK,
                        reason=(
                            "claim cites a source that was never supplied "
                            f"({len(supplied)} source(s) were supplied)"
                        ),
                        source_id=claim.source_id,
                        cited=True,
                    )
                )
                findings.append(
                    finding(
                        guard="inbound",
                        rule_id="citation.unsupplied",
                        action="block",
                        klass="provenance",
                        count=1,
                    )
                )
                continue

            if claim.quote and _normalized(claim.quote) not in _normalized(fragment.text):
                reviews.append(
                    ClaimReview(
                        index=index,
                        decision=DecisionLevel.BLOCK,
                        reason="claim contradicts the supplied source: quoted span is not in it",
                        source_id=claim.source_id,
                        cited=True,
                    )
                )
                findings.append(
                    finding(
                        guard="inbound",
                        rule_id="citation.contradiction",
                        action="block",
                        klass="provenance",
                        count=1,
                    )
                )
                continue

            reviews.append(
                ClaimReview(
                    index=index,
                    decision=DecisionLevel.LOG,
                    reason="claim is quoted from the supplied source",
                    source_id=claim.source_id,
                    cited=True,
                )
            )

        blocked = [review for review in reviews if review.decision is DecisionLevel.BLOCK]
        flagged = [review for review in reviews if review.decision is DecisionLevel.WARN]
        if blocked:
            reason = "refused claim(s) " + ", ".join(str(review.index) for review in blocked)
            outcome = decided(
                "inbound",
                DecisionLevel.BLOCK,
                reason,
                evidence={
                    "claims": len(reviews),
                    "refused": [review.index for review in blocked],
                },
            )
        elif flagged:
            outcome = decided(
                "inbound",
                DecisionLevel.WARN,
                "flagged uncited claim(s) " + ", ".join(str(review.index) for review in flagged),
                evidence={
                    "claims": len(reviews),
                    "flagged": [review.index for review in flagged],
                    "supplied_ids": list(supplied),
                },
            )
        else:
            outcome = decided(
                "inbound",
                DecisionLevel.LOG,
                f"{len(reviews)} claim(s) re-validated against the supplied grounding",
                evidence={"claims": len(reviews), "supplied_ids": list(supplied)},
            )

        return InboundOutcome(outcome=outcome, claims=tuple(reviews), findings=tuple(findings))
