#!/usr/bin/env python3
"""Per-tenant AND per-role budget enforcement for the FinOps model chooser.

Adapted from the leaderboard ``finops-governor.sh`` pre-flight check (exit 0
OK / 1 DEGRADED / 2 HARD BLOCKED) and ``config/finops-budget.yaml`` per-tier
budget + throttle semantics. The chooser consults a ``BudgetDecisionMaker`` before
any model call and turns the returned action into routing behavior.

Two independent budget axes share one ledger:

- **Per-tenant** (issue #17) — ``TenantBudgetLine`` / ``BudgetDecisionMaker`` below.
  The tenant's monthly ceiling and its stop/warn/fallback policy.
- **Per-role** (issue #633, workbook-2) — ``RoleBudget`` / ``RoleBudgetEnforcer``.
  The monthly cap the workbook declares per C-suite role (CEO 300 / CTO 250 /
  COO 100 / CFO 50 / CMO 200 USD). The caps are **consumed** from the registry
  (``registry/personas/org-chart.yaml`` + the bound persona cards), never
  redefined here: ``load_role_budgets`` asserts chart and card agree and fails
  closed on drift, missing card, or an unrecognised cap.

The per-role cap path is **deterministic arithmetic only** — the same
percentage comparison as the tenant path, no model call, no token spend. This
is the workbook mechanical rule "zero-token arithmetic" (CFO row): a budget
decision must never be delegated to a generative loop, or enforcement would
cost tokens to decide whether tokens may be spent.

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

# The registry declaration this module CONSUMES read-only (issue #632,
# workbook-1). Resolved relative to the repo root so the module stays
# standalone: gateway/finops/budget.py -> <root>/registry/personas/...
REPO_ROOT = PKG_DIR.parent.parent
ORG_CHART_PATH = REPO_ROOT / "registry" / "personas" / "org-chart.yaml"
PERSONA_CARDS_DIR = REPO_ROOT / "registry" / "personas" / "cards"


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
class TenantBudgetLine:
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


class BudgetDecisionMaker:
    """Pre-flight per-tenant budget checks over an injected spend ledger."""

    def __init__(
        self,
        budgets: Optional[Dict[str, TenantBudgetLine]] = None,
        ledger: Optional[BudgetLedger] = None,
        default_policy: BudgetPolicy = BudgetPolicy.WARN,
        default_monthly_budget_usd: float = 1000.0,
    ) -> None:
        self.budgets: Dict[str, TenantBudgetLine] = dict(budgets or {})
        self.ledger = ledger or BudgetLedger()
        # Fallback applied to tenants with no explicit budget line: unbudgeted
        # tenants stay routable but are flagged, so spend is never invisible.
        self.default_policy = default_policy
        self.default_monthly_budget_usd = default_monthly_budget_usd
        # Per-role cap axis (issue #633). Attached by ``load_budgets``; ``None``
        # means no per-role ceiling was declared, so only the tenant axis applies.
        self.roles: Optional["RoleBudgetEnforcer"] = None

    def add_budget(self, budget: TenantBudgetLine) -> None:
        self.budgets[budget.tenant_id] = budget

    def budget_for(self, tenant_id: str) -> TenantBudgetLine:
        budget = self.budgets.get(tenant_id)
        if budget is not None:
            return budget
        return TenantBudgetLine(
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
# Per-role monthly caps (issue #633, workbook-2)
#
# The workbook declares one monthly cap per C-suite role. The role is the
# persona the call is dispatched for (``Choice.agent_id``); its cap lives in
# registry/personas/org-chart.yaml and on the bound persona card, which
# registry.validate_org_chart() already refuses to let drift. This section
# CONSUMES that declaration — it never restates a cap as a literal.
# --------------------------------------------------------------------------- #

# Default over-cap policy for a role: a role at its cap REFUSES spend
# ("stop"), which is the workbook-2 acceptance criterion. The policy is
# configurable per budgets.yaml; the default is deliberately the strictest.
ROLE_DEFAULT_POLICY = BudgetPolicy.STOP
# A role is at cap only when projected spend actually reaches the cap, so the
# default warn threshold is 100% (the cap itself). A tenant-level warn_at_pct
# is a *soft* early-warning line; a role cap is the hard ceiling.
ROLE_DEFAULT_WARN_AT_PCT = 100.0

# Ledger key namespace. A role cap and a tenant budget are separate axes over
# the same ledger, so a role's spend is keyed distinctly from its tenant's.
ROLE_SCOPE = "role"


def role_ledger_key(tenant: str, role_id: str) -> str:
    """Ledger key for one (tenant, role) pair (never collides with a tenant)."""
    return f"{ROLE_SCOPE}:{tenant}/{role_id}"


class RoleBudgetError(BudgetError):
    """Raised when the per-role cap declaration is missing or inconsistent."""


class RoleBudgetBlocked(BudgetError):
    """Raised when a call is refused by a per-ROLE monthly cap.

    Carries the underlying ``BudgetDecision`` so the caller can record the
    refused decision in the metering store — a cap refusal is a metered event,
    not a silent drop.
    """

    def __init__(self, role_id: str, tenant: str, decision: "BudgetDecision") -> None:
        super().__init__(
            f"role {role_id!r} (tenant {tenant!r}): budget {decision.action.value} "
            f"— {decision.reason}"
        )
        self.role_id = role_id
        self.tenant = tenant
        self.decision = decision

    @property
    def action(self) -> str:
        return self.decision.action.value

    @property
    def reason(self) -> str:
        return self.decision.reason


@dataclass(frozen=True)
class RoleBudget:
    """One role's monthly cap, consumed from the workbook-1 declaration."""

    role_id: str
    monthly_cap_usd: float
    policy: BudgetPolicy = ROLE_DEFAULT_POLICY
    warn_at_pct: float = ROLE_DEFAULT_WARN_AT_PCT
    hard_cap_pct: float = 100.0
    tenant: str = "platform"
    default_model_tier: Optional[str] = None
    heartbeat_schedule: Optional[str] = None
    source_path: Optional[str] = None

    def __post_init__(self) -> None:
        if self.monthly_cap_usd < 0:
            raise RoleBudgetError(
                f"role {self.role_id!r}: monthly cap must be >= 0, "
                f"got {self.monthly_cap_usd}"
            )
        if not 0 < self.warn_at_pct <= self.hard_cap_pct:
            raise RoleBudgetError(
                f"role {self.role_id!r}: need 0 < warn_at_pct <= hard_cap_pct"
            )

    def decide(self, projected_usd: float) -> BudgetAction:
        """Classify a projected spend against this role's cap.

        Deterministic arithmetic only: no model call, no token spend. This is
        the workbook "zero-token arithmetic" rule — the cap path is a pure
        function of numbers, so verifying a refusal never costs money.
        """
        if self.monthly_cap_usd == 0:
            # A zero cap refuses all spend deterministically (no per-cent math
            # is meaningful when the denominator is 0).
            return BudgetAction.STOP
        pct = (projected_usd / self.monthly_cap_usd) * 100.0
        if pct >= self.hard_cap_pct:
            return BudgetAction.STOP
        if pct >= self.warn_at_pct:
            return {
                BudgetPolicy.STOP: BudgetAction.STOP,
                BudgetPolicy.WARN: BudgetAction.WARN,
                BudgetPolicy.FALLBACK: BudgetAction.FALLBACK,
            }[self.policy]
        return BudgetAction.ALLOW

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role_id": self.role_id,
            "tenant": self.tenant,
            "monthlyCapUsd": self.monthly_cap_usd,
            "policy": self.policy.value,
            "warnAtPct": self.warn_at_pct,
            "hardCapPct": self.hard_cap_pct,
            "defaultModelTier": self.default_model_tier,
            "heartbeatSchedule": self.heartbeat_schedule,
            "source": self.source_path,
        }


