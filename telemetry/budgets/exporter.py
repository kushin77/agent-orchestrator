"""telemetry/budgets — machine-readable budget/SLO state exporter (issue #34).

The export surface for tenant dashboards and alerting: a single offline
snapshot of the operational safety rails — kill-switch state, per-tenant
budget positions, per-tenant quota statuses, the SLO feed (CONSUMED from
telemetry/observability, issue #32) and an audit summary — written as JSON
(optionally a compact JSONL event feed) for dashboards/alerts to consume.

The exporter is a *formatter*: it reads current state off the injected
enforcers/ledger/audit and never makes policy decisions itself.  SLO verdict
vocabulary (``OK``/``AT_RISK``/``BREACHED``/``NO_DATA`` and the
:class:`SloResult` shape) is consumed from ``telemetry.observability.slos``,
never redefined.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from telemetry.budgets.audit import BudgetAuditStore
from telemetry.budgets.budget import BudgetEnforcer, BudgetLimit, TenantBudgetPolicy
from telemetry.budgets.killswitch import KillSwitchController
from telemetry.budgets.ledger import SpendLedger
from telemetry.budgets.model import now_utc_iso
from telemetry.budgets.quota import QuotaEnforcer


def _position(current: float, limit: float, warn_at: float) -> str:
    """Map current usage onto ok/warning/exceeded for a budget limit."""
    if limit > 0 and current >= limit:
        return "exceeded"
    if current >= warn_at:
        return "warning"
    return "ok"


class BudgetStateExporter:
    """Builds the machine-readable budget/quota/SLO/audit state snapshot.

    ``ledger`` supplies current durable usage; the enforcers supply policy
    and (for quota) live probe status; ``killswitch`` supplies the global
    pause state; ``audit`` supplies the decision summary.
    """

    def __init__(
        self,
        ledger: SpendLedger,
        *,
        killswitch: Optional[KillSwitchController] = None,
        budget: Optional[BudgetEnforcer] = None,
        quota: Optional[QuotaEnforcer] = None,
        audit: Optional[BudgetAuditStore] = None,
    ) -> None:
        self.ledger = ledger
        self.killswitch = killswitch
        self.budget = budget
        self.quota = quota
        self.audit = audit

    # ------------------------------------------------------------------ #
    def kill_switch_state(self) -> Dict[str, Any]:
        if self.killswitch is None:
            return {"globalPause": False, "configured": False}
        state = self.killswitch.state
        return {
            "globalPause": state.global_pause,
            "reason": state.reason,
            "pausedBy": state.paused_by,
            "timestamp": state.timestamp,
            "exemptServices": list(state.exempt_services),
        }

    def tenant_budget_state(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Current budget position for one tenant (current vs limit vs warn)."""
        if self.budget is None:
            return None
        policy = self.budget.policy_for(tenant_id)
        if policy is None:
            return None
        limits: Dict[str, Any] = {}
        if policy.cost_limit is not None:
            current = _current_for(self.ledger, policy.cost_limit, tenant_id)
            limits["costUsd"] = _limit_state(
                policy.cost_limit, current, "cost"
            )
        if policy.token_limit is not None:
            current = float(self.ledger.daily_tokens(tenant_id))
            limits["tokens"] = _limit_state(
                policy.token_limit, current, "tokens"
            )
        caps = []
        for cap in policy.vendor_caps:
            current = float(self.ledger.vendor_monthly_cost(tenant_id, cap.vendor))
            caps.append(
                {
                    "vendor": cap.vendor,
                    "currentUsd": round(current, 8),
                    "limitUsd": round(cap.limit_usd, 8),
                    "warnAtUsd": round(cap.warn_at, 8),
                    "position": _position(current, cap.limit_usd, cap.warn_at),
                }
            )
        return {
            "tenantId": tenant_id,
            "mode": policy.mode,
            "limits": limits,
            "vendorCaps": caps,
        }

    def tenant_quota_state(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Current quota status for one tenant across all resources."""
        if self.quota is None:
            return None
        policy = self.quota.policy_for(tenant_id)
        if policy is None:
            return None
        resources: Dict[str, Any] = {}
        for resource in sorted(policy.limits):
            limit = policy.limit_for(resource)
            if limit is None:
                continue
            usage = self.quota.current_usage(tenant_id, resource)
            status = self.quota.status(tenant_id, resource)
            resources[resource] = {
                "current": round(usage, 8),
                "softLimit": round(limit.soft_limit, 8),
                "hardLimit": round(limit.hard_limit, 8),
                "window": limit.window,
                "status": status,
            }
        return {"tenantId": tenant_id, "plan": policy.plan, "resources": resources}

    # ------------------------------------------------------------------ #
    def tenant_ids(self) -> List[str]:
        """The tenant universe (union of budget + quota policy tenants)."""
        ids = set()
        if self.budget is not None:
            ids.update(self.budget.policies)
        if self.quota is not None:
            ids.update(self.quota.policies)
        return sorted(ids)

    def snapshot(
        self,
        *,
        slo_results: Optional[Sequence[Any]] = None,
    ) -> Dict[str, Any]:
        """Build the full machine-readable state export.

        ``slo_results`` is an optional sequence of
        ``telemetry.observability.slos.SloResult`` objects (issue #32
        vocabulary) — the SLO feed for tenant dashboards/alerting.
        """
        tenants: Dict[str, Any] = {}
        for tenant_id in self.tenant_ids():
            state: Dict[str, Any] = {}
            budget_state = self.tenant_budget_state(tenant_id)
            quota_state = self.tenant_quota_state(tenant_id)
            if budget_state is not None:
                state["budget"] = budget_state
            if quota_state is not None:
                state["quotas"] = quota_state
            tenants[tenant_id] = state
        return {
            "generatedAt": now_utc_iso(),
            "killSwitch": self.kill_switch_state(),
            "tenants": tenants,
            "slos": [slo_row(r) for r in (slo_results or [])],
            "audit": self.audit_summary(),
        }

    # ------------------------------------------------------------------ #
    def audit_summary(self) -> Dict[str, Any]:
        """Decision counts + the most recent audit events (alerting feed)."""
        if self.audit is None:
            return {"configured": False}
        events = self.audit.read()
        by_decision: Dict[str, int] = {}
        by_code: Dict[str, int] = {}
        for event in events:
            by_decision[event.decision] = by_decision.get(event.decision, 0) + 1
            by_code[event.code] = by_code.get(event.code, 0) + 1
        recent = [e.to_dict() for e in events[-20:]]
        return {
            "configured": True,
            "totalEvents": len(events),
            "byDecision": by_decision,
            "byCode": by_code,
            "recent": recent,
        }

    # ------------------------------------------------------------------ #
    def write_json(self, path: Path, *, slo_results: Optional[Sequence[Any]] = None) -> Path:
        """Write the snapshot to a JSON file (dashboards/alerts consume it)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.snapshot(slo_results=slo_results), indent=2) + "\n",
            encoding="utf-8",
        )
        return path


