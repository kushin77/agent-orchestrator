"""telemetry/chat — the turn cost/latency/budget read model (issue #506).

---knowledge---
module_id: telemetry.chat.readmodel
system: telemetry
app: chat
solution_class: enterprise
patterns: [read-model, carried-not-computed]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [ChatFinOpsReadModel, TurnCostView, CostRollup]
invariants: "every figure is carried from a TurnAttribution, so the UX never computes a number and the visible arithmetic is the billed arithmetic"
gotchas: ""
related: ["#506", "#1510"]
do_not_duplicate: null
---knowledge---


The small projection the chat UX lane renders.  It exists so the UX never
computes a number: every figure here is *carried* from a ``TurnAttribution``
(which took it from the rate card, the record, or the enforcer), so the surface
the user sees is the same arithmetic the tenant is charged for.  This lane
owns the numbers; the UX lane owns their presentation.

Three views, one source:

- ``turn(turn_id)`` — one turn: tokens, cost (and what it would have cost
  cold), cache share, latency, and the budget verdict that let it through (or
  refused it).
- ``conversation(id)`` / ``tenant(id)`` / ``agent(id)`` — rollups over the
  turns' own figures (a sum of reported costs, never a re-price).
- ``ticket(id)`` — the ADR-0014 join: the cost of the turns a ticket spawned.

A refused turn appears in the rollups with ``refused_turns`` counted and no
cost: the read model reports **what the guard prevented** alongside what was
spent, and never books a charge for a turn that was never dispatched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .model import TurnAttribution


@dataclass(frozen=True)
class TurnCostView:
    """One turn's cost, latency and budget state — as charged, not re-derived."""

    turn_id: str
    conversation_id: str
    tenant_id: str
    agent_id: str
    ts: str
    outcome: str
    tier: Optional[str]
    provider: Optional[str]
    model: Optional[str]
    ticket_id: Optional[str]
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: Optional[float]
    cost_source: Optional[str]
    cold_equivalent_cost_usd: Optional[float]
    billable: bool
    metered: bool
    unmetered_reason: Optional[str]
    cache_hit_share: float
    cached_tokens: int
    prompt_tokens: int
    billable_input_tokens: int
    budget_decision: str
    budget_allowed: bool
    budget_known: bool = False
    budget_code: Optional[str] = None
    budget_outcome: Optional[str] = None
    budget_projected_usd: Optional[float] = None
    budget_soft_warning: bool = False
    budget_hard_stop: bool = False
    client_tier: Optional[str] = None
    tier_claim_honoured: bool = False
    usage_record_id: Optional[str] = None
    ledger_seq: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turnId": self.turn_id,
            "conversationId": self.conversation_id,
            "tenantId": self.tenant_id,
            "agentId": self.agent_id,
            "ticketId": self.ticket_id,
            "ts": self.ts,
            "outcome": self.outcome,
            "tier": self.tier,
            "provider": self.provider,
            "model": self.model,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "tokens": self.input_tokens + self.output_tokens,
            "latencyMs": self.latency_ms,
            "costUsd": self.cost_usd,
            "costSource": self.cost_source,
            "coldEquivalentCostUsd": self.cold_equivalent_cost_usd,
            "billable": self.billable,
            "metered": self.metered,
            "unmeteredReason": self.unmetered_reason,
            "cacheHitShare": self.cache_hit_share,
            "cachedTokens": self.cached_tokens,
            "promptTokens": self.prompt_tokens,
            "billableInputTokens": self.billable_input_tokens,
            "budgetDecision": self.budget_decision,
            "budgetAllowed": self.budget_allowed,
            "budgetKnown": self.budget_known,
            "budgetCode": self.budget_code,
            "budgetOutcome": self.budget_outcome,
            "budgetProjectedUsd": self.budget_projected_usd,
            "budgetSoftWarning": self.budget_soft_warning,
            "budgetHardStop": self.budget_hard_stop,
            "clientTier": self.client_tier,
            "tierClaimHonoured": self.tier_claim_honoured,
            "usageRecordId": self.usage_record_id,
            "ledgerSeq": self.ledger_seq,
        }


@dataclass(frozen=True)
class CostRollup:
    """A rollup over a set of turns' own reported figures.

    ``cost_usd`` sums the charged turns; ``refused_turns`` counts what the
    guard prevented (those turns contribute no cost).  ``cache_hit_share`` is
    the token-weighted prefix reuse across the set.
    """

    key: str
    turns: int = 0
    allowed_turns: int = 0
    refused_turns: int = 0
    billable_turns: int = 0
    unmetered_turns: int = 0
    cache_hit_turns: int = 0
    cost_usd: float = 0.0
    cold_equivalent_cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: float = 0.0

    @property
    def cache_savings_usd(self) -> float:
        """Charged-vs-cold difference across the rollup's charged turns."""
        return round(self.cold_equivalent_cost_usd - self.cost_usd, 10)

    @property
    def cache_hit_share(self) -> float:
        if self.prompt_tokens <= 0:
            return 0.0
        return round(self.cached_tokens / self.prompt_tokens, 6)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "turns": self.turns,
            "allowedTurns": self.allowed_turns,
            "refusedTurns": self.refused_turns,
            "billableTurns": self.billable_turns,
            "unmeteredTurns": self.unmetered_turns,
            "cacheHitTurns": self.cache_hit_turns,
            "costUsd": round(self.cost_usd, 10),
            "coldEquivalentCostUsd": round(self.cold_equivalent_cost_usd, 10),
            "cacheSavingsUsd": self.cache_savings_usd,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "promptTokens": self.prompt_tokens,
            "cachedTokens": self.cached_tokens,
            "cacheHitShare": self.cache_hit_share,
            "latencyMs": round(self.latency_ms, 3),
        }


