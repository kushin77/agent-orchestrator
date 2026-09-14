"""guardrails.chat.egress — the outbound DLP gate for a chat turn.

Issue #507 scope 1: **every** outbound turn — the user prompt, the assembled
grounding prefix and the tool-call arguments — passes the **existing** DLP
scrub gate (``guardrails/dlp``, issue #27, consumed read-only through
:class:`~dlp.engine.ScrubEngine`) before anything leaves for a model.

Outcome model, fail-closed:

* a ``block``-class scrub match **aborts the call**: the outcome is ``BLOCK``,
  ``aborted`` is True and ``dispatch_text`` is empty — the payload is never
  assembled, let alone returned for dispatch;
* a ``redact``-class match lets the call proceed with the **redacted**
  component text and records a ``WARN``; the finding names the rule, its class,
  the match count and the offsets, and never the matched value
  (``verdict.finding`` has no parameter for it);
* a scrub that cannot run (the rule catalog is missing, or the engine raises)
  is *undecidable* — ``ran=False`` and BLOCK, never LOG;
* a turn whose every outbound component is empty is refused as well: nothing
  was inspected, so there is nothing this gate could honestly clear.

Controls (``guardrails/policy``, default OFF — see :mod:`guardrails.chat.policy`):

* ``data-egress-guard`` ON escalates a redaction-class finding from ``WARN``
  (send redacted) to ``BLOCK`` (send nothing);
* ``tool-use-guard`` ON adds a prompt-injection analysis of the tool-call
  arguments before dispatch; a ``blocked`` analysis aborts the turn.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from dlp.catalog import CatalogError
from dlp.engine import ScrubEngine
from dlp.injection import InjectionDetector

from .policy import ControlBinding, ControlBindingError
from .verdict import (
    DecisionLevel,
    GuardOutcome,
    decided,
    finding,
    undecidable,
)

#: The control this guard consults to escalate redaction to a refusal.
CONTROL_DATA_EGRESS = "data-egress-guard"
#: The control this guard consults to add injection analysis of tool arguments.
CONTROL_TOOL_USE = "tool-use-guard"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_tool_arguments(arguments: Any) -> Tuple[Tuple[str, str], ...]:
    """Normalize tool-call arguments into ``((name, text), ...)``.

    Accepted shapes: a mapping of argument name to text, a sequence of
    ``(name, text)`` pairs, or a sequence of bare strings (named ``arg0``,
    ``arg1``, ...). Anything else is refused rather than guessed at.
    """
    if arguments is None:
        return ()
    if isinstance(arguments, Mapping):
        pairs = []
        for name, text in arguments.items():
            if not isinstance(name, str) or not isinstance(text, str):
                raise TypeError(f"tool argument {name!r} must map a name to a string")
            pairs.append((name, text))
        return tuple(pairs)
    if isinstance(arguments, (str, bytes)):
        raise TypeError("tool_arguments must be a mapping or a sequence, not a string")
    pairs = []
    for index, item in enumerate(arguments):
        if isinstance(item, str):
            pairs.append((f"arg{index}", item))
            continue
        if isinstance(item, (tuple, list)) and len(item) == 2:
            name, text = item
            if not isinstance(name, str) or not isinstance(text, str):
                raise TypeError(f"tool argument #{index} must be a (name, text) pair of strings")
            pairs.append((name, text))
            continue
        raise TypeError(f"tool argument #{index} is not a string or a (name, text) pair")
    return tuple(pairs)


@dataclass(frozen=True)
class OutboundTurn:
    """Everything one chat turn would send to a model."""

    user_prompt: str
    grounding_prefix: str = ""
    tool_arguments: Tuple[Tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.user_prompt, str):
            raise TypeError("user_prompt must be a string")
        if not isinstance(self.grounding_prefix, str):
            raise TypeError("grounding_prefix must be a string")
        object.__setattr__(self, "tool_arguments", _normalize_tool_arguments(self.tool_arguments))

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> "OutboundTurn":
        """Build a turn from a mapping (the shape the CLI accepts)."""
        if not isinstance(document, Mapping):
            raise TypeError("an outbound turn must be a mapping")
        return cls(
            user_prompt=document.get("user_prompt", ""),
            grounding_prefix=document.get("grounding_prefix", ""),
            tool_arguments=document.get("tool_arguments") or (),
        )


@dataclass(frozen=True)
class ComponentDecision:
    """What the scrub gate decided about one outbound component."""

    name: str
    verdict: str  # "sent" | "blocked"
    rule_ids: tuple
    redacted: bool
    spans: tuple
    sha256: str

    @property
    def blocked(self) -> bool:
        return self.verdict == "blocked"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component": self.name,
            "verdict": self.verdict,
            "rule_ids": list(self.rule_ids),
            "redacted": self.redacted,
            "spans": [list(span) for span in self.spans],
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class EgressOutcome:
    """The outbound gate's answer for one turn."""

    outcome: GuardOutcome
    aborted: bool
    dispatch_text: str
    components: tuple = ()
    findings: tuple = field(default_factory=tuple)

    @property
    def decision(self) -> DecisionLevel:
        return self.outcome.decision

    @property
    def allowed(self) -> bool:
        return not self.aborted and self.outcome.decision is not DecisionLevel.BLOCK

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aborted": self.aborted,
            "allowed": self.allowed,
            "dispatch_text": self.dispatch_text,
            "components": [component.to_dict() for component in self.components],
            "findings": list(self.findings),
        }