def _current_for(ledger: SpendLedger, limit: BudgetLimit, tenant_id: str) -> float:
    if limit.window == "day":
        return float(ledger.daily_cost(tenant_id))
    return float(ledger.monthly_cost(tenant_id))


def _limit_state(limit: BudgetLimit, current: float, kind: str) -> Dict[str, Any]:
    """Render one budget limit's current/limit/warnAt/position.

    Unit-clear keys: cost limits read ``currentUsd``/``limitUsd``/
    ``warnAtUsd``; token limits read ``currentTokens``/``limitTokens``/
    ``warnAtTokens`` — never a bare unitless ``limit``.
    """
    unit = "Usd" if kind == "cost" else "Tokens"
    return {
        "current" + unit: round(current, 8),
        "limit" + unit: round(limit.limit, 8),
        "warnAt" + unit: round(limit.warn_at, 8),
        "window": limit.window,
        "position": _position(current, limit.limit, limit.warn_at),
    }


def slo_row(result: Any) -> Dict[str, Any]:
    """Render one issue-#32 SLO result into the export feed shape.

    Consumes the observability SLO vocabulary verbatim (verdicts + fields) —
    never redefines it.  Accepts either a ``telemetry.observability.slos``
    ``SloResult`` object or a serialized dict in its ``to_dict()`` shape (so
    a JSON feed produced by the observability lane can be re-exported
    without reconstructing objects).
    """
    if isinstance(result, dict):
        return _slo_row_from_dict(result)
    return {
        "tenantId": result.tenant_id,
        "slo": result.name,
        "kind": result.kind,
        "windowStart": result.window_start_iso,
        "windowEnd": result.window_end_iso,
        "verdict": result.verdict,
        "isOk": result.is_ok,
        "missedWindow": result.missed_window,
        "attempts": result.attempts,
        "goodCount": result.good_count,
        "badCount": result.bad_count,
        "violationCount": result.violation_count,
        "measuredRatio": result.measured_ratio,
        "measuredMs": result.measured_ms,
        "spentUsd": round(result.spent_usd, 6),
        "budgetConsumedRatio": result.budget_consumed_ratio,
        "target": result.definition.target,
    }


def _slo_row_from_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a serialized SloResult dict (``to_dict()`` shape) to a row."""
    return {
        "tenantId": payload.get("tenantId"),
        "slo": payload.get("slo"),
        "kind": payload.get("kind"),
        "windowStart": payload.get("windowStart"),
        "windowEnd": payload.get("windowEnd"),
        "verdict": payload.get("verdict"),
        "isOk": payload.get("verdict") == "OK",
        "missedWindow": payload.get("missedWindow"),
        "attempts": payload.get("attempts"),
        "goodCount": payload.get("goodCount"),
        "badCount": payload.get("badCount"),
        "violationCount": payload.get("violationCount"),
        "measuredRatio": payload.get("measuredRatio"),
        "measuredMs": payload.get("measuredMs"),
        "spentUsd": payload.get("spentUsd"),
        "budgetConsumedRatio": payload.get("budgetConsumedRatio"),
        "target": payload.get("target"),
    }
