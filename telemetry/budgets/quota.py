"""telemetry/budgets — per-tenant soft/hard quota enforcer (issue #34).

The soft/hard resource-quota rail the gateway/engine consult before
dispatch: per-tenant limits over **calls**, **tokens**, **concurrency** and
**storage**.  Soft limits warn; hard limits refuse the request (the
shared-services soft/hard convention, cannibalized from
``resource-quota-enforcer``).  Usage for calls/tokens is read from the
durable metering feed (issue #33) through the ``SpendLedger``; concurrency
and storage are supplied by an injected ``state_probe`` (a real deployment
wires the gateway in-flight counter / control-plane usage there).

Quota overrides per plan (acceptance criterion 4, entitlements link, phase
6): a tenant references a ``plan`` key (``free``/``standard``/``premium``/
``enterprise``) and the loader resolves effective limits as **plan
defaults < per-tenant explicit overrides**.  Phase-6 entitlements will drive
plan membership; this lane ships the plan-keyed override mechanics as a
consumed config contract (the later lane populates ``planDefaults``).

A ``block`` decision maps onto the metering non-billable outcome ``blocked``
(issue #33 ``NON_BILLABLE_OUTCOMES``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol

import yaml

from telemetry.budgets.ledger import SpendLedger
from telemetry.budgets.model import (
    DECISION_ALLOW,
    DECISION_BLOCK,
    DECISION_WARN,
    EnforcerDecision,
    KIND_QUOTA,
    OUTCOME_BLOCKED,
    QUOTA_RESOURCES,
    QUOTA_STATUS_EXCEEDED,
    QUOTA_STATUS_OK,
    QUOTA_STATUS_WARNING,
    RESOURCE_CONCURRENCY,
    RESOURCE_REQUESTS,
    RESOURCE_STORAGE,
    RESOURCE_TOKENS,
    day_bucket,
)

DEFAULT_QUOTA_CONFIG = Path(__file__).resolve().parent / "config" / "quotas.yaml"
DEFAULT_PLAN = "free"


@dataclass(frozen=True)
class QuotaLimit:
    """One per-tenant soft/hard limit over a quota resource."""

    resource: str
    window: Optional[str]          # day for requests/tokens; None for concurrency/storage
    soft_limit: float
    hard_limit: float

    def __post_init__(self) -> None:
        if self.resource not in QUOTA_RESOURCES:
            raise ValueError(f"unknown quota resource: {self.resource!r}")
        if self.soft_limit < 0 or self.hard_limit < 0:
            raise ValueError("quota limits must be non-negative")
        if self.hard_limit < self.soft_limit:
            raise ValueError("hard_limit must be >= soft_limit")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource": self.resource,
            "window": self.window,
            "softLimit": round(self.soft_limit, 8),
            "hardLimit": round(self.hard_limit, 8),
        }


@dataclass(frozen=True)
class QuotaPolicy:
    """One tenant's effective quota set (after plan-default resolution)."""

    tenant_id: str
    plan: str = DEFAULT_PLAN
    limits: Dict[str, QuotaLimit] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise ValueError("tenant_id is required")
        if self.limits is None:
            object.__setattr__(self, "limits", {})
        for resource, limit in self.limits.items():
            if limit.resource != resource:
                raise ValueError("quota limit key must match its resource")

    def limit_for(self, resource: str) -> Optional[QuotaLimit]:
        return self.limits.get(resource)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenantId": self.tenant_id,
            "plan": self.plan,
            "quotas": [l.to_dict() for l in sorted(
                self.limits.values(), key=lambda l: l.resource
            )],
        }


class StateProbe(Protocol):
    """Supplies live concurrency/storage figures per tenant.

    ``SpendLedger`` covers durable calls/tokens; concurrency and storage are
    not in the metering feed, so a deployment injects a probe wired to the
    gateway in-flight counter / control-plane usage.
    """

    def inflight(self, tenant_id: str) -> int:
        """Currently in-flight (concurrent) model calls for ``tenant_id``."""

    def storage_bytes(self, tenant_id: str) -> int:
        """Currently stored bytes for ``tenant_id``."""


