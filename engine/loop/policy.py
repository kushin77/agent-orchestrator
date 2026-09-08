"""engine/loop.policy — bounds, cost caps and escalation thresholds.

The loop is *bounded by construction*; all the knobs live here so the
contract is explicit and reviewable:

* :class:`LoopPolicy` — the actor-loop bounds: per-tier iteration budget,
  the whole-loop token budget (cost cap), the consecutive-failure budget,
  the parse/allowlist failure policy (``retry`` or ``cannot_assess``), the
  gmail 0.9/0.6 confidence triage thresholds, and whether tier escalation /
  human-in-the-loop are enabled.
* :class:`ComplexityPolicy` — the deterministic complexity banding that
  routes complex work to a deeper persona up front (hermes fast/standard/
  deep banding at 40/70).
* :class:`Profile` — a small, registry-compatible view of an agent profile:
  the loop consumes the frozen profile fields (``toolAllowlist``,
  ``defaultModelTier``) rather than redefining them.

Defaults follow the harvested sources: ``max_iterations=10`` (gmail's
<=10-iteration tool loop), ``confirm_confidence=0.9`` / ``human_confidence=
0.6`` (the gmail triage pattern the issue names), and the hermes complexity
band thresholds 40/70.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from .model import ModelTier, TIER_ORDER, next_tier

# Failure-policy vocabulary (issue #23 acceptance #1).
RETRY = "retry"
CANNOT_ASSESS = "cannot_assess"
_PARSE_POLICIES = (RETRY, CANNOT_ASSESS)


@dataclass(frozen=True)
class ComplexityPolicy:
    """Deterministic complexity banding (hermes ``complexity_scorer`` shape).

    A task whose complexity score is below ``fast_threshold`` routes to the
    fast/cheap tier; above-or-equal ``deep_threshold`` routes to the deepest
    persona; in between routes to the standard tier.
    """

    fast_threshold: float = 40.0  # hermes FAST_PATH_THRESHOLD
    deep_threshold: float = 70.0  # hermes DEEP_PATH_THRESHOLD
    weight_structure: float = 0.5  # task-structure component weight
    weight_breadth: float = 0.3  # scope/tool-breadth component weight
    weight_depth: float = 0.2  # reasoning-depth component weight

    def __post_init__(self) -> None:
        if not 0.0 <= self.fast_threshold <= self.deep_threshold <= 100.0:
            raise ValueError("complexity thresholds must satisfy 0 <= fast <= deep <= 100")
        weights = (self.weight_structure, self.weight_breadth, self.weight_depth)
        if any(w < 0 for w in weights) or abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("complexity weights must be non-negative and sum to 1")

    def route_tier(self, score: float) -> ModelTier:
        """Map a 0..100 score onto the starting tier (hermes path banding)."""
        if score >= self.deep_threshold:
            return ModelTier.TIER3
        if score < self.fast_threshold:
            return ModelTier.TIER1
        return ModelTier.TIER2


@dataclass(frozen=True)
class LoopPolicy:
    """The bounded-loop knobs (issue #23 acceptance #1/#2/#4)."""

    max_iterations: int = 10  # per-tier iteration budget (gmail <=10 loop)
    token_budget: int = 100_000  # whole-loop token cost cap
    max_consecutive_failures: int = 3  # repeated-failure escalation threshold
    on_allowlist_violation: str = RETRY  # a tool outside the allowlist
    on_parse_failure: str = RETRY  # an output that fails schema validation
    confirm_confidence: float = 0.9  # >= this => SUCCEEDED without review
    human_confidence: float = 0.6  # < this => hand to a human-in-the-loop
    tiered_escalation: bool = True  # may bump to a higher-tier persona
    human_in_loop: bool = True  # may hand off to a human
    max_tier: ModelTier = ModelTier.TIER3  # deepest tier this session may use

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        if self.token_budget < 1:
            raise ValueError("token_budget must be >= 1")
        if self.max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be >= 1")
        if self.on_allowlist_violation not in _PARSE_POLICIES:
            raise ValueError(f"on_allowlist_violation must be one of {_PARSE_POLICIES}")
        if self.on_parse_failure not in _PARSE_POLICIES:
            raise ValueError(f"on_parse_failure must be one of {_PARSE_POLICIES}")
        if not 0.0 <= self.human_confidence <= self.confirm_confidence <= 1.0:
            raise ValueError("confidence thresholds must satisfy 0 <= human <= confirm <= 1")
        if self.max_tier not in TIER_ORDER:
            raise ValueError(f"max_tier must be one of {[t.value for t in TIER_ORDER]}")

    def higher_tier(self, tier: ModelTier) -> Optional[ModelTier]:
        """The next tier this session may escalate to, honouring ``max_tier``."""
        if not self.tiered_escalation:
            return None
        if tier_rank(self.max_tier) <= tier_rank(tier):
            return None
        candidate = next_tier(tier)
        if candidate is None:
            return None
        if tier_rank(candidate) > tier_rank(self.max_tier):
            return None
        return candidate

    def hard_step_bound(self) -> int:
        """Absolute upper bound on actor steps for this policy.

        Every tier may spend ``max_iterations`` steps and there are at most
        ``len(TIER_ORDER)`` tiers, so a loop can never execute more than this
        many actor decisions — the guaranteed-termination invariant that the
        negative test asserts.
        """
        return self.max_iterations * (len(TIER_ORDER) + 1)


def tier_rank(tier: ModelTier) -> int:
    """0-based rank of a tier in :data:`TIER_ORDER`."""
    return TIER_ORDER.index(tier)


@dataclass(frozen=True)
class Profile:
    """Registry-compatible view of one agent profile for the loop.

    The loop consumes the frozen profile contract from the agent-registry
    lane (issue #9): ``tool_allowlist`` maps to the profile's
    ``toolAllowlist`` and ``default_tier`` to ``defaultModelTier``.  The loop
    never redefines those fields; it reads whatever view it is handed.
    """

    agent_id: str
    default_tier: ModelTier = ModelTier.TIER1
    tool_allowlist: Sequence[str] = ()
    final_schema: Optional[Mapping[str, Any]] = None  # declared output schema

    def allows(self, tool: str) -> bool:
        """Whether the profile's tool allowlist admits ``tool``.

        An empty allowlist is treated as *no restriction declared* (admit
        all) — mirroring the registry default where an absent allowlist does
        not forbid every tool.  When the allowlist is non-empty it is the
        sole authority (fail-closed on unknown tools).
        """
        if not self.tool_allowlist:
            return True
        return tool in set(self.tool_allowlist)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], agent_id: str = "") -> "Profile":
        """Build a profile view from a plain dict (registry-schema shaped).

        Accepts the registry field spellings ``toolAllowlist`` /
        ``defaultModelTier`` and the snake_case equivalents so a caller may
        hand over an ``agent-profile`` dict as-is.
        """
        allowlist = data.get("tool_allowlist", data.get("toolAllowlist", []))
        tier_value = data.get(
            "default_tier",
            data.get("defaultModelTier", ModelTier.TIER1.value),
        )
        tier = (
            tier_value
            if isinstance(tier_value, ModelTier)
            else ModelTier(str(tier_value))
        )
        final_schema = data.get("final_schema", data.get("finalSchema"))
        return cls(
            agent_id=data.get("agent_id", agent_id),
            default_tier=tier,
            tool_allowlist=tuple(allowlist or ()),
            final_schema=final_schema,
        )


def profile_allowlist(names: Iterable[str]) -> Sequence[str]:
    """Normalize an allowlist iterable to a deterministic tuple."""
    return tuple(sorted(set(names)))
