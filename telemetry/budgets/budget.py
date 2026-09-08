"""telemetry/budgets — per-tenant + per-vendor/model budget enforcer (issue #34).

The hard cost/token budget rail.  Each tenant has a budget policy (YAML
under ``config/policies.yaml``): a cost limit over a window (monthly USD by
default), a token limit over a window (daily tokens by default), and
optional per-vendor caps (the *per-vendor/model* budget ladder).  Spend is
read from the durable metering feed (issue #33) through the ``SpendLedger``
protocol in ``ledger.py`` — never a per-process counter.

Decision ladder (CONSUMED from gateway/finops, issue #17):

- below the ``warnAtPct`` threshold  -> ``allow``;
- at/above ``warnAtPct`` (but below the limit) -> ``warn`` (flagged);
- at/above the limit (100%) -> ``block`` (the caller must refuse the call);
- in ``observe`` mode the same ladder reports ``would_warn`` /
  ``would_block`` and never refuses (safe-rollout doctrine: new blocking
  controls default OFF; a tenant is flipped to ``enforce`` only after a
  recorded track record exists).

A ``block`` decision maps onto the metering non-billable outcome
``budget_exceeded`` (issue #33 ``NON_BILLABLE_OUTCOMES``), so a refused call
is never metered as usage — and its projected spend is not double counted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from telemetry.budgets.ledger import SpendLedger
from telemetry.budgets.model import (
    DECISION_ALLOW,
    DECISION_BLOCK,
    DECISION_WARN,
    DECISION_WOULD_BLOCK,
    DECISION_WOULD_WARN,
    EnforcerDecision,
    KIND_BUDGET,
    MODE_ENFORCE,
    MODE_OBSERVE,
    MODES,
    OUTCOME_BUDGET_EXCEEDED,
    WINDOWS,
    this_month_utc,
    today_utc,
)

DEFAULT_POLICY_CONFIG = Path(__file__).resolve().parent / "config" / "policies.yaml"

#: Default warn threshold when a policy omits ``warnAtPct``.
DEFAULT_WARN_AT_PCT = 0.8


@dataclass(frozen=True)
class BudgetLimit:
    """One limit (cost USD or token count) over one window."""

    window: str  # day | month
    limit: float  # USD or tokens
    warn_at_pct: float = DEFAULT_WARN_AT_PCT

    def __post_init__(self) -> None:
        if self.window not in WINDOWS:
            raise ValueError(f"unknown budget window: {self.window!r}")
        if self.limit <= 0:
            raise ValueError("budget limit must be positive")
        if not 0.0 < self.warn_at_pct <= 1.0:
            raise ValueError("warnAtPct must be in (0, 1]")

    @property
    def warn_at(self) -> float:
        """The absolute threshold at which a call is flagged warn."""
        return self.warn_at_pct * self.limit

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window": self.window,
            "limit": round(self.limit, 8),
            "warnAtPct": self.warn_at_pct,
        }


@dataclass(frozen=True)
class VendorBudgetCap:
    """A per-vendor budget cap for one tenant (per-vendor/model ladder).

    Vendor spend is read from the metering per-vendor rollup, which is
    aggregated monthly, so the cap window is fixed to ``month`` (fail
    closed rather than pretend a daily vendor figure exists).
    """

    vendor: str
    window: str = "month"
    limit_usd: float = 0.0
    warn_at_pct: float = DEFAULT_WARN_AT_PCT

    def __post_init__(self) -> None:
        if not self.vendor:
            raise ValueError("vendor cap requires a vendor name")
        if self.window != "month":
            raise ValueError("vendor cap window must be 'month' (vendor spend "
                             "is metered monthly)")
        if self.limit_usd <= 0:
            raise ValueError("vendor cap limit must be positive")
        if not 0.0 < self.warn_at_pct <= 1.0:
            raise ValueError("warnAtPct must be in (0, 1]")

    @property
    def limit(self) -> float:
        """Uniform limit accessor (mirrors :class:`BudgetLimit`)."""
        return self.limit_usd

    @property
    def warn_at(self) -> float:
        return self.warn_at_pct * self.limit_usd

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vendor": self.vendor,
            "window": self.window,
            "limitUsd": round(self.limit_usd, 8),
            "warnAtPct": self.warn_at_pct,
        }


@dataclass(frozen=True)
class TenantBudgetPolicy:
    """One tenant's full budget policy (cost + tokens + vendor caps)."""

    tenant_id: str
    mode: str = MODE_OBSERVE
    cost_limit: Optional[BudgetLimit] = None   # default: monthly USD
    token_limit: Optional[BudgetLimit] = None  # default: daily tokens
    vendor_caps: Tuple[VendorBudgetCap, ...] = ()

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise ValueError("tenant_id is required")
        if self.mode not in MODES:
            raise ValueError(f"unknown budget mode: {self.mode!r}")
        if self.token_limit is not None and self.token_limit.window != "day":
            raise ValueError(
                "token budget window must be 'day' (the metering feed's "
                "daily token budget — issue #33)"
            )

    def cap_for(self, vendor: Optional[str]) -> Optional[VendorBudgetCap]:
        """The per-vendor cap for ``vendor``, or ``None`` when unset."""
        if not vendor:
            return None
        for cap in self.vendor_caps:
            if cap.vendor == vendor:
                return cap
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenantId": self.tenant_id,
            "mode": self.mode,
            "costLimit": self.cost_limit.to_dict() if self.cost_limit else None,
            "tokenLimit": self.token_limit.to_dict() if self.token_limit else None,
            "vendorCaps": [c.to_dict() for c in self.vendor_caps],
        }


