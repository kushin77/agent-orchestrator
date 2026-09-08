#!/usr/bin/env python3
"""FinOps model chooser: cheapest-capable-model-wins routing ladder (issue #17).

The gateway proxy (issue #16, later wave) and the state-machine engine call
``ModelChooser.choose(...)`` before every commercial model call. The chooser
implements the FinOps doctrine — default every task to the cheapest model that
can plausibly finish it, escalate only on observed difficulty or failure,
security/IaC/governance never below L1 — and returns a concrete
``Choice`` (tier + model + estimated cost + reasons).

Selection pipeline (all offline, deterministic, unit-testable):

1. **Task class → cheapest capable default tier** from ``tiers.yaml``.
2. **Security guardrail**: a task class tagged ``security``/``iac``/
   ``governance`` is floored at ``security.floorTier`` (L1) and can never
   resolve to L0.
3. **Difficulty escalation**: the difficulty scorer's 0-100 score is compared
   against ``escalation.thresholds`` to pick the target tier; the target is
   clamped to the task class's ``maxTier`` (per-task-type tier cap).
4. **Budget pre-flight**: the per-tenant budget enforcer is consulted.
   ``stop`` raises ``BudgetBlocked``; ``fallback`` downgrades the tier toward
   the class's cheapest-capable floor (never below it, never below the
   security floor) instead of blocking; ``warn`` flags the call.
5. **Health-aware model pick**: within the chosen tier the cheapest HEALTHY
   model wins; an unhealthy primary falls back to the next candidate in the
   same tier; if the whole tier is unhealthy the chooser escalates one tier
   (respecting the class cap) or raises ``NoHealthyModelError``.
6. **Cost attribution**: every non-blocked choice emits a ``CallRecord`` to
   the injected ``MeteringSink`` (Phase-5 metering-store hook).

Health is an injected signal (dict or callable of model id -> healthy), so
tests and the gateway can drive fallback behavior without a live provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from budget import BudgetAction, BudgetBlocked, BudgetDecision, BudgetEnforcer
from complexity import DifficultyScore, DifficultyScorer
from loader import ModelSpec, TaskClass, TierTable, ValidationError
from metering import CallRecord, MeteringSink, NoopMeteringSink

# Default assumed tokens per routed call when the caller does not provide one.
DEFAULT_TOKENS_PER_CALL = 4000

# Default monthly budget ceiling for tenants without an explicit line.
DEFAULT_UNBUDGETED_MONTHLY_USD = 1000.0


class ChooserError(Exception):
    """Base error raised by the model chooser."""


class NoHealthyModelError(ChooserError):
    """Every candidate of every reachable tier is unhealthy."""


class EscalationCapReached(ChooserError):
    """A failure escalation was requested past the task class's maxTier."""


@dataclass(frozen=True)
class Choice:
    """A routed model selection (what the gateway proxy executes)."""

    task_class: str
    tier: str  # L0/L1/L2
    model: ModelSpec
    estimated_cost_usd: float
    tenant_id: str
    agent_id: str
    complexity: Optional[float] = None
    budget_action: str = "allow"
    warning: Optional[str] = None
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_class": self.task_class,
            "tier": self.tier,
            "model": self.model.id,
            "provider": self.model.provider,
            "estimated_cost_usd": self.estimated_cost_usd,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "complexity": self.complexity,
            "budget_action": self.budget_action,
            "warning": self.warning,
            "reasons": list(self.reasons),
        }


# Health signal: absent, a {model_id: bool} map, or a predicate callable.
HealthSignal = Optional[Union[Dict[str, bool], Callable[[str], bool]]]


