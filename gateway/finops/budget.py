#!/usr/bin/env python3
"""Per-tenant budget enforcement for the FinOps model chooser (issue #17).

Adapted from the leaderboard ``finops-governor.sh`` pre-flight check (exit 0
OK / 1 DEGRADED / 2 HARD BLOCKED) and ``config/finops-budget.yaml`` per-tier
budget + throttle semantics. The chooser consults a ``BudgetEnforcer`` before
any model call and turns the returned action into routing behavior.

Policy vocabulary (per-tenant, from budgets.yaml):

- stop     - spend is refused once projected use reaches ``warn_at_pct``.
- warn     - spend is allowed up to ``hard_cap_pct`` but every call from
             ``warn_at_pct`` on carries a warning flag.
- fallback - from ``warn_at_pct`` on the chooser downgrades the model tier
             toward the task class's cheapest-capable tier instead of
             blocking.
- hard     - ``hard_cap_pct`` (default 100) is absolute: projected spend at or
  cap        above it always stops, whatever the policy.

The enforcer is a pure decision function over an injected spend ledger, so the
gateway can wire it to the real per-tenant billing state later without
changing the chooser. ``BudgetBlocked`` is the exception the chooser raises
when the decision is ``stop``.

Standalone module: imports nothing from the rest of the package (the loader
for budgets.yaml lives here too, mirroring the module's self-containment).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

import yaml  # type: ignore

PKG_DIR = Path(__file__).resolve().parent
BUDGETS_PATH = PKG_DIR / "budgets.yaml"


class BudgetPolicy(str, Enum):
    """Per-tenant over-budget policy."""

    STOP = "stop"
    WARN = "warn"
    FALLBACK = "fallback"


class BudgetAction(str, Enum):
    """Outcome of a pre-flight budget check."""

    ALLOW = "allow"
    WARN = "warn"
    FALLBACK = "fallback"
    STOP = "stop"


class BudgetError(Exception):
    """Base error for budget enforcement."""


class BudgetBlocked(BudgetError):
    """Raised when a model call is refused by a per-tenant budget."""

    def __init__(self, tenant_id: str, action: str, reason: str) -> None:
        super().__init__(f"tenant {tenant_id!r}: budget {action} — {reason}")
        self.tenant_id = tenant_id
        self.action = action
        self.reason = reason


@dataclass(frozen=True)
class TenantBudget:
    """A tenant's budget line."""

    tenant_id: str
    monthly_budget_usd: float
    policy: BudgetPolicy
    warn_at_pct: float = 80.0
    hard_cap_pct: float = 100.0

    def __post_init__(self) -> None:
        if self.monthly_budget_usd <= 0:
            raise ValueError(
                f"{self.tenant_id}: monthly_budget_usd must be > 0"
            )
        if not 0 < self.warn_at_pct <= self.hard_cap_pct:
            raise ValueError(
                f"{self.tenant_id}: need 0 < warn_at_pct <= hard_cap_pct"
            )

    def decide(self, projected_usd: float) -> BudgetAction:
        """Classify a projected spend against this budget line."""
        pct = (projected_usd / self.monthly_budget_usd) * 100.0
        if pct >= self.hard_cap_pct:
            return BudgetAction.STOP
        if pct >= self.warn_at_pct:
            return {
                BudgetPolicy.STOP: BudgetAction.STOP,
                BudgetPolicy.WARN: BudgetAction.WARN,
                BudgetPolicy.FALLBACK: BudgetAction.FALLBACK,
            }[self.policy]
        return BudgetAction.ALLOW


@dataclass
class BudgetLedger:
    """In-memory cumulative spend per tenant (injectable, reset-able)."""

    _spend: Dict[str, float] = field(default_factory=dict)

    def spend(self, tenant_id: str) -> float:
        return round(self._spend.get(tenant_id, 0.0), 6)

    def add_spend(self, tenant_id: str, amount_usd: float) -> None:
        if amount_usd < 0:
            raise ValueError(f"spend must be >= 0, got {amount_usd}")
        self._spend[tenant_id] = round(self.spend(tenant_id) + amount_usd, 6)

    def reset(self, tenant_id: Optional[str] = None) -> None:
        if tenant_id is None:
            self._spend.clear()
        else:
            self._spend.pop(tenant_id, None)


@dataclass(frozen=True)
class BudgetDecision:
    """Result of a pre-flight budget check for one prospective call."""

    action: BudgetAction
    tenant_id: str
    reason: str
    estimated_cost_usd: float
    projected_usd: float
    budget_usd: float
    pct_used: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "tenant_id": self.tenant_id,
            "reason": self.reason,
            "estimated_cost_usd": self.estimated_cost_usd,
            "projected_usd": self.projected_usd,
            "budget_usd": self.budget_usd,
            "pct_used": self.pct_used,
        }


