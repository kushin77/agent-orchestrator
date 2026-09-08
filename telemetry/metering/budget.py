"""telemetry/metering — observe->enforce daily token budget toggle (issue #33).

The safe-rollout control for per-tenant daily token budgets: by default the
budget **observes** (computes and reports what *would* be blocked) and never
blocks; a tenant (or the global default) can be flipped to **enforce** only
after a recorded track record exists — no blocking control ships before a
recorded track record (fleet golden rule: new controls default OFF).

The daily token total is read from the durable usage store (the same
append-only ledger the rollups read), so the budget is correct across
process restarts and instances sharing the store — a fresh instance's
"current usage" is never zero just because it started after the spend.

This lane owns the toggle + verdict; wiring the verdict into an actual
gateway refusal is the consuming layer's job (the gateway limits lane,
issue #19, already owns request-path blocking; the phase-5 budgets lane,
issue #34, consumes these daily totals).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import yaml

from telemetry.metering.model import day_bucket, now_utc_iso
from telemetry.metering.report import UsageReporter

BUDGET_MODE_OBSERVE = "observe"
BUDGET_MODE_ENFORCE = "enforce"
BUDGET_MODES = frozenset({BUDGET_MODE_OBSERVE, BUDGET_MODE_ENFORCE})

DECISION_ALLOW = "allow"
DECISION_WOULD_BLOCK = "would_block"
DECISION_BLOCK = "block"

DEFAULT_BUDGET_CONFIG = Path(__file__).resolve().parent / "config" / "budgets.yaml"


@dataclass(frozen=True)
class BudgetPolicy:
    """One tenant's daily token budget + rollout mode."""

    tenant_id: str
    daily_token_limit: int
    mode: str = BUDGET_MODE_OBSERVE

    def __post_init__(self) -> None:
        if self.daily_token_limit <= 0:
            raise ValueError("daily_token_limit must be a positive integer")
        if self.mode not in BUDGET_MODES:
            raise ValueError(f"unknown budget mode: {self.mode!r}")


@dataclass(frozen=True)
class BudgetVerdict:
    """The result of checking one tenant's projected daily usage."""

    tenant_id: str
    mode: str
    current_tokens: int
    requested_tokens: int
    daily_token_limit: Optional[int]
    decision: str
    reason: str

    @property
    def projected_tokens(self) -> int:
        return self.current_tokens + self.requested_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenantId": self.tenant_id,
            "mode": self.mode,
            "currentTokens": self.current_tokens,
            "requestedTokens": self.requested_tokens,
            "projectedTokens": self.projected_tokens,
            "dailyTokenLimit": self.daily_token_limit,
            "decision": self.decision,
            "reason": self.reason,
        }