class ModelChooser:
    """Cheapest-capable model router with escalation, budgets, and health."""

    def __init__(
        self,
        table: TierTable,
        scorer: Optional[DifficultyScorer] = None,
        budget_enforcer: Optional[BudgetEnforcer] = None,
        sink: Optional[MeteringSink] = None,
        health: HealthSignal = None,
        tokens_per_call: int = DEFAULT_TOKENS_PER_CALL,
    ) -> None:
        self.table = table
        self.scorer = scorer or DifficultyScorer()
        self.enforcer = budget_enforcer
        self.sink = sink if sink is not None else NoopMeteringSink()
        self.health = health
        self.tokens_per_call = tokens_per_call

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def choose(
        self,
        task_class: str,
        tenant_id: str = "system",
        agent_id: str = "anonymous",
        complexity: Optional[float] = None,
        prompt: Optional[str] = None,
        files: Optional[List[str]] = None,
        symbols: Optional[List[str]] = None,
        repo_loc: Optional[int] = None,
        tokens: Optional[int] = None,
    ) -> Choice:
        """Route one task to the cheapest capable healthy model.

        Raises ``ValidationError`` for an unknown task class,
        ``BudgetBlocked`` when the tenant budget stops the call, and
        ``NoHealthyModelError`` when no reachable tier has a healthy model.
        """
        cls = self.table.task_class(task_class)  # unknown -> ValidationError
        if complexity is None:
            complexity = self._score_complexity(
                cls, prompt or "", files or [], symbols or [], repo_loc
            )
        if complexity < 0 or complexity > 100:
            raise ValidationError(f"complexity must be in [0, 100], got {complexity}")

        target = self._difficulty_tier(cls, complexity)
        reasons: List[str] = []
        return self._select(cls, target, complexity, tenant_id, agent_id, tokens, reasons)

    def escalate_on_failure(
        self,
        choice: Choice,
        trigger: str = "failure",
        tokens: Optional[int] = None,
    ) -> Choice:
        """Escalate one tier up after a failure at the current tier.

        Raises ``EscalationCapReached`` when the task class's ``maxTier``
        forbids going higher (per-task-type tier cap).
        """
        cls = self.table.task_class(choice.task_class)
        nxt = self.table.next_tier(choice.tier)
        if nxt is None or self.table.rank(nxt) > self.table.rank(cls.max_tier):
            raise EscalationCapReached(
                f"task class {choice.task_class!r} is capped at {cls.max_tier}; "
                f"cannot escalate from {choice.tier}"
            )
        reasons = list(choice.reasons) + [f"{trigger}: escalate {choice.tier} -> {nxt}"]
        return self._select(
            cls,
            nxt,
            choice.complexity,
            choice.tenant_id,
            choice.agent_id,
            tokens,
            reasons,
        )

    # ------------------------------------------------------------------ #
    # Internal pipeline
    # ------------------------------------------------------------------ #
    def _score_complexity(
        self,
        cls: TaskClass,
        prompt: str,
        files: List[str],
        symbols: List[str],
        repo_loc: Optional[int],
    ) -> float:
        scored: DifficultyScore = self.scorer.score(
            prompt=prompt,
            files=files,
            symbols=symbols,
            repo_loc=repo_loc,
            task_class=cls.name,
        )
        return scored.score

    def _difficulty_tier(self, cls: TaskClass, complexity: float) -> str:
        """Cheapest capable tier for a task, before the security floor clamp.

        Starts from the class's default tier, then raises one tier per
        escalation threshold the difficulty score clears, and caps at the
        class's ``maxTier`` (per-task-type tier cap).

        ``escalation.thresholds`` is keyed by the tier being LEFT: crossing
        ``thresholds[L0]`` (40) means L0 is insufficient and the task routes
        at L1; crossing ``thresholds[L1]`` (70) routes it at L2.
        """
        keys = self.table.keys()
        start = self.table.rank(cls.default_tier)
        target = cls.default_tier
        # Climb from the default tier upward: leaving each rung requires the
        # difficulty score to clear that rung's threshold.
        for i in range(start, len(keys) - 1):
            source = keys[i]
            if complexity >= self.table.threshold(source):
                target = keys[i + 1]
        if self.table.rank(target) > self.table.rank(cls.max_tier):
            target = cls.max_tier
        return target

    def _select(
        self,
        cls: TaskClass,
        start_tier: str,
        complexity: Optional[float],
        tenant_id: str,
        agent_id: str,
        tokens: Optional[int],
        reasons: List[str],
    ) -> Choice:
        """Resolve a concrete model for ``cls`` starting at ``start_tier``.

        Applies the security floor, the budget policy loop (fallback
        downgrades), and health-aware cheapest-model picking.
        """
        table = self.table
        tokens = tokens if tokens is not None else self.tokens_per_call
        floor = table.security_floor if cls.guardrail is not None else table.lowest_tier()
        # Downgrades under budget-fallback never go below the class's cheapest
        # capable tier NOR below the guardrail floor.
        downgrade_low = table.higher(cls.default_tier, floor)
        if table.rank(start_tier) < table.rank(floor):
            start_tier = floor

        tier_key = start_tier
        while True:
            if table.rank(tier_key) > table.rank(cls.max_tier):
                tier_key = cls.max_tier
            healthy = table.healthy_models(tier_key, self._is_healthy)
            if not healthy:
                nxt = table.next_tier(tier_key)
                if nxt is not None and table.rank(nxt) <= table.rank(cls.max_tier):
                    reasons.append(f"no-healthy-model@{tier_key}: escalate to {nxt}")
                    tier_key = nxt
                    continue
                raise NoHealthyModelError(
                    f"no healthy model for task class {cls.name!r} at tier {tier_key}"
                )
            model = healthy[0]  # cheapest healthy candidate
            est = table.estimate_cost(model, tokens)

            decision = self._check_budget(tenant_id, est)
            if decision.action is BudgetAction.STOP:
                raise BudgetBlocked(tenant_id, decision.action.value, decision.reason)
            if decision.action is BudgetAction.FALLBACK:
                prev = table.prev_tier(tier_key)
                if prev is not None and table.rank(prev) >= table.rank(downgrade_low):
                    reasons.append(f"budget-fallback: downgrade {tier_key} -> {prev}")
                    tier_key = prev
                    continue
                # Already at the class's cheapest-capable floor: flag, don't block.
                reasons.append("budget-fallback: at cheapest-capable tier; flagged")

            warning: Optional[str] = None
            if decision.action in (BudgetAction.WARN, BudgetAction.FALLBACK):
                warning = (
                    f"tenant {tenant_id} at {decision.pct_used}% of budget "
                    f"({decision.reason})"
                )

            choice = Choice(
                task_class=cls.name,
                tier=tier_key,
                model=model,
                estimated_cost_usd=est,
                tenant_id=tenant_id,
                agent_id=agent_id,
                complexity=complexity,
                budget_action=decision.action.value,
                warning=warning,
                reasons=list(reasons),
            )
            self._record(choice)
            return choice

    def _check_budget(self, tenant_id: str, estimated_cost_usd: float) -> BudgetDecision:
        """Pre-flight budget check; no enforcer means always allow."""
        if self.enforcer is None:
            return BudgetDecision(
                action=BudgetAction.ALLOW,
                tenant_id=tenant_id,
                reason="no budget enforcer configured",
                estimated_cost_usd=estimated_cost_usd,
                projected_usd=estimated_cost_usd,
                budget_usd=0.0,
                pct_used=0.0,
            )
        return self.enforcer.check(tenant_id, estimated_cost_usd)

    def _is_healthy(self, model_id: str) -> bool:
        """Health of a model from the injected signal (absent = healthy)."""
        if self.health is None:
            return True
        if isinstance(self.health, dict):
            return self.health.get(model_id, True)
        return bool(self.health(model_id))

    def _record(self, choice: Choice) -> None:
        """Emit cost attribution for a routed call to the metering sink."""
        record = CallRecord(
            tenant_id=choice.tenant_id,
            agent_id=choice.agent_id,
            task_class=choice.task_class,
            tier=choice.tier,
            model=choice.model.id,
            provider=choice.model.provider,
            estimated_cost_usd=choice.estimated_cost_usd,
            budget_action=choice.budget_action,
            complexity=choice.complexity,
            reasons=list(choice.reasons),
        )
        self.sink.record(record)