class StaticProbe:
    """Dict-backed probe for tests/offline use.

    Unseeded tenants fall back to ``default_inflight`` / ``default_storage``
    (so an offline CLI can apply one live figure to the checked tenant).
    """

    def __init__(
        self,
        inflight: Optional[dict[str, int]] = None,
        storage: Optional[dict[str, int]] = None,
        *,
        default_inflight: int = 0,
        default_storage: int = 0,
    ) -> None:
        self._inflight = dict(inflight or {})
        self._storage = dict(storage or {})
        self._default_inflight = int(default_inflight)
        self._default_storage = int(default_storage)

    def seed_inflight(self, tenant_id: str, count: int) -> None:
        self._inflight[tenant_id] = int(count)

    def seed_storage(self, tenant_id: str, bytes_: int) -> None:
        self._storage[tenant_id] = int(bytes_)

    def inflight(self, tenant_id: str) -> int:
        return int(self._inflight.get(tenant_id, self._default_inflight))

    def storage_bytes(self, tenant_id: str) -> int:
        return int(self._storage.get(tenant_id, self._default_storage))


def load_quota_policies(path: Path = DEFAULT_QUOTA_CONFIG) -> Dict[str, QuotaPolicy]:
    """Load quota policies, resolving per-tenant overrides over plan defaults.

    Schema (see ``config/quotas.yaml``):

    .. code-block:: yaml

        planDefaults:
          free:      { requests: {window: day, softLimit: .., hardLimit: ..}, .. }
        tenantQuotas:
          - tenantId: acme
            plan: enterprise          # entitlements link (phase 6)
            quotas: { requests: {..}, concurrency: {softLimit: .., hardLimit: ..} }

    Effective limits: plan default < explicit per-tenant value (a tenant
    resource with neither is unlimited).
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path}: quota config root must be a mapping")
    plan_defaults: Dict[str, Dict[str, QuotaLimit]] = {}
    for plan_name, resources in (raw.get("planDefaults") or {}).items():
        plan_defaults[str(plan_name)] = _parse_resource_map(resources, path)
    policies: Dict[str, QuotaPolicy] = {}
    for entry in raw.get("tenantQuotas", []) or []:
        if not isinstance(entry, Mapping):
            raise ValueError(f"{path}: each tenantQuota must be a mapping")
        tenant_id = str(entry.get("tenantId") or "")
        if not tenant_id:
            raise ValueError(f"{path}: tenantQuota missing tenantId")
        plan = str(entry.get("plan") or DEFAULT_PLAN)
        explicit = _parse_resource_map(entry.get("quotas") or {}, path)
        defaults = plan_defaults.get(plan, {})
        merged: Dict[str, QuotaLimit] = dict(defaults)
        merged.update(explicit)  # per-tenant override wins
        policies[tenant_id] = QuotaPolicy(tenant_id=tenant_id, plan=plan, limits=merged)
    return policies


def _parse_resource_map(
    block: Any, path: Path
) -> Dict[str, QuotaLimit]:
    if not block:
        return {}
    if not isinstance(block, Mapping):
        raise ValueError(f"{path}: quota resources must be a mapping")
    limits: Dict[str, QuotaLimit] = {}
    for resource, spec in block.items():
        if not isinstance(spec, Mapping):
            raise ValueError(f"{path}: quota {resource} must be a mapping")
        if resource not in QUOTA_RESOURCES:
            raise ValueError(f"{path}: unknown quota resource {resource!r}")
        window = spec.get("window")
        if resource in (RESOURCE_CONCURRENCY, RESOURCE_STORAGE):
            window = None
        limits[str(resource)] = QuotaLimit(
            resource=str(resource),
            window=None if window is None else str(window),
            soft_limit=float(spec.get("softLimit") or 0.0),
            hard_limit=float(spec.get("hardLimit") or 0.0),
        )
    return limits


class QuotaEnforcer:
    """Per-tenant soft/hard quota enforcement over a spend ledger + probe.

    ``ledger`` supplies durable calls/tokens usage; ``probe`` supplies live
    concurrency/storage; ``policies`` maps tenant id -> ``QuotaPolicy``.  A
    tenant with no quota for a resource is unlimited (``allow``).
    """

    def __init__(
        self,
        ledger: SpendLedger,
        policies: Optional[Mapping[str, QuotaPolicy]] = None,
        *,
        probe: Optional[StateProbe] = None,
    ) -> None:
        self.ledger = ledger
        self.policies: Dict[str, QuotaPolicy] = dict(policies or {})
        self.probe = probe or StaticProbe()

    def policy_for(self, tenant_id: str) -> Optional[QuotaPolicy]:
        return self.policies.get(tenant_id)

    def current_usage(self, tenant_id: str, resource: str, *, day: Optional[str] = None) -> float:
        """The durable/live current usage for one resource (0 when none)."""
        if resource == RESOURCE_REQUESTS:
            return float(self.ledger.daily_calls(tenant_id, day=day))
        if resource == RESOURCE_TOKENS:
            return float(self.ledger.daily_tokens(tenant_id, day=day))
        if resource == RESOURCE_CONCURRENCY:
            return float(self.probe.inflight(tenant_id))
        if resource == RESOURCE_STORAGE:
            return float(self.probe.storage_bytes(tenant_id))
        raise ValueError(f"unknown quota resource: {resource!r}")

    def status(self, tenant_id: str, resource: str, *, day: Optional[str] = None) -> str:
        """Current soft/hard status for one resource (ok/warning/exceeded)."""
        limit = self._limit(tenant_id, resource)
        if limit is None:
            return QUOTA_STATUS_OK
        usage = self.current_usage(tenant_id, resource, day=day)
        return quota_status(usage, limit.soft_limit, limit.hard_limit)

    def check(
        self,
        tenant_id: str,
        *,
        agent_id: Optional[str] = None,
        resource: Optional[str] = None,
        requested: float = 1.0,
        requested_tokens: float = 0.0,
        requested_storage: float = 0.0,
        day: Optional[str] = None,
    ) -> EnforcerDecision:
        """Pre-flight one call against the tenant's quotas.

        With ``resource`` set, only that resource is checked and ``requested``
        is its projected increment (1 for a call on requests/concurrency,
        projected tokens, or a storage delta in bytes).  Without ``resource``
        every configured resource is checked: requests/concurrency project one
        call, tokens project ``requested_tokens``, storage projects
        ``requested_storage``; the most severe decision wins.  ``day`` pins
        the evaluation day bucket (``YYYY-MM-DD``) for reproducible checks.
        """
        policy = self.policy_for(tenant_id)
        if policy is None:
            return EnforcerDecision(
                tenant_id=tenant_id,
                kind=KIND_QUOTA,
                decision=DECISION_ALLOW,
                reason="no quota policy configured for tenant",
                code="quota.unconfigured",
                agent_id=agent_id,
            )
        if resource is not None:
            limit = policy.limit_for(resource)
            if limit is None:
                return EnforcerDecision(
                    tenant_id=tenant_id,
                    kind=KIND_QUOTA,
                    decision=DECISION_ALLOW,
                    reason=f"no {resource} quota configured for tenant",
                    code=f"quota.{resource}.unconfigured",
                    agent_id=agent_id,
                )
            usage = self.current_usage(tenant_id, resource, day=day)
            return self._check_resource(
                tenant_id=tenant_id,
                agent_id=agent_id,
                limit=limit,
                usage=usage,
                requested=requested,
            )
        decisions: list[EnforcerDecision] = []
        for res in sorted(policy.limits):
            limit = policy.limit_for(res)
            if limit is None:
                continue
            usage = self.current_usage(tenant_id, res, day=day)
            decisions.append(
                self._check_resource(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    limit=limit,
                    usage=usage,
                    requested=_default_requested(res, requested_tokens, requested_storage),
                )
            )
        if not decisions:
            return EnforcerDecision(
                tenant_id=tenant_id,
                kind=KIND_QUOTA,
                decision=DECISION_ALLOW,
                reason="no quota limits configured for tenant",
                code="quota.no_limits",
                agent_id=agent_id,
            )
        return max(decisions, key=_severity)

    # ------------------------------------------------------------------ #
    def _limit(self, tenant_id: str, resource: str) -> Optional[QuotaLimit]:
        policy = self.policy_for(tenant_id)
        if policy is None:
            return None
        return policy.limit_for(resource)

    def _check_resource(
        self,
        *,
        tenant_id: str,
        agent_id: Optional[str],
        limit: QuotaLimit,
        usage: float,
        requested: float,
    ) -> EnforcerDecision:
        projected = usage + requested
        if projected > limit.hard_limit:
            return EnforcerDecision(
                tenant_id=tenant_id,
                kind=KIND_QUOTA,
                decision=DECISION_BLOCK,
                reason=(
                    f"hard {limit.resource} quota exceeded: {projected:g} > "
                    f"hardLimit {limit.hard_limit:g}"
                ),
                code=f"quota.hard.{limit.resource}.exceeded",
                agent_id=agent_id,
                resource=limit.resource,
                window=limit.window,
                current=usage,
                requested=requested,
                limit=limit.hard_limit,
                warn_at=limit.soft_limit,
                outcome=OUTCOME_BLOCKED,
            )
        if projected > limit.soft_limit:
            return EnforcerDecision(
                tenant_id=tenant_id,
                kind=KIND_QUOTA,
                decision=DECISION_WARN,
                reason=(
                    f"soft {limit.resource} quota exceeded: {projected:g} > "
                    f"softLimit {limit.soft_limit:g} (below hardLimit)"
                ),
                code=f"quota.soft.{limit.resource}.exceeded",
                agent_id=agent_id,
                resource=limit.resource,
                window=limit.window,
                current=usage,
                requested=requested,
                limit=limit.hard_limit,
                warn_at=limit.soft_limit,
            )
        return EnforcerDecision(
            tenant_id=tenant_id,
            kind=KIND_QUOTA,
            decision=DECISION_ALLOW,
            reason=f"within {limit.resource} quota ({projected:g} <= softLimit)",
            code=f"quota.{limit.resource}.allow",
            agent_id=agent_id,
            resource=limit.resource,
            window=limit.window,
            current=usage,
            requested=requested,
            limit=limit.hard_limit,
            warn_at=limit.soft_limit,
        )


def quota_status(usage: float, soft_limit: float, hard_limit: float) -> str:
    """Map current usage onto the ok/warning/exceeded status ladder."""
    if hard_limit > 0 and usage >= hard_limit:
        return QUOTA_STATUS_EXCEEDED
    if usage >= soft_limit:
        return QUOTA_STATUS_WARNING
    return QUOTA_STATUS_OK


def _default_requested(resource: str, requested_tokens: float, requested_storage: float) -> float:
    """Projected increment for a resource in an all-resources check."""
    if resource == RESOURCE_TOKENS:
        return float(requested_tokens)
    if resource == RESOURCE_STORAGE:
        return float(requested_storage)
    return 1.0  # requests / concurrency project this one call


#: Order used to pick the most severe of several quota decisions.
_SEVERITY_ORDER = {DECISION_ALLOW: 0, DECISION_WARN: 1, DECISION_BLOCK: 2}


def _severity(decision: EnforcerDecision) -> int:
    return _SEVERITY_ORDER.get(decision.decision, 0)
