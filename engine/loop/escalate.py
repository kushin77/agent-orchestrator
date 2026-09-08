"""engine/loop.escalate — complexity scoring and the escalation engine.

Issue #23 acceptance #2: *escalation — complexity/uncertainty -> a
higher-tier persona (hermes escalation pattern) or a human-in-the-loop
above the confidence threshold (gmail 0.9/0.6 triage pattern).*  Two pieces
live here:

* :class:`ComplexityScorer` — a deterministic, offline 0..100 complexity
  score for a task input (hermes ``complexity_scorer`` shape: component
  scores weighted into one score, then banded by :class:`ComplexityPolicy`
  into a starting tier).  Used to route genuinely complex work to a deeper
  persona *before* the loop burns cheap-tier iterations.
* :class:`EscalationEngine` — the single decision point for *mid-loop*
  escalation.  Escalation is **monotonic upward** through the tier order
  (never down, never a cycle), then to a human, then to ``none`` — which is
  precisely why a loop is guaranteed to terminate: a session can escalate at
  most ``len(TIER_ORDER)-1`` times before it must settle or hand off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .model import EscalationTargetKind, EscalationTrigger, ModelTier
from .policy import ComplexityPolicy, LoopPolicy

_DEPTH_MARKERS = (
    "compare", "contrast", "why", "explain", "plan", "design", "strategy",
    "trade-off", "evaluate", "analyse", "analyze", "root cause", "impact",
    "architecture", "decompose",
)
_BREADTH_MARKERS = (
    "multiple", "several", "both", "all", "each", "different", "across",
    "repository", "services", "files", "components", "integrate",
)
_WORD_SPLIT = re.compile(r"\W+")


@dataclass(frozen=True)
class ComplexityScore:
    """Deterministic complexity verdict for one task input."""

    score: float  # 0..100
    band: str  # "fast" | "standard" | "deep"
    route_tier: str  # ModelTier.value the task routes to
    components: Mapping[str, float] = field(default_factory=dict)

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "score": self.score,
            "band": self.band,
            "route_tier": self.route_tier,
            "components": dict(self.components),
        }


def _collect_text(task_input: Mapping[str, Any]) -> str:
    """Join every textual fragment of a task input (deterministic order)."""
    parts: list[str] = []
    for key in sorted(task_input):
        value = task_input[key]
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
    return " ".join(parts).lower()


def _count_lists(task_input: Mapping[str, Any]) -> int:
    return sum(1 for value in task_input.values() if isinstance(value, (list, tuple)))


class ComplexityScorer:
    """Deterministic heuristic complexity scorer (offline, no ML/history).

    Three deterministic components, each scored 0..100 (mirroring the hermes
    component-score idea, stripped of the ML/historical inputs that cannot
    run offline):

    * *structure* — the explicit requirements/constraints a task states
      (sub-objectives, listed inputs, "must/should" clauses);
    * *breadth* — the scope signals (breadth markers + listed inputs);
    * *depth* — the reasoning signals (depth markers + prompt length).

    The policy-weighted sum is clamped to 0..100 and banded by
    :class:`ComplexityPolicy` into a starting tier.
    """

    def __init__(self, policy: ComplexityPolicy = ComplexityPolicy()) -> None:
        self.policy = policy

    def score(self, task_input: Mapping[str, Any]) -> ComplexityScore:
        text = _collect_text(task_input)
        words = [w for w in _WORD_SPLIT.split(text) if w]

        # structure (0..100): listed inputs + explicit constraint clauses.
        listed = _count_lists(task_input)
        constraint_hits = len(re.findall(r"\b(must|should|required|need)\b", text))
        structure = min(100.0, listed * 15.0 + constraint_hits * 5.0)

        # breadth (0..100): scope markers + listed inputs.
        breadth = min(100.0, sum(1 for m in _BREADTH_MARKERS if m in text) * 8.0 + listed * 4.0)

        # depth (0..100): reasoning markers + prompt-length tail.
        depth = min(100.0, sum(1 for m in _DEPTH_MARKERS if m in text) * 7.0
                    + min(20.0, len(words) / 20.0))

        raw = (
            structure * self.policy.weight_structure
            + breadth * self.policy.weight_breadth
            + depth * self.policy.weight_depth
        )
        score = round(max(0.0, min(100.0, raw)), 1)
        tier = self.policy.route_tier(score)
        band = {
            ModelTier.TIER1: "fast",
            ModelTier.TIER2: "standard",
            ModelTier.TIER3: "deep",
        }[tier]
        return ComplexityScore(
            score=score,
            band=band,
            route_tier=tier.value,
            components={
                "structure": round(structure, 1),
                "breadth": round(breadth, 1),
                "depth": round(depth, 1),
            },
        )

    def route(self, task_input: Mapping[str, Any]) -> ModelTier:
        """The starting tier for ``task_input`` under this policy."""
        return ModelTier(self.score(task_input).route_tier)


@dataclass(frozen=True)
class EscalationDecision:
    """What the escalation engine decided for one trigger."""

    kind: str  # "tier" | "human" | "none"
    trigger: str  # EscalationTrigger.value
    from_tier: str
    to_tier: Optional[str] = None  # set when kind == "tier"
    reason: str = ""

    @property
    def target_kind(self) -> str:
        if self.kind == "tier":
            return EscalationTargetKind.TIER.value
        if self.kind == "human":
            return EscalationTargetKind.HUMAN.value
        return ""  # "none" escalations are never recorded as events


class EscalationEngine:
    """Mid-loop escalation decision maker (bounded, monotonic, deterministic).

    ``next_target`` answers one question: given the current tier and the
    trigger, where does this loop go next?  The answer is fully determined by
    the :class:`LoopPolicy`:

    1. a higher tier is available under the policy -> ``tier`` (monotonic up);
    2. otherwise a human is in the loop -> ``human`` (terminal hand-off);
    3. otherwise -> ``none`` (the caller must end the loop with an honest
       terminal outcome — never a silent pass and never an infinite loop).
    """

    def __init__(self, policy: LoopPolicy) -> None:
        self.policy = policy

    def next_target(
        self,
        tier: ModelTier,
        trigger: EscalationTrigger,
        *,
        reason: str = "",
        allow_tier: bool = True,
    ) -> EscalationDecision:
        higher = self.policy.higher_tier(tier) if allow_tier else None
        if higher is not None:
            return EscalationDecision(
                kind="tier",
                trigger=trigger.value,
                from_tier=tier.value,
                to_tier=higher.value,
                reason=reason,
            )
        if self.policy.human_in_loop:
            return EscalationDecision(
                kind="human",
                trigger=trigger.value,
                from_tier=tier.value,
                reason=reason,
            )
        return EscalationDecision(
            kind="none", trigger=trigger.value, from_tier=tier.value, reason=reason
        )

    def human_target(self, tier: ModelTier, trigger: EscalationTrigger, *, reason: str = "") -> EscalationDecision:
        """Escalate straight to a human (used when tokens are exhausted)."""
        if self.policy.human_in_loop:
            return EscalationDecision(
                kind="human", trigger=trigger.value, from_tier=tier.value, reason=reason
            )
        return EscalationDecision(
            kind="none", trigger=trigger.value, from_tier=tier.value, reason=reason
        )