class ChatFinOpsReadModel:
    """Holds the turn views and serves per-turn / per-scope rollups."""

    def __init__(self, views: Sequence[TurnCostView] = ()) -> None:
        self._turns: Dict[str, TurnCostView] = {}
        for view in views:
            self._turns[view.turn_id] = view

    # ------------------------------------------------------------------ #
    def add(self, attribution: TurnAttribution, outcome: Any = None) -> TurnCostView:
        """Record one attribution (optionally with its budget verdict)."""
        view = self._view(attribution, outcome)
        self._turns[view.turn_id] = view
        return view

    def record(
        self, attribution: TurnAttribution, outcome: Any = None
    ) -> TurnCostView:
        """Alias of :meth:`add` for callers that think in turns, not rows."""
        return self.add(attribution, outcome)

    def turn(self, turn_id: str) -> Optional[TurnCostView]:
        return self._turns.get(turn_id)

    def turns(self) -> List[TurnCostView]:
        return [self._turns[key] for key in sorted(self._turns)]

    # ------------------------------------------------------------------ #
    def conversation(self, conversation_id: str) -> CostRollup:
        return self._rollup(
            f"conversation:{conversation_id}",
            lambda v: v.conversation_id == conversation_id,
        )

    def tenant(self, tenant_id: str) -> CostRollup:
        return self._rollup(f"tenant:{tenant_id}", lambda v: v.tenant_id == tenant_id)

    def agent(self, agent_id: str) -> CostRollup:
        return self._rollup(f"agent:{agent_id}", lambda v: v.agent_id == agent_id)

    def ticket(self, ticket_id: str) -> CostRollup:
        """The cost of the turns one ticket spawned (the ADR-0014 join)."""
        return self._rollup(f"ticket:{ticket_id}", lambda v: v.ticket_id == ticket_id)

    def totals(self) -> CostRollup:
        return self._rollup("all", lambda _v: True)

    # ------------------------------------------------------------------ #
    def _view(self, attribution: TurnAttribution, outcome: Any) -> TurnCostView:
        return TurnCostView(
            turn_id=attribution.turn_id,
            conversation_id=attribution.conversation_id,
            tenant_id=attribution.tenant_id,
            agent_id=attribution.agent_id,
            ts=attribution.ts,
            outcome=attribution.outcome,
            tier=attribution.tier,
            provider=attribution.provider,
            model=attribution.model,
            ticket_id=attribution.ticket_id,
            input_tokens=attribution.input_tokens,
            output_tokens=attribution.output_tokens,
            latency_ms=attribution.latency_ms,
            cost_usd=attribution.cost_usd,
            cost_source=attribution.cost_source,
            cold_equivalent_cost_usd=attribution.cold_equivalent_cost_usd,
            billable=attribution.billable,
            metered=attribution.metered,
            unmetered_reason=attribution.unmetered_reason,
            cache_hit_share=attribution.cache_hit_share,
            cached_tokens=attribution.cache.cached_tokens,
            prompt_tokens=attribution.cache.prompt_tokens,
            billable_input_tokens=attribution.cache.billable_input_tokens,
            budget_decision=getattr(outcome, "decision", attribution.budget_action),
            budget_allowed=bool(getattr(outcome, "allowed", True)),
            budget_known=outcome is not None,
            budget_code=getattr(outcome, "code", None),
            budget_outcome=getattr(outcome, "outcome", None),
            budget_projected_usd=getattr(outcome, "estimated_cost_usd", None),
            budget_soft_warning=bool(getattr(outcome, "soft_warning", False)),
            budget_hard_stop=bool(getattr(outcome, "hard_stop", False)),
            client_tier=attribution.client_tier,
            tier_claim_honoured=attribution.tier_claim_honoured,
            usage_record_id=attribution.usage_record_id,
            ledger_seq=attribution.ledger_seq,
        )

    def _rollup(self, key: str, predicate: Any) -> CostRollup:
        turns = [view for view in self.turns() if predicate(view)]
        cost = 0.0
        cold = 0.0
        for view in turns:
            if view.cost_usd is not None:
                cost += view.cost_usd
            if view.cold_equivalent_cost_usd is not None:
                cold += view.cold_equivalent_cost_usd
        return CostRollup(
            key=key,
            turns=len(turns),
            allowed_turns=sum(1 for v in turns if v.budget_allowed),
            refused_turns=sum(1 for v in turns if not v.budget_allowed),
            billable_turns=sum(1 for v in turns if v.billable),
            unmetered_turns=sum(1 for v in turns if not v.metered),
            cache_hit_turns=sum(1 for v in turns if v.cached_tokens > 0),
            cost_usd=cost,
            cold_equivalent_cost_usd=cold,
            input_tokens=sum(v.input_tokens for v in turns),
            output_tokens=sum(v.output_tokens for v in turns),
            prompt_tokens=sum(v.prompt_tokens for v in turns),
            cached_tokens=sum(v.cached_tokens for v in turns),
            latency_ms=sum(v.latency_ms for v in turns),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "totals": self.totals().to_dict(),
            "turns": [view.to_dict() for view in self.turns()],
        }
