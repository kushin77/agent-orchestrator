"""telemetry/budgets — composed pre-dispatch call check (issue #34).

The enforcement contract the gateway/engine lanes call before a model call:
one ``preflight()`` that runs the three rails in the safe order and returns
a single verdict the caller either lets through or refuses.

Order matters:

1. **Kill switch** — a platform-wide pause refuses every non-critical call
   first (spend stops instantly regardless of headroom).
2. **Quota** — hard soft/hard resource quotas over calls/tokens/concurrency/
   storage (soft warns, hard refuses).
3. **Budget** — per-tenant (and per-vendor/model) cost/token budget with the
   warn -> block ladder over the durable metering feed.

The returned :class:`PreflightResult` carries the aggregate decision (the
most severe across rails), each rail's individual decision, and the metering
``outcome`` the caller should attach when the call is refused (``refused`` /
``blocked`` / ``budget_exceeded`` — all in the issue-#33
``NON_BILLABLE_OUTCOMES`` set, so a refused call is never metered).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from telemetry.budgets.budget import BudgetEnforcer
from telemetry.budgets.killswitch import KillSwitchController
from telemetry.budgets.model import (
    DECISION_ALLOW,
    EnforcerDecision,
    KIND_BUDGET,
    KIND_KILL_SWITCH,
    KIND_QUOTA,
)
from telemetry.budgets.quota import QuotaEnforcer

_SEVERITY = {
    DECISION_ALLOW: 0,
    "would_warn": 1,
    "warn": 2,
    "would_block": 3,
    "block": 4,
    "refuse": 5,
}


@dataclass(frozen=True)
class PreflightResult:
    """The composed pre-dispatch verdict across all three rails."""

    tenant_id: str
    decisions: List[EnforcerDecision]  # kill switch, quota, budget order

    @property
    def decision(self) -> str:
        """The aggregate decision (most severe rail wins)."""
        if not self.decisions:
            return DECISION_ALLOW
        return max(
            (d.decision for d in self.decisions), key=lambda s: _SEVERITY.get(s, 0)
        )

    @property
    def allowed(self) -> bool:
        return self.decision in {DECISION_ALLOW, "warn", "would_warn", "would_block"}

    @property
    def outcome(self) -> Optional[str]:
        """The metering non-billable outcome for a refused call (else None)."""
        for d in self.decisions:
            if d.decision in {"block", "refuse"} and d.outcome:
                return d.outcome
        return None

    def rail(self, kind: str) -> Optional[EnforcerDecision]:
        for d in self.decisions:
            if d.kind == kind:
                return d
        return None

    def to_dict(self) -> dict:
        return {
            "tenantId": self.tenant_id,
            "decision": self.decision,
            "allowed": self.allowed,
            "outcome": self.outcome,
            "rails": [d.to_dict() for d in self.decisions],
        }


def preflight(
    tenant_id: str,
    *,
    agent_id: Optional[str] = None,
    vendor: Optional[str] = None,
    model: Optional[str] = None,
    service: Optional[str] = None,
    critical: bool = False,
    requested_cost_usd: float = 0.0,
    requested_tokens: int = 0,
    day: Optional[str] = None,
    month: Optional[str] = None,
    killswitch: Optional[KillSwitchController] = None,
    quota: Optional[QuotaEnforcer] = None,
    budget: Optional[BudgetEnforcer] = None,
) -> PreflightResult:
    """Run the composed pre-dispatch check over the three injected rails.

    Rails are injected (the CLI wires the default config; the gateway/engine
    lanes wire their live ledger/probe).  A rail not supplied is skipped.
    ``day``/``month`` pin the evaluation buckets for reproducible checks.
    """
    decisions: List[EnforcerDecision] = []
    if killswitch is not None:
        decisions.append(
            killswitch.check_call(
                tenant_id, agent_id=agent_id, service=service, critical=critical
            )
        )
    if quota is not None:
        decisions.append(
            quota.check(
                tenant_id,
                agent_id=agent_id,
                requested_tokens=float(requested_tokens),
                day=day,
            )
        )
    if budget is not None:
        decisions.append(
            budget.check(
                tenant_id,
                agent_id=agent_id,
                vendor=vendor,
                model=model,
                requested_cost_usd=requested_cost_usd,
                requested_tokens=requested_tokens,
                day=day,
                month=month,
            )
        )
    return PreflightResult(tenant_id=tenant_id, decisions=decisions)


def first_blocking_rail(result: PreflightResult) -> Optional[EnforcerDecision]:
    """The rail that refused the call (kill switch/quota/budget), or None.

    Walks in rail order (kill switch first) so the *reason* surfaced to a
    refused call is the most fundamental one — not the loudest.
    """
    for d in result.decisions:
        if d.decision in {"block", "refuse"}:
            return d
    return None