class ChatEgressGuard:
    """The outbound DLP gate of the chat turn."""

    def __init__(
        self,
        *,
        scrubber: Optional[ScrubEngine] = None,
        detector: Optional[InjectionDetector] = None,
        controls: Optional[ControlBinding] = None,
    ) -> None:
        self.load_error = ""
        if scrubber is not None:
            self.scrubber: Optional[ScrubEngine] = scrubber
        else:
            try:
                self.scrubber = ScrubEngine()
            except CatalogError as exc:  # the catalog is the policy; no catalog, no clearing
                self.scrubber = None
                self.load_error = f"dlp scrub catalog unavailable: {exc}"
        self.detector = detector if detector is not None else InjectionDetector()
        self.controls = controls if controls is not None else ControlBinding.default()

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _components(turn: OutboundTurn) -> list:
        """Every outbound component that carries text, in dispatch order."""
        candidates = [
            ("grounding_prefix", turn.grounding_prefix),
            ("user_prompt", turn.user_prompt),
        ]
        candidates.extend(
            (f"tool_arguments:{name}", text) for name, text in turn.tool_arguments
        )
        return [(name, text) for name, text in candidates if text]

    @staticmethod
    def _assemble(parts: Sequence[Tuple[str, str]]) -> str:
        """Assemble the dispatch payload from the (redacted) components."""
        blocks = []
        for name, text in parts:
            blocks.append(f"{name}:\n{text}")
        return "\n\n".join(blocks)

    def _undecidable(self, reason: str) -> EgressOutcome:
        return EgressOutcome(
            outcome=undecidable("egress", reason),
            aborted=True,
            dispatch_text="",
        )

    # -- the gate ----------------------------------------------------------

    def guard(
        self, turn: OutboundTurn, *, controls: Optional[ControlBinding] = None
    ) -> EgressOutcome:
        """Run one outbound turn through the scrub gate.

        Returns an :class:`EgressOutcome`; ``aborted`` True means the payload
        must not be dispatched and ``dispatch_text`` is empty.
        """
        binding = controls if controls is not None else self.controls
        try:
            escalate_redaction = binding.is_active(CONTROL_DATA_EGRESS)
            analyse_tools = binding.is_active(CONTROL_TOOL_USE)
        except ControlBindingError as exc:
            return self._undecidable(f"control binding unresolved: {exc}")

        if self.scrubber is None:
            return self._undecidable(self.load_error or "dlp scrub engine unavailable")

        components = self._components(turn)
        if not components:
            return self._undecidable("no outbound component carried text (nothing was inspected)")

        decisions: list = []
        per_rule: Dict[str, Dict[str, Any]] = {}
        blocked_rule_ids: list = []
        redacted_parts: list = []

        for name, text in components:
            try:
                result = self.scrubber.scrub(text)
            except CatalogError as exc:
                return self._undecidable(f"dlp scrub failed: {exc}")

            spans = tuple((match.start, match.end) for match in result.matches)
            for match in result.matches:
                entry = per_rule.setdefault(
                    match.rule_id,
                    {"action": match.action, "class": match.rule_class, "spans": []},
                )
                entry["spans"].append((match.start, match.end))
            rule_ids = tuple(dict.fromkeys(match.rule_id for match in result.matches))

            if result.blocked:
                blocked_rule_ids.extend(result.blocked_by)
                decisions.append(
                    ComponentDecision(
                        name=name,
                        verdict="blocked",
                        rule_ids=rule_ids,
                        redacted=False,
                        spans=spans,
                        sha256="",
                    )
                )
                continue

            redacts = [match for match in result.matches if match.action != "block"]
            decisions.append(
                ComponentDecision(
                    name=name,
                    verdict="sent",
                    rule_ids=rule_ids,
                    redacted=bool(redacts),
                    spans=spans,
                    sha256=_sha256(result.text),
                )
            )
            redacted_parts.append((name, result.text))

        findings = tuple(
            finding(
                guard="egress",
                rule_id=rule_id,
                action=entry["action"],
                klass=entry["class"],
                count=len(entry["spans"]),
                spans=tuple(entry["spans"]),
            )
            for rule_id, entry in per_rule.items()
        )
        dispatched = self._assemble(redacted_parts)

        if blocked_rule_ids:
            reason = "DLP block rule(s) matched: " + ", ".join(sorted(set(blocked_rule_ids)))
            return EgressOutcome(
                outcome=decided(
                    "egress",
                    DecisionLevel.BLOCK,
                    reason,
                    evidence={
                        "rules": sorted(set(blocked_rule_ids)),
                        "components": [component.name for component in decisions],
                        "dispatch_sha256": "",
                    },
                ),
                aborted=True,
                dispatch_text="",
                components=tuple(decisions),
                findings=findings,
            )

        if analyse_tools and turn.tool_arguments:
            for argument_name, text in turn.tool_arguments:
                if not text:
                    continue
                report = self.detector.analyze(text)
                if report.blocked:
                    signal_ids = tuple(hit.signal_id for hit in report.hits)
                    tool_findings = tuple(
                        finding(
                            guard="egress",
                            rule_id=hit.signal_id,
                            action="block",
                            klass=hit.category,
                            count=1,
                            spans=((hit.start, hit.start + len(hit.matched)),),
                        )
                        for hit in report.hits
                    )
                    return EgressOutcome(
                        outcome=decided(
                            "egress",
                            DecisionLevel.BLOCK,
                            f"{CONTROL_TOOL_USE} is ON: tool argument {argument_name!r} carries "
                            f"injection signal(s) {', '.join(signal_ids)}",
                            evidence={
                                "control": CONTROL_TOOL_USE,
                                "argument": argument_name,
                                "signals": list(signal_ids),
                            },
                        ),
                        aborted=True,
                        dispatch_text="",
                        components=tuple(decisions),
                        findings=findings + tool_findings,
                    )

        if len(redacted_parts) != len(decisions):
            # Every component reached this point through the "sent" branch, so a
            # shortfall means the gate lost a component: undecidable, not a pass.
            return self._undecidable("a scrubbed component produced no dispatchable text")

        redacted_rule_ids = sorted(
            rule_id for rule_id, entry in per_rule.items() if entry["action"] != "block"
        )
        if escalate_redaction and redacted_rule_ids:
            control_finding = finding(
                guard="egress",
                rule_id=CONTROL_DATA_EGRESS,
                action="block",
                klass="control",
                count=len(redacted_rule_ids),
            )
            return EgressOutcome(
                outcome=decided(
                    "egress",
                    DecisionLevel.BLOCK,
                    f"{CONTROL_DATA_EGRESS} is ON: refusing redaction-class finding(s) "
                    + ", ".join(redacted_rule_ids),
                    evidence={"control": CONTROL_DATA_EGRESS, "rules": redacted_rule_ids},
                ),
                aborted=True,
                dispatch_text="",
                components=tuple(decisions),
                findings=findings + (control_finding,),
            )

        if redacted_rule_ids:
            return EgressOutcome(
                outcome=decided(
                    "egress",
                    DecisionLevel.WARN,
                    "redacted before dispatch: " + ", ".join(redacted_rule_ids),
                    evidence={
                        "rules": redacted_rule_ids,
                        "dispatch_sha256": _sha256(dispatched),
                    },
                ),
                aborted=False,
                dispatch_text=dispatched,
                components=tuple(decisions),
                findings=findings,
            )

        return EgressOutcome(
            outcome=decided(
                "egress",
                DecisionLevel.LOG,
                f"{len(decisions)} outbound component(s) scrubbed clean",
                evidence={
                    "components": [component.name for component in decisions],
                    "dispatch_sha256": _sha256(dispatched),
                },
            ),
            aborted=False,
            dispatch_text=dispatched,
            components=tuple(decisions),
            findings=findings,
        )
