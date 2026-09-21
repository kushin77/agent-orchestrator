"""telemetry/chat — per-turn budget enforcement for chat (issue #506).

---knowledge---
module_id: telemetry.chat.budget_guard
system: telemetry
app: chat
solution_class: enterprise
patterns: [pre-dispatch-guard, consumed-rails, observe-mode]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [TurnBudgetGuard, GuardedTurnRunner, GuardedTurnResult, TurnBudgetOutcome]
invariants: "the guard composes the merged telemetry.budgets rails in their safe order rather than inventing a second ladder"
gotchas: ""
related: ["#506", "#1510"]
do_not_duplicate: null
---knowledge---


The pre-dispatch guard a chat turn passes *before* the model is called.  It
does not invent a ladder: the enforcement rails are the merged
``telemetry.budgets`` ones — the global kill switch
(``config/killswitch.yaml``), the per-tenant quota rails and the per-tenant
budget enforcer over the durable spend ledger — composed in the safe order by
``telemetry.budgets.preflight.preflight`` (kill switch first, then quota, then
budget).  Their decision vocabulary (``allow`` -> ``warn`` -> ``block`` /
``refuse``) is itself consumed from ``gateway/finops``.

Two doctrines this module encodes:

1. **Refused and still metered** (AO-GR-18: metering is the mechanism,
   enforcement the guarantee).  A refused turn is *not* silently dropped: it
   produces exactly one metering record — the non-billable event the metering
   lane already defines (``billable=False``, ``metered=True``) — and exactly
   one ledger event.  A refusal must be visible in the numbers, not just in
   the log.
2. **Refused before the call.**  ``GuardedTurnRunner`` asks the guard first
   and only invokes the injected provider when the turn is allowed, so a
   kill-switch-on tenant never reaches a provider (the gate proves it with a
   counting provider asserting zero calls).

Observe/enforce is the consumed rollout posture: a tenant in ``observe`` mode
is reported (``would_warn``/``would_block``) and never refused, so a new
control can ship without stopping spend.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from telemetry.budgets.budget import (
    DEFAULT_POLICY_CONFIG,
    BudgetEnforcer,
    load_budget_policies,
)
from telemetry.budgets.killswitch import (
    DEFAULT_KILLSWITCH_CONFIG,
    KillSwitchController,
    load_killswitch_state,
)
from telemetry.budgets.ledger import SpendLedger, StaticLedger
from telemetry.budgets.model import (  # consumed ladder vocabulary
    BLOCKING_DECISIONS,
    DECISION_FALLBACK,
    DECISION_WARN,
    DECISION_WOULD_WARN,
    EnforcerDecision,
    MODE_OBSERVE,
    OUTCOME_BLOCKED,
    day_bucket,  # consumed bucket helpers: the turn's own window, never a copy
    month_bucket,
)
from telemetry.budgets.preflight import PreflightResult, first_blocking_rail, preflight
from telemetry.budgets.quota import (
    DEFAULT_QUOTA_CONFIG,
    QuotaEnforcer,
    StaticProbe,
    load_quota_policies,
)

from .model import ChatTurn, TurnAttribution

#: Decisions that warn without refusing (the soft-cap half of the ladder).
SOFT_WARNING_DECISIONS = frozenset(
    {DECISION_WARN, DECISION_WOULD_WARN, DECISION_FALLBACK}
)


@dataclass(frozen=True)
class TurnBudgetOutcome:
    """The guard's verdict for one chat turn.

    ``outcome`` is the metering non-billable outcome the turn is recorded
    under when it is refused (``None`` when allowed), so the refusal and its
    metering row agree by construction.  ``metered`` is always ``True``: a
    turn is metered whatever the verdict.  ``day`` / ``month`` are the buckets
    the turn was **actually evaluated in** (pinned by the caller, else derived
    from the turn's own timestamp), so a verdict names the window it decided
    over instead of leaving it to be inferred from when the check happened to
    run — the #506 date bomb's blind spot.
    """

    turn_id: str
    tenant_id: str
    agent_id: str
    decision: str
    allowed: bool
    code: str
    reason: str
    outcome: Optional[str] = None
    rails: Tuple[Dict[str, Any], ...] = ()
    metered: bool = True
    model: Optional[str] = None
    vendor: Optional[str] = None
    estimated_cost_usd: float = 0.0
    cap: Optional[str] = None
    day: Optional[str] = None
    month: Optional[str] = None

    @property
    def refused(self) -> bool:
        """True when the turn must not reach a provider."""
        return not self.allowed

    @property
    def soft_warning(self) -> bool:
        """True when the turn is allowed but flagged at/above the warn mark."""
        return self.allowed and self.decision in SOFT_WARNING_DECISIONS

    @property
    def hard_stop(self) -> bool:
        """True when the turn was refused by a hard rail (block/refuse)."""
        return self.decision in BLOCKING_DECISIONS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turnId": self.turn_id,
            "tenantId": self.tenant_id,
            "agentId": self.agent_id,
            "decision": self.decision,
            "allowed": self.allowed,
            "refused": self.refused,
            "softWarning": self.soft_warning,
            "hardStop": self.hard_stop,
            "code": self.code,
            "reason": self.reason,
            "outcome": self.outcome,
            "metered": self.metered,
            "model": self.model,
            "vendor": self.vendor,
            "estimatedCostUsd": self.estimated_cost_usd,
            "cap": self.cap,
            "day": self.day,
            "month": self.month,
            "rails": [dict(rail) for rail in self.rails],
        }


def _reporting_rail(result: PreflightResult) -> Optional[EnforcerDecision]:
    """The rail whose decision to surface: the blocker, else the warn, else first.

    This is not a second ladder — it only picks which consumed decision to
    report, preferring a refusal over a warning over silence.
    """
    blocking = first_blocking_rail(result)
    if blocking is not None:
        return blocking
    for decision_rail in result.decisions:
        if decision_rail.decision in SOFT_WARNING_DECISIONS:
            return decision_rail
    return result.decisions[0] if result.decisions else None


class TurnBudgetGuard:
    """Pre-dispatch enforcement for one chat turn over the merged budget rails.

    Rails are injected so a deployment wires its live kill switch, quota probe
    and durable spend ledger, while tests wire deterministic in-memory ones.
    A rail that is not supplied is skipped by the composed pre-flight check.
    """

    def __init__(
        self,
        *,
        killswitch: Optional[KillSwitchController] = None,
        quota: Optional[QuotaEnforcer] = None,
        budget: Optional[BudgetEnforcer] = None,
    ) -> None:
        self.killswitch = killswitch
        self.quota = quota
        self.budget = budget

    @classmethod
    def from_config(
        cls,
        *,
        spend_ledger: Optional[SpendLedger] = None,
        default_mode: str = MODE_OBSERVE,
        killswitch_path: Optional[Path] = None,
        policies_path: Optional[Path] = None,
        quotas_path: Optional[Path] = None,
        probe: Optional[StaticProbe] = None,
    ) -> "TurnBudgetGuard":
        """Wire the shipped ``telemetry/budgets`` config into a turn guard.

        The kill switch ships OFF (``config/killswitch.yaml``) and budget
        policies default to ``observe`` (new controls default OFF); a tenant
        is flipped to ``enforce`` by its own policy, never by this lane.
        """
        state = load_killswitch_state(killswitch_path or DEFAULT_KILLSWITCH_CONFIG)
        ledger = spend_ledger if spend_ledger is not None else StaticLedger()
        policies = load_budget_policies(policies_path or DEFAULT_POLICY_CONFIG)
        quotas = load_quota_policies(quotas_path or DEFAULT_QUOTA_CONFIG)
        return cls(
            killswitch=KillSwitchController(initial=state),
            quota=QuotaEnforcer(ledger, quotas, probe=probe or StaticProbe()),
            budget=BudgetEnforcer(ledger, policies, default_mode=default_mode),
        )

    # ------------------------------------------------------------------ #
    def check(
        self,
        turn: ChatTurn,
        *,
        estimated_cost_usd: float = 0.0,
        requested_tokens: int = 0,
        model: Optional[str] = None,
        vendor: Optional[str] = None,
        critical: Optional[bool] = None,
        day: Optional[str] = None,
        month: Optional[str] = None,
    ) -> TurnBudgetOutcome:
        """Run the composed pre-dispatch check for one turn.

        ``estimated_cost_usd`` is the chooser's projected cost for the turn
        (the caller's figure — this lane does not re-derive it).  ``model`` /
        ``vendor`` are the *routed* values from the chooser's stamp, used for
        the per-vendor cap; a client-supplied model is never passed here.

        ``day`` / ``month`` pin the evaluation buckets deliberately.  When a
        caller does not pin them they are **derived from the turn's own
        timestamp** (``turn.normalized_ts``) instead of being left for the
        rails to resolve: a rail reads the *live* clock when it is given no
        bucket (``StaticLedger`` falls back to ``today_utc()`` /
        ``this_month_utc()``), so a turn dated in the past was evaluated
        against today's spend, missed a fixture seeded on its own bucket, and
        silently came back *allowed*.  That is the #506 date bomb — a green
        that expires with the calendar.  Deriving the bucket from the turn is
        what makes a pinned fixture correct **by construction** rather than
        correct only on the day it was written.  A live turn carries
        ``ts == now``, so its derived bucket is today's and a live turn is
        judged exactly as before.
        """
        if estimated_cost_usd < 0:
            raise ValueError(
                f"estimated_cost_usd must be >= 0, got {estimated_cost_usd}"
            )
        turn_ts = turn.normalized_ts
        if day is None:
            day = day_bucket(turn_ts)
        if month is None:
            month = month_bucket(turn_ts)
        is_critical = turn.critical if critical is None else critical
        result = preflight(
            turn.tenant_id,
            agent_id=turn.agent_id,
            vendor=vendor,
            model=model,
            critical=is_critical,
            requested_cost_usd=estimated_cost_usd,
            requested_tokens=requested_tokens,
            day=day,
            month=month,
            killswitch=self.killswitch,
            quota=self.quota,
            budget=self.budget,
        )
        rail = _reporting_rail(result)
        allowed = result.allowed
        return TurnBudgetOutcome(
            turn_id=turn.turn_id,
            tenant_id=turn.tenant_id,
            agent_id=turn.agent_id,
            decision=result.decision,
            allowed=allowed,
            code=rail.code if rail is not None else "chat.turn.no_rail",
            reason=rail.reason if rail is not None else "no enforcement rail wired",
            outcome=None if allowed else (result.outcome or OUTCOME_BLOCKED),
            rails=tuple(decision.to_dict() for decision in result.decisions),
            model=model,
            vendor=vendor,
            estimated_cost_usd=estimated_cost_usd,
            cap=rail.cap if rail is not None else None,
            day=day,
            month=month,
        )


@dataclass(frozen=True)
class GuardedTurnResult:
    """What one guarded turn produced: the verdict, and the attribution."""

    outcome: TurnBudgetOutcome
    attribution: TurnAttribution
    provider_called: bool

    @property
    def allowed(self) -> bool:
        return self.outcome.allowed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome.to_dict(),
            "providerCalled": self.provider_called,
            "attribution": self.attribution.to_dict(),
        }


class GuardedTurnRunner:
    """Runs a turn through the guard, then the provider, then the atttributor.

    The order is the whole point: the guard is consulted **before** the
    provider, so a refused turn never reaches a model (and never becomes a
    provider call that has to be reconciled afterwards).  A refused turn is
    still attributed — metered and audited as a non-billable event — so the
    FinOps numbers show the spend that was prevented, not just the spend that
    happened.
    """

    def __init__(self, guard: TurnBudgetGuard, attributor: Any) -> None:
        self.guard = guard
        self.attributor = attributor

    def run(
        self,
        turn: ChatTurn,
        provider: Callable[[ChatTurn], Any],
        *,
        estimated_cost_usd: float = 0.0,
        requested_tokens: int = 0,
        model: Optional[str] = None,
        vendor: Optional[str] = None,
        tier: Optional[str] = None,
        critical: Optional[bool] = None,
        day: Optional[str] = None,
        month: Optional[str] = None,
    ) -> GuardedTurnResult:
        """Guard, then (only if allowed) call ``provider``, then attribute.

        ``day`` / ``month`` pin the evaluation buckets for a caller that wants
        them fixed; left unset they are **not passed on** (rather than passed
        as ``None``) so a guard subclass injected here keeps the ability to
        supply the bucket itself, and the guard's own default — the turn's own
        bucket — applies (see :meth:`TurnBudgetGuard.check`).
        """
        buckets: Dict[str, str] = {}
        if day is not None:
            buckets["day"] = day
        if month is not None:
            buckets["month"] = month
        outcome = self.guard.check(
            turn,
            estimated_cost_usd=estimated_cost_usd,
            requested_tokens=requested_tokens,
            model=model,
            vendor=vendor,
            critical=critical,
            **buckets,
        )
        if not outcome.allowed:
            attribution = self.attributor.attribute_refusal(
                turn,
                outcome=outcome.outcome or OUTCOME_BLOCKED,
                decision=outcome.decision,
                code=outcome.code,
                reason=outcome.reason,
                model=model,
                tier=tier,
            )
            return GuardedTurnResult(
                outcome=outcome, attribution=attribution, provider_called=False
            )

        record = provider(turn)
        attribution = self.attributor.attribute(turn, record, decision=outcome.decision)
        return GuardedTurnResult(
            outcome=outcome, attribution=attribution, provider_called=True
        )