def load_budget_policies(path: Path = DEFAULT_POLICY_CONFIG) -> Dict[str, TenantBudgetPolicy]:
    """Load per-tenant budget policies from YAML (fail closed on malformed)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path}: budget policy config root must be a mapping")
    policies: Dict[str, TenantBudgetPolicy] = {}
    for entry in raw.get("policies", []) or []:
        if not isinstance(entry, Mapping):
            raise ValueError(f"{path}: each policy must be a mapping")
        tenant_id = str(entry.get("tenantId") or "")
        if not tenant_id:
            raise ValueError(f"{path}: policy missing tenantId")
        caps = []
        for cap in entry.get("vendorCaps", []) or []:
            if not isinstance(cap, Mapping):
                raise ValueError(f"{path}: vendor cap must be a mapping")
            caps.append(
                VendorBudgetCap(
                    vendor=str(cap.get("vendor") or ""),
                    window=str(cap.get("window") or "month"),
                    limit_usd=float(cap.get("limitUsd") or 0.0),
                    warn_at_pct=float(cap.get("warnAtPct") or DEFAULT_WARN_AT_PCT),
                )
            )
        cost = entry.get("cost") or {}
        tokens = entry.get("tokens") or {}
        policies[tenant_id] = TenantBudgetPolicy(
            tenant_id=tenant_id,
            mode=str(entry.get("mode") or MODE_OBSERVE),
            cost_limit=_parse_limit(cost, path, "cost"),
            token_limit=_parse_limit(tokens, path, "tokens"),
            vendor_caps=tuple(caps),
        )
    return policies


def _parse_limit(block: Mapping[str, Any], path: Path, label: str) -> Optional[BudgetLimit]:
    """Parse a ``cost``/``tokens`` sub-block into a ``BudgetLimit`` or None.

    ``cost`` limits default to the monthly window; ``tokens`` limits are
    daily (the metering feed's daily token budget, issue #33) — both are the
    windows the durable ledger can actually measure.
    """
    if not block:
        return None
    if not isinstance(block, Mapping):
        raise ValueError(f"{path}: {label} must be a mapping")
    limit = block.get("limitUsd") if label == "cost" else block.get("limit")
    if limit is None:
        return None
    if label == "cost":
        window = str(block.get("window") or "month")
    else:
        window = "day"  # token budgets are daily (issue #33 vocabulary)
    return BudgetLimit(
        window=window,
        limit=float(limit),
        warn_at_pct=float(block.get("warnAtPct") or DEFAULT_WARN_AT_PCT),
    )


class BudgetEnforcer:
    """Per-tenant budget enforcement over a durable spend ledger.

    ``ledger`` supplies current spend (canonically the metering-feed
    adapter); ``policies`` maps tenant id -> ``TenantBudgetPolicy``
    (loadable from YAML).  A tenant with no policy and no default limit is
    unlimited and always ``allow``.
    """

    def __init__(
        self,
        ledger: SpendLedger,
        policies: Optional[Mapping[str, TenantBudgetPolicy]] = None,
        *,
        default_mode: str = MODE_OBSERVE,
    ) -> None:
        if default_mode not in MODES:
            raise ValueError(f"unknown default budget mode: {default_mode!r}")
        self.ledger = ledger
        self.policies: Dict[str, TenantBudgetPolicy] = dict(policies or {})
        self.default_mode = default_mode

    def policy_for(self, tenant_id: str) -> Optional[TenantBudgetPolicy]:
        return self.policies.get(tenant_id)

    def check(
        self,
        tenant_id: str,
        *,
        agent_id: Optional[str] = None,
        vendor: Optional[str] = None,
        model: Optional[str] = None,
        requested_cost_usd: float = 0.0,
        requested_tokens: int = 0,
        day: Optional[str] = None,
        month: Optional[str] = None,
    ) -> EnforcerDecision:
        """Pre-flight one prospective call against the tenant's budget.

        Returns the **most severe** budget decision across the tenant cost
        limit, the tenant token limit and (when a ``vendor`` is supplied) the
        per-vendor cap.  In ``enforce`` mode an over-limit call is ``block``;
        in ``observe`` mode it is ``would_block`` and never refused.

        ``day``/``month`` pin the evaluation buckets (``YYYY-MM-DD`` /
        ``YYYY-MM``) for reproducible offline checks; they default to the
        current UTC day/month.
        """
        policy = self.policy_for(tenant_id)
        if policy is None:
            return EnforcerDecision(
                tenant_id=tenant_id,
                kind=KIND_BUDGET,
                decision=DECISION_ALLOW,
                reason="no budget policy configured for tenant",
                code="budget.unconfigured",
                agent_id=agent_id,
                vendor=vendor,
                model=model,
                mode=self.default_mode,
            )

        decisions: List[EnforcerDecision] = []
        if policy.cost_limit is not None:
            decisions.append(
                self._check_limit(
                    policy=policy,
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    vendor=vendor,
                    model=model,
                    limit=policy.cost_limit,
                    kind_code="cost",
                    current=self._current_cost(policy.cost_limit, tenant_id, month=month),
                    requested=requested_cost_usd,
                    requested_label="cost_usd",
                )
            )
        if policy.token_limit is not None:
            decisions.append(
                self._check_limit(
                    policy=policy,
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    vendor=vendor,
                    model=model,
                    limit=policy.token_limit,
                    kind_code="tokens",
                    current=self._current_tokens(policy.token_limit, tenant_id, day=day),
                    requested=float(requested_tokens),
                    requested_label="tokens",
                )
            )
        cap = policy.cap_for(vendor)
        if cap is not None:
            decisions.append(
                self._check_limit(
                    policy=policy,
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    vendor=vendor,
                    model=model,
                    limit=cap,
                    kind_code="vendor_cost",
                    current=self._current_vendor_cost(
                        cap, tenant_id, vendor or "", month=month
                    ),
                    requested=requested_cost_usd,
                    requested_label="cost_usd",
                    vendor_name=vendor,
                )
            )

        if not decisions:
            return EnforcerDecision(
                tenant_id=tenant_id,
                kind=KIND_BUDGET,
                decision=DECISION_ALLOW,
                reason="budget policy has no limits configured",
                code="budget.no_limits",
                agent_id=agent_id,
                vendor=vendor,
                model=model,
                mode=policy.mode,
            )
        # Most severe wins: block > would_block > warn > would_warn > allow.
        return max(decisions, key=_severity)

    # ------------------------------------------------------------------ #
    def _current_cost(self, limit: BudgetLimit, tenant_id: str, *, month: Optional[str]) -> float:
        if limit.window == "day":
            return float(self.ledger.daily_cost(tenant_id))
        return float(self.ledger.monthly_cost(tenant_id, month=month))

    def _current_tokens(self, limit: BudgetLimit, tenant_id: str, *, day: Optional[str]) -> float:
        # Token budgets are daily (validated in ``TenantBudgetPolicy``).
        return float(self.ledger.daily_tokens(tenant_id, day=day))

    def _current_vendor_cost(
        self, cap: VendorBudgetCap, tenant_id: str, vendor: str, *, month: Optional[str]
    ) -> float:
        # Vendor caps are monthly (validated in ``VendorBudgetCap``).
        return float(self.ledger.vendor_monthly_cost(tenant_id, vendor, month=month))

    def _check_limit(
        self,
        *,
        policy: TenantBudgetPolicy,
        tenant_id: str,
        agent_id: Optional[str],
        vendor: Optional[str],
        model: Optional[str],
        limit: Any,
        kind_code: str,
        current: float,
        requested: float,
        requested_label: str,
        vendor_name: Optional[str] = None,
    ) -> EnforcerDecision:
        projected = current + requested
        limit_value = float(limit.limit)
        warn_at = float(limit.warn_at)
        code_base = (
            f"budget.vendor.{vendor_name or vendor}"
            if vendor_name
            else f"budget.{kind_code}"
        )
        if projected >= limit_value:
            decision, code_suffix = (
                (DECISION_BLOCK, "exceeded")
                if policy.mode == MODE_ENFORCE
                else (DECISION_WOULD_BLOCK, "would_exceed")
            )
            return EnforcerDecision(
                tenant_id=tenant_id,
                kind=KIND_BUDGET,
                decision=decision,
                reason=(
                    f"{'enforce' if policy.mode == MODE_ENFORCE else 'observe'}: "
                    f"projected {requested_label}={projected:.6g} >= "
                    f"limit {limit_value:.6g} ({code_base})"
                ),
                code=f"{code_base}.{code_suffix}",
                agent_id=agent_id,
                vendor=vendor_name or vendor,
                model=model,
                window=limit.window,
                current=current,
                requested=requested,
                limit=limit_value,
                warn_at=warn_at,
                mode=policy.mode,
                outcome=OUTCOME_BUDGET_EXCEEDED if decision == DECISION_BLOCK else None,
            )
        if projected >= warn_at:
            decision = (
                DECISION_WARN if policy.mode == MODE_ENFORCE else DECISION_WOULD_WARN
            )
            return EnforcerDecision(
                tenant_id=tenant_id,
                kind=KIND_BUDGET,
                decision=decision,
                reason=(
                    f"projected {requested_label}={projected:.6g} at/above "
                    f"warnAt={warn_at:.6g} ({code_base})"
                ),
                code=f"{code_base}.warn",
                agent_id=agent_id,
                vendor=vendor_name or vendor,
                model=model,
                window=limit.window,
                current=current,
                requested=requested,
                limit=limit_value,
                warn_at=warn_at,
                mode=policy.mode,
            )
        return EnforcerDecision(
            tenant_id=tenant_id,
            kind=KIND_BUDGET,
            decision=DECISION_ALLOW,
            reason=f"within budget ({projected:.6g} < warnAt={warn_at:.6g})",
            code=f"{code_base}.allow",
            agent_id=agent_id,
            vendor=vendor_name or vendor,
            model=model,
            window=limit.window,
            current=current,
            requested=requested,
            limit=limit_value,
            warn_at=warn_at,
            mode=policy.mode,
        )


#: Order used to pick the most severe of several budget decisions.
_SEVERITY_ORDER = {
    DECISION_ALLOW: 0,
    DECISION_WOULD_WARN: 1,
    DECISION_WARN: 2,
    DECISION_WOULD_BLOCK: 3,
    DECISION_BLOCK: 4,
}


def _severity(decision: EnforcerDecision) -> int:
    return _SEVERITY_ORDER.get(decision.decision, 0)
