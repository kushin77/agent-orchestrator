"""guardrails.chat.retrieval — retrieval-injection defense for grounded turns.

Issue #507 scope 3: retrieved material (tickets, KB documents, ledger excerpts)
is **untrusted input**. Before any of it can enter the model's grounding prefix
it is scanned by the **existing** prompt-injection detector
(``guardrails/dlp/injection.py``, issue #27, consumed read-only through
:class:`~dlp.injection.InjectionDetector`), so a poisoned source cannot escalate
into an instruction.

Per fragment, the detector's documented verdict maps to this lane's tri-state:

===================  =========  ==============================================
detector verdict     decision   effect
===================  =========  ==============================================
``blocked``          BLOCK      the fragment is **quarantined** and the turn is
                                refused: a poisoned source is an incident, not
                                a footnote
``suspicious``       WARN       admitted, but only inside the untrusted
                                delimiters
``benign``           LOG        admitted inside the untrusted delimiters
===================  =========  ==============================================

Every admitted fragment — benign or merely suspicious — is wrapped by
``dlp.injection.wrap_untrusted``, which neutralizes an embedded closing
delimiter; retrieved text is never concatenated raw into the prompt.

An envelope this lane cannot validate is *undecidable* (BLOCK, ``ran=False``),
never "there was no grounding".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from dlp.injection import InjectionDetector, wrap_untrusted

from .envelope import GroundingEnvelope, GroundingError
from .verdict import DecisionLevel, GuardOutcome, decided, finding, undecidable


@dataclass(frozen=True)
class FragmentAdmission:
    """What the retrieval gate decided about one retrieved fragment."""

    source_id: str
    decision: DecisionLevel
    admitted: bool
    signal_ids: tuple = ()
    text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "decision": self.decision.value,
            "admitted": self.admitted,
            "signals": list(self.signal_ids),
        }


@dataclass(frozen=True)
class RetrievalOutcome:
    """The retrieval-injection gate's answer for one grounded turn."""

    outcome: GuardOutcome
    fragments: tuple = ()
    prompt_prefix: str = ""
    findings: tuple = field(default_factory=tuple)

    @property
    def decision(self) -> DecisionLevel:
        return self.outcome.decision

    @property
    def admitted_ids(self) -> tuple:
        return tuple(fragment.source_id for fragment in self.fragments if fragment.admitted)

    @property
    def quarantined_ids(self) -> tuple:
        return tuple(fragment.source_id for fragment in self.fragments if not fragment.admitted)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "admitted": list(self.admitted_ids),
            "quarantined": list(self.quarantined_ids),
            "prompt_prefix": self.prompt_prefix,
            "fragments": [fragment.to_dict() for fragment in self.fragments],
            "findings": list(self.findings),
        }


class RetrievalGuard:
    """Scans retrieved fragments before they can enter a prompt.

    The defense is not control-gated: it is fail-closed for every turn that
    supplies retrieved material (a poisoned source is an incident whether or
    not a tenant has flipped a toggle).
    """

    def __init__(self, *, detector: Optional[InjectionDetector] = None) -> None:
        self.detector = detector if detector is not None else InjectionDetector()

    def _envelope(self, envelope: Any) -> Tuple[Optional[GroundingEnvelope], str]:
        if isinstance(envelope, GroundingEnvelope):
            return envelope, ""
        if isinstance(envelope, Mapping):
            try:
                return GroundingEnvelope.from_mapping(envelope), ""
            except GroundingError as exc:
                return None, f"grounding envelope is not readable: {exc}"
        return None, f"grounding envelope has an unsupported type {type(envelope).__name__}"

    def guard(self, envelope: Any) -> RetrievalOutcome:
        """Scan every supplied fragment; nothing poisoned reaches the prefix."""
        if envelope is None:
            return RetrievalOutcome(
                outcome=decided(
                    "retrieval",
                    DecisionLevel.LOG,
                    "no retrieved material was supplied (nothing to inspect)",
                    evidence={"fragments_considered": 0},
                )
            )
        parsed, problem = self._envelope(envelope)
        if parsed is None:
            return RetrievalOutcome(
                outcome=undecidable("retrieval", problem),
                fragments=(),
                prompt_prefix="",
            )

        fragments: list = []
        findings: list = []
        admitted_blocks: list = []
        for fragment in parsed:
            report = self.detector.analyze(fragment.text)
            signal_ids = tuple(report.reasons)
            if report.blocked:
                fragments.append(
                    FragmentAdmission(
                        source_id=fragment.source_id,
                        decision=DecisionLevel.BLOCK,
                        admitted=False,
                        signal_ids=signal_ids,
                    )
                )
                findings.extend(
                    finding(
                        guard="retrieval",
                        rule_id=hit.signal_id,
                        action="quarantine",
                        klass=hit.category,
                        count=1,
                        spans=((hit.start, hit.start + len(hit.matched)),),
                    )
                    for hit in report.hits
                )
                continue

            decision = (
                DecisionLevel.WARN if report.verdict == "suspicious" else DecisionLevel.LOG
            )
            wrapped = wrap_untrusted(fragment.text)
            fragments.append(
                FragmentAdmission(
                    source_id=fragment.source_id,
                    decision=decision,
                    admitted=True,
                    signal_ids=signal_ids,
                    text=wrapped,
                )
            )
            if decision is DecisionLevel.WARN:
                findings.extend(
                    finding(
                        guard="retrieval",
                        rule_id=hit.signal_id,
                        action="warn",
                        klass=hit.category,
                        count=1,
                        spans=((hit.start, hit.start + len(hit.matched)),),
                    )
                    for hit in report.hits
                )
            admitted_blocks.append(f"source_id={fragment.source_id}\n{wrapped}")

        quarantined = tuple(
            fragment.source_id for fragment in fragments if not fragment.admitted
        )
        signals = sorted(
            {
                signal_id
                for fragment in fragments
                if not fragment.admitted
                for signal_id in fragment.signal_ids
            }
        )
        prefix = "\n\n".join(admitted_blocks)
        decisions = [fragment.decision for fragment in fragments]
        if any(decision is DecisionLevel.BLOCK for decision in decisions):
            outcome = decided(
                "retrieval",
                DecisionLevel.BLOCK,
                "quarantined poisoned retrieved source(s): "
                + ", ".join(quarantined)
                + " (signals: "
                + ", ".join(signals)
                + ")",
                evidence={
                    "quarantined": list(quarantined),
                    "fragments_considered": len(fragments),
                },
            )
        elif any(decision is DecisionLevel.WARN for decision in decisions):
            outcome = decided(
                "retrieval",
                DecisionLevel.WARN,
                "admitted fragment(s) with suspicious content inside untrusted delimiters",
                evidence={
                    "admitted": list(fragment.source_id for fragment in fragments if fragment.admitted),
                    "fragments_considered": len(fragments),
                },
            )
        else:
            outcome = decided(
                "retrieval",
                DecisionLevel.LOG,
                f"{len(fragments)} retrieved fragment(s) scanned clean",
                evidence={
                    "admitted": list(fragment.source_id for fragment in fragments if fragment.admitted),
                    "fragments_considered": len(fragments),
                },
            )

        return RetrievalOutcome(
            outcome=outcome,
            fragments=tuple(fragments),
            prompt_prefix=prefix,
            findings=tuple(findings),
        )