def load_budget_config(path: Path = DEFAULT_BUDGET_CONFIG) -> Dict[str, BudgetPolicy]:
    """Load per-tenant budget policies from the YAML config (fail closed)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path}: budget config root must be a mapping")
    policies: Dict[str, BudgetPolicy] = {}
    for entry in raw.get("policies", []) or []:
        if not isinstance(entry, Mapping):
            raise ValueError(f"{path}: each policy must be a mapping")
        tenant_id = str(entry.get("tenantId") or "")
        if not tenant_id:
            raise ValueError(f"{path}: policy missing tenantId")
        policies[tenant_id] = BudgetPolicy(
            tenant_id=tenant_id,
            daily_token_limit=int(entry.get("dailyTokenLimit") or 0),
            mode=str(entry.get("mode") or BUDGET_MODE_OBSERVE),
        )
    return policies


class DailyTokenBudget:
    """Per-tenant daily token budget with an observe->enforce toggle.

    ``reporter`` supplies the durable daily token totals from the usage
    store; ``policies`` maps tenant id -> ``BudgetPolicy`` (loadable from
    YAML).  ``default_mode`` and ``default_limit`` apply to tenants without
    an explicit policy; with no limit at all the budget is unlimited and
    always allows (still reported as observe).
    """

    def __init__(
        self,
        reporter: UsageReporter,
        policies: Optional[Mapping[str, BudgetPolicy]] = None,
        *,
        default_mode: str = BUDGET_MODE_OBSERVE,
        default_limit: Optional[int] = None,
    ) -> None:
        if default_mode not in BUDGET_MODES:
            raise ValueError(f"unknown default budget mode: {default_mode!r}")
        if default_limit is not None and default_limit <= 0:
            raise ValueError("default_limit must be a positive integer")
        self.reporter = reporter
        self.policies: Dict[str, BudgetPolicy] = dict(policies or {})
        self.default_mode = default_mode
        self.default_limit = default_limit

    def policy_for(self, tenant_id: str) -> Optional[BudgetPolicy]:
        """The effective policy for a tenant, or ``None`` when unlimited.

        An explicit policy wins; otherwise a default policy is synthesized
        from ``default_mode``/``default_limit`` only when a limit exists.
        ``None`` means the tenant has no daily token budget at all.
        """
        policy = self.policies.get(tenant_id)
        if policy is not None:
            return policy
        if self.default_limit is None:
            return None
        return BudgetPolicy(
            tenant_id=tenant_id,
            daily_token_limit=self.default_limit,
            mode=self.default_mode,
        )

    def current_daily_tokens(self, tenant_id: str, day: Optional[str] = None) -> int:
        """Billable tokens consumed by ``tenant_id`` on a UTC day.

        ``day`` defaults to today.  Reads from the durable store (never a
        per-process counter), so the total survives restarts and is shared
        across instances pointing at the same store.
        """
        day = day or day_bucket(now_utc_iso())
        rows = self.reporter.tenant_daily(tenant_id)
        total = 0
        for bucket, agg in rows.items():
            if bucket == day:
                total += agg.total_tokens
        return total

    def check(
        self,
        tenant_id: str,
        requested_tokens: int = 0,
        day: Optional[str] = None,
    ) -> BudgetVerdict:
        """Project today's usage and return an allow / would-block / block.

        In ``observe`` mode (the default) an over-budget tenant gets
        ``would_block`` — reported, never refused.  In ``enforce`` mode it
        gets ``block`` — the consuming caller must refuse the call.  A tenant
        with no limit is ``allow`` with an explicit reason.
        """
        policy = self.policy_for(tenant_id)
        current = self.current_daily_tokens(tenant_id, day=day)
        if policy is None:
            return BudgetVerdict(
                tenant_id=tenant_id,
                mode=self.default_mode,
                current_tokens=current,
                requested_tokens=requested_tokens,
                daily_token_limit=None,
                decision=DECISION_ALLOW,
                reason="no daily token budget configured for tenant",
            )
        projected = current + requested_tokens
        over = projected > policy.daily_token_limit
        if policy.mode == BUDGET_MODE_ENFORCE:
            if over:
                return BudgetVerdict(
                    tenant_id=tenant_id,
                    mode=policy.mode,
                    current_tokens=current,
                    requested_tokens=requested_tokens,
                    daily_token_limit=policy.daily_token_limit,
                    decision=DECISION_BLOCK,
                    reason=(
                        f"enforce daily budget exceeded: {projected} > "
                        f"{policy.daily_token_limit}"
                    ),
                )
            return BudgetVerdict(
                tenant_id=tenant_id,
                mode=policy.mode,
                current_tokens=current,
                requested_tokens=requested_tokens,
                daily_token_limit=policy.daily_token_limit,
                decision=DECISION_ALLOW,
                reason=f"within daily budget ({projected} <= {policy.daily_token_limit})",
            )
        # observe mode: report what would be blocked, never block.
        if over:
            return BudgetVerdict(
                tenant_id=tenant_id,
                mode=policy.mode,
                current_tokens=current,
                requested_tokens=requested_tokens,
                daily_token_limit=policy.daily_token_limit,
                decision=DECISION_WOULD_BLOCK,
                reason=(
                    f"observe: projected {projected} > {policy.daily_token_limit} "
                    "(would block in enforce mode)"
                ),
            )
        return BudgetVerdict(
            tenant_id=tenant_id,
            mode=policy.mode,
            current_tokens=current,
            requested_tokens=requested_tokens,
            daily_token_limit=policy.daily_token_limit,
            decision=DECISION_ALLOW,
            reason=f"within daily budget ({projected} <= {policy.daily_token_limit})",
        )