class BudgetEnforcer:
    """Pre-flight per-tenant budget checks over an injected spend ledger."""

    def __init__(
        self,
        budgets: Optional[Dict[str, TenantBudget]] = None,
        ledger: Optional[BudgetLedger] = None,
        default_policy: BudgetPolicy = BudgetPolicy.WARN,
        default_monthly_budget_usd: float = 1000.0,
    ) -> None:
        self.budgets: Dict[str, TenantBudget] = dict(budgets or {})
        self.ledger = ledger or BudgetLedger()
        # Fallback applied to tenants with no explicit budget line: unbudgeted
        # tenants stay routable but are flagged, so spend is never invisible.
        self.default_policy = default_policy
        self.default_monthly_budget_usd = default_monthly_budget_usd

    def add_budget(self, budget: TenantBudget) -> None:
        self.budgets[budget.tenant_id] = budget

    def budget_for(self, tenant_id: str) -> TenantBudget:
        budget = self.budgets.get(tenant_id)
        if budget is not None:
            return budget
        return TenantBudget(
            tenant_id=tenant_id,
            monthly_budget_usd=self.default_monthly_budget_usd,
            policy=self.default_policy,
        )

    def check(self, tenant_id: str, estimated_cost_usd: float) -> BudgetDecision:
        """Classify one prospective call for ``tenant_id``.

        Never raises: returns a ``BudgetDecision``; the chooser raises
        ``BudgetBlocked`` when the action is ``stop``. Spend is only committed
        to the ledger by ``commit`` after a call is actually made.
        """
        if estimated_cost_usd < 0:
            raise ValueError(f"estimated cost must be >= 0, got {estimated_cost_usd}")
        budget = self.budget_for(tenant_id)
        projected = round(self.ledger.spend(tenant_id) + estimated_cost_usd, 6)
        action = budget.decide(projected)
        pct = round((projected / budget.monthly_budget_usd) * 100.0, 2)
        reason = {
            BudgetAction.ALLOW: "within budget",
            BudgetAction.WARN: "at/above warn threshold; spend flagged",
            BudgetAction.FALLBACK: "at/above warn threshold; downgrade tier",
            BudgetAction.STOP: "budget exhausted or hard cap reached",
        }[action]
        return BudgetDecision(
            action=action,
            tenant_id=tenant_id,
            reason=reason,
            estimated_cost_usd=estimated_cost_usd,
            projected_usd=projected,
            budget_usd=budget.monthly_budget_usd,
            pct_used=pct,
        )

    def commit(self, tenant_id: str, estimated_cost_usd: float) -> None:
        """Record realized spend after a model call (ledger update)."""
        self.ledger.add_spend(tenant_id, estimated_cost_usd)


# --------------------------------------------------------------------------- #
# budgets.yaml loader
# --------------------------------------------------------------------------- #
def parse_budgets(data: Dict[str, Any]) -> Dict[str, TenantBudget]:
    """Parse and validate a budgets mapping (from budgets.yaml or a test)."""
    if not isinstance(data, dict):
        raise BudgetError("budget config must be a mapping")
    raw = data.get("budgets")
    if not isinstance(raw, dict):
        raise BudgetError("budget config: missing 'budgets' mapping")
    budgets: Dict[str, TenantBudget] = {}
    for tenant_id, cfg in raw.items():
        if not isinstance(cfg, dict):
            raise BudgetError(f"budgets.{tenant_id}: config must be a mapping")
        monthly = cfg.get("monthlyBudgetUsd")
        if not isinstance(monthly, (int, float)) or monthly <= 0:
            raise BudgetError(
                f"budgets.{tenant_id}: monthlyBudgetUsd must be a number > 0"
            )
        policy_raw = cfg.get("policy", "warn")
        try:
            policy = BudgetPolicy(policy_raw)
        except ValueError:
            raise BudgetError(
                f"budgets.{tenant_id}: policy must be one of "
                f"stop|warn|fallback, got {policy_raw!r}"
            ) from None
        budgets[tenant_id] = TenantBudget(
            tenant_id=tenant_id,
            monthly_budget_usd=float(monthly),
            policy=policy,
            warn_at_pct=float(cfg.get("warnAtPct", 80.0)),
            hard_cap_pct=float(cfg.get("hardCapPct", 100.0)),
        )
    return budgets


def load_budgets(
    path: Path = BUDGETS_PATH,
    ledger: Optional[BudgetLedger] = None,
    default_policy: BudgetPolicy = BudgetPolicy.WARN,
) -> BudgetEnforcer:
    """Load budgets.yaml into a ``BudgetEnforcer`` backed by ``ledger``."""
    if not path.is_file():
        raise BudgetError(f"budget config not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise BudgetError(f"{path}: top-level YAML must be a mapping")
    parsed = parse_budgets(data)
    default = data.get("defaultPolicy", default_policy.value)
    try:
        policy = BudgetPolicy(default)
    except ValueError:
        raise BudgetError(f"{path}: defaultPolicy must be stop|warn|fallback") from None
    return BudgetEnforcer(budgets=parsed, ledger=ledger, default_policy=policy)