class RoleBudgetEnforcer:
    """Pre-flight per-ROLE cap checks over the same ledger as the tenant axis.

    A role cap is consulted **before** the tenant budget for a call dispatched
    for that role: the narrower ceiling is the one that must not be crossed,
    and a role at its cap refuses spend even when the tenant overall is well
    inside its own budget.
    """

    def __init__(
        self,
        roles: Optional[Dict[str, RoleBudget]] = None,
        ledger: Optional[BudgetLedger] = None,
    ) -> None:
        self.roles: Dict[str, RoleBudget] = dict(roles or {})
        self.ledger = ledger if ledger is not None else BudgetLedger()

    # -- registration ---------------------------------------------------------
    def _key(self, role_id: str, tenant: str) -> str:
        return role_ledger_key(tenant, role_id)

    def add_role(self, budget: RoleBudget) -> None:
        """Register one role cap (keyed by tenant + role id)."""
        self.roles[self._key(budget.role_id, budget.tenant)] = budget

    def has_role(self, role_id: str, tenant: str = "platform") -> bool:
        return self._key(role_id, tenant) in self.roles

    def role_for(self, role_id: str, tenant: str = "platform") -> Optional[RoleBudget]:
        """The role's cap, or ``None`` when the role declares no cap.

        ``None`` means "no per-role ceiling applies" — the call then falls
        through to the tenant budget. It never means "allowed": an unknown role
        is not silently unbudgeted, it is simply governed by the tenant axis.
        """
        return self.roles.get(self._key(role_id, tenant))

    # -- decision -------------------------------------------------------------
    def check(
        self,
        role_id: str,
        estimated_cost_usd: float,
        tenant: str = "platform",
    ) -> Optional[BudgetDecision]:
        """Classify one prospective call against ``role_id``'s cap.

        Returns ``None`` when the role declares no cap (no ceiling applies).
        Never raises: the chooser raises ``RoleBudgetBlocked`` on ``stop``.
        """
        if estimated_cost_usd < 0:
            raise ValueError(f"estimated cost must be >= 0, got {estimated_cost_usd}")
        budget = self.role_for(role_id, tenant)
        if budget is None:
            return None
        key = self._key(role_id, tenant)
        projected = round(self.ledger.spend(key) + estimated_cost_usd, 6)
        action = budget.decide(projected)
        pct = (
            round((projected / budget.monthly_cap_usd) * 100.0, 2)
            if budget.monthly_cap_usd
            else 100.0
        )
        reason = {
            BudgetAction.ALLOW: "within role cap",
            BudgetAction.WARN: "at/above role warn threshold; spend flagged",
            BudgetAction.FALLBACK: "at/above role warn threshold; downgrade tier",
            BudgetAction.STOP: "role monthly cap reached; spend refused",
        }[action]
        return BudgetDecision(
            action=action,
            tenant_id=f"{tenant}/{role_id}",
            reason=reason,
            estimated_cost_usd=estimated_cost_usd,
            projected_usd=projected,
            budget_usd=budget.monthly_cap_usd,
            pct_used=pct,
        )

    def commit(self, role_id: str, estimated_cost_usd: float, tenant: str = "platform") -> None:
        """Record realized role spend after a model call (ledger update)."""
        self.ledger.add_spend(self._key(role_id, tenant), estimated_cost_usd)

    def spend(self, role_id: str, tenant: str = "platform") -> float:
        return self.ledger.spend(self._key(role_id, tenant))

    def reset(self, role_id: Optional[str] = None, tenant: str = "platform") -> None:
        if role_id is None:
            self.ledger.reset()
        else:
            self.ledger.reset(self._key(role_id, tenant))


# --------------------------------------------------------------------------- #
# org-chart -> per-role caps (consume the workbook-1 declaration)
# --------------------------------------------------------------------------- #
_CAP_FIELD = "monthlyBudgetCapUsd"
_CAP_FIELDS = ("defaultModelTier", _CAP_FIELD, "heartbeatSchedule")


def load_org_chart(path: Path = ORG_CHART_PATH) -> Dict[str, Any]:
    """Load the registry org-chart declaration (consumed read-only).

    The registry owns schema validation (``registry.personas.registry``); this
    loader deliberately does not re-implement it. It fails closed only on a
    missing/unreadable/mis-shaped declaration, so the finops lane stays
    decoupled from the registry module while still consuming its artifact.
    """
    if not path.is_file():
        raise RoleBudgetError(f"org chart not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            chart = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise RoleBudgetError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(chart, dict):
        raise RoleBudgetError(f"{path}: org chart must be a YAML mapping")
    roles = chart.get("roles")
    if not isinstance(roles, list) or not roles:
        raise RoleBudgetError(f"{path}: org chart must declare a non-empty 'roles' list")
    return chart


def load_persona_card(persona_id: str, cards_dir: Path = PERSONA_CARDS_DIR) -> Dict[str, Any]:
    """Load one persona card (consumed read-only, for cap agreement checks)."""
    path = Path(cards_dir) / f"{persona_id}.yaml"
    if not path.is_file():
        raise RoleBudgetError(
            f"persona card for role {persona_id!r} not found at {path}"
        )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            card = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise RoleBudgetError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(card, dict):
        raise RoleBudgetError(f"{path}: persona card must be a YAML mapping")
    return card


def load_role_budgets(
    chart: Optional[Dict[str, Any]] = None,
    *,
    chart_path: Path = ORG_CHART_PATH,
    cards_dir: Path = PERSONA_CARDS_DIR,
    policy: BudgetPolicy = ROLE_DEFAULT_POLICY,
    warn_at_pct: float = ROLE_DEFAULT_WARN_AT_PCT,
    hard_cap_pct: float = 100.0,
) -> Dict[str, RoleBudget]:
    """Resolve per-role caps by **consuming** the workbook-1 declaration.

    For every role in the org chart this reads the role's
    ``monthlyBudgetCapUsd`` (and tier/heartbeat) and asserts the bound persona
    card declares the same values — mirroring
    ``registry.personas.registry.validate_org_chart``, so a cap can never drift
    between the chart and the card this enforcer would act on. A role with no
    numeric cap, a missing card, or a chart/card disagreement raises
    ``RoleBudgetError`` (fail closed).

    The cap values are read, never restated as literals: the numbers live in
    ``registry/personas/**`` and this function is their only consumer here.
    """
    chart = chart if chart is not None else load_org_chart(chart_path)
    tenant = str(chart.get("tenant", "platform"))
    roles: Dict[str, RoleBudget] = {}
    for node in chart.get("roles", []):
        if not isinstance(node, dict):
            raise RoleBudgetError("org chart role entries must be mappings")
        role_id = node.get("id")
        if not isinstance(role_id, str) or not role_id:
            raise RoleBudgetError(f"org chart role with invalid id: {role_id!r}")
        cap = node.get(_CAP_FIELD)
        if isinstance(cap, bool) or not isinstance(cap, (int, float)):
            raise RoleBudgetError(
                f"role {role_id!r}: {_CAP_FIELD} must be a number, got {cap!r}"
            )
        if cap < 0:
            raise RoleBudgetError(
                f"role {role_id!r}: {_CAP_FIELD} must be >= 0, got {cap!r}"
            )
        card = load_persona_card(role_id, cards_dir)
        for fname in _CAP_FIELDS:
            if fname in node and node[fname] != card.get(fname):
                raise RoleBudgetError(
                    f"role {role_id!r}: chart declares {fname}={node[fname]!r} but "
                    f"its card declares {card.get(fname)!r} (chart and card must agree)"
                )
        roles[role_ledger_key(tenant, role_id)] = RoleBudget(
            role_id=role_id,
            monthly_cap_usd=float(cap),
            policy=policy,
            warn_at_pct=warn_at_pct,
            hard_cap_pct=hard_cap_pct,
            tenant=tenant,
            default_model_tier=node.get("defaultModelTier"),
            heartbeat_schedule=node.get("heartbeatSchedule"),
            source_path=str(chart_path),
        )
    if not roles:
        raise RoleBudgetError("org chart declares no roles")
    return roles


def load_role_enforcer(
    ledger: Optional[BudgetLedger] = None,
    *,
    chart_path: Path = ORG_CHART_PATH,
    cards_dir: Path = PERSONA_CARDS_DIR,
    policy: BudgetPolicy = ROLE_DEFAULT_POLICY,
    warn_at_pct: float = ROLE_DEFAULT_WARN_AT_PCT,
) -> RoleBudgetEnforcer:
    """Build a ``RoleBudgetEnforcer`` from the live workbook-1 declaration."""
    roles = load_role_budgets(
        chart_path=chart_path,
        cards_dir=cards_dir,
        policy=policy,
        warn_at_pct=warn_at_pct,
    )
    return RoleBudgetEnforcer(roles=roles, ledger=ledger)


# --------------------------------------------------------------------------- #
# budgets.yaml loader
# --------------------------------------------------------------------------- #
def parse_role_policy(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the per-role defaults block of budgets.yaml (issue #633).

    The per-role *caps* are consumed from the registry org chart; budgets.yaml
    only documents the vocabulary and may override the default over-cap policy
    and warn threshold for every role. Returns the resolved defaults.
    """
    if not isinstance(data, dict):
        raise BudgetError("budget config must be a mapping")
    block = data.get("roles")
    if block is None:
        return {
            "policy": ROLE_DEFAULT_POLICY,
            "warn_at_pct": ROLE_DEFAULT_WARN_AT_PCT,
            "hard_cap_pct": 100.0,
            "capSource": "registry/personas/org-chart.yaml",
        }
    if not isinstance(block, dict):
        raise BudgetError("roles config must be a mapping")
    policy_raw = block.get("defaultPolicy", ROLE_DEFAULT_POLICY.value)
    try:
        policy = BudgetPolicy(policy_raw)
    except ValueError:
        raise BudgetError(
            f"roles.defaultPolicy must be one of stop|warn|fallback, got {policy_raw!r}"
        ) from None
    warn_at = float(block.get("warnAtPct", ROLE_DEFAULT_WARN_AT_PCT))
    hard_cap = float(block.get("hardCapPct", 100.0))
    if not 0 < warn_at <= hard_cap:
        raise BudgetError("roles: need 0 < warnAtPct <= hardCapPct")
    return {
        "policy": policy,
        "warn_at_pct": warn_at,
        "hard_cap_pct": hard_cap,
        "capSource": block.get("capSource", "registry/personas/org-chart.yaml"),
    }


def parse_budgets(data: Dict[str, Any]) -> Dict[str, TenantBudgetLine]:
    """Parse and validate a budgets mapping (from budgets.yaml or a test)."""
    if not isinstance(data, dict):
        raise BudgetError("budget config must be a mapping")
    raw = data.get("budgets")
    if not isinstance(raw, dict):
        raise BudgetError("budget config: missing 'budgets' mapping")
    budgets: Dict[str, TenantBudgetLine] = {}
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
        budgets[tenant_id] = TenantBudgetLine(
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
    *,
    with_roles: bool = True,
    org_chart_path: Path = ORG_CHART_PATH,
    cards_dir: Path = PERSONA_CARDS_DIR,
) -> BudgetDecisionMaker:
    """Load budgets.yaml into a ``BudgetDecisionMaker`` backed by ``ledger``.

    With ``with_roles`` (the default) the returned enforcer also exposes the
    per-role cap axis (issue #633) via ``BudgetDecisionMaker.roles``, sharing this
    enforcer's ledger so role spend and tenant spend stay on one book.
    """
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
    enforcer = BudgetDecisionMaker(budgets=parsed, ledger=ledger, default_policy=policy)
    if with_roles:
        role_cfg = parse_role_policy(data)
        roles = load_role_budgets(
            chart_path=org_chart_path,
            cards_dir=cards_dir,
            policy=role_cfg["policy"],
            warn_at_pct=role_cfg["warn_at_pct"],
            hard_cap_pct=role_cfg["hard_cap_pct"],
        )
        enforcer.roles = RoleBudgetEnforcer(roles=roles, ledger=enforcer.ledger)
    return enforcer
