"""telemetry/observability — breach detection + alerts (issue #32, phase 5).

Implements the **outcome-not-liveness** doctrine cannibalized from the
leaderboard ``loop-outcome-check.sh`` ("HEALTHY IS NOT WORKING"): alerts fire
on actual outcome metrics computed from the telemetry store, never on
heartbeat presence.  A tenant with **no data in the SLO window** is treated as
an alertable gap (silence is never assumed healthy), not as a passing check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from telemetry.observability.model import now_utc_iso
from telemetry.observability.slos import (
    VERDICT_AT_RISK,
    VERDICT_BREACHED,
    VERDICT_NO_DATA,
    SloResult,
)

SEV_WARNING = "warning"
SEV_CRITICAL = "critical"
SEVERITIES = frozenset({SEV_WARNING, SEV_CRITICAL})

#: Human reason codes for each alert.
REASON_BREACHED = "slo_target_breached"
REASON_AT_RISK = "error_budget_at_risk"
REASON_MISSING_WINDOW = "no_window_data"          # outcome-not-liveness gap
REASON_NO_ATTEMPTS = "no_serve_attempts"          # data present, no outcomes
REASON_COST_OVER_BUDGET = "cost_over_budget"


@dataclass(frozen=True)
class Alert:
    """One alert raised from an SLO result (an outcome, not a heartbeat)."""

    tenant_id: str
    slo: str
    kind: str
    severity: str
    reason: str
    verdict: str
    window_start: str
    window_end: str
    fired_at: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenantId": self.tenant_id,
            "slo": self.slo,
            "kind": self.kind,
            "severity": self.severity,
            "reason": self.reason,
            "verdict": self.verdict,
            "windowStart": self.window_start,
            "windowEnd": self.window_end,
            "firedAt": self.fired_at,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class AlertPolicy:
    """Maps an SLO verdict to a severity (cannibalized breach_detector shape).

    Defaults: BREACHED -> critical, AT_RISK -> warning, NO_DATA -> critical.
    A missed SLO window is the most dangerous signal (a silently down tenant
    must page, not whisper).
    """

    breached_severity: str = SEV_CRITICAL
    at_risk_severity: str = SEV_WARNING
    no_data_severity: str = SEV_CRITICAL

    def severity_for(self, verdict: str) -> str:
        if verdict == VERDICT_BREACHED:
            return self.breached_severity
        if verdict == VERDICT_AT_RISK:
            return self.at_risk_severity
        if verdict == VERDICT_NO_DATA:
            return self.no_data_severity
        raise ValueError(f"no severity mapped for verdict {verdict!r}")

    def reason_for(self, result: SloResult) -> str:
        if result.verdict == VERDICT_BREACHED:
            return (
                REASON_COST_OVER_BUDGET
                if result.kind == "cost"
                else REASON_BREACHED
            )
        if result.verdict == VERDICT_AT_RISK:
            return REASON_AT_RISK
        if result.verdict == VERDICT_NO_DATA:
            return (
                REASON_MISSING_WINDOW
                if result.missed_window
                else REASON_NO_ATTEMPTS
            )
        raise ValueError(f"no alert for OK verdict {result.verdict!r}")


class BreachDetector:
    """Detects alerts from a list of :class:`SloResult` objects."""

    def __init__(self, policy: Optional[AlertPolicy] = None) -> None:
        self.policy = policy or AlertPolicy()

    def detect(self, results: list[SloResult]) -> list[Alert]:
        """Return one alert per non-OK result, in result order."""
        fired_at = now_utc_iso()
        alerts: list[Alert] = []
        for result in results:
            if result.verdict not in (VERDICT_BREACHED, VERDICT_AT_RISK, VERDICT_NO_DATA):
                continue
            detail: dict[str, Any] = {"target": result.definition.target}
            if result.measured_ratio is not None:
                detail["measuredRatio"] = result.measured_ratio
            if result.measured_ms is not None:
                detail["measuredMs"] = result.measured_ms
            if result.budget_consumed_ratio is not None:
                detail["budgetConsumedRatio"] = round(result.budget_consumed_ratio, 6)
            if result.attempts:
                detail["attempts"] = result.attempts
            if result.violation_count:
                detail["violations"] = result.violation_count
            detail["spentUsd"] = round(result.spent_usd, 6)
            alerts.append(
                Alert(
                    tenant_id=result.tenant_id,
                    slo=result.name,
                    kind=result.kind,
                    severity=self.policy.severity_for(result.verdict),
                    reason=self.policy.reason_for(result),
                    verdict=result.verdict,
                    window_start=result.window_start_iso,
                    window_end=result.window_end_iso,
                    fired_at=fired_at,
                    detail=detail,
                )
            )
        return alerts

    def group_by_tenant(self, alerts: list[Alert]) -> dict[str, list[Alert]]:
        """Group alerts by tenant (insertion order preserved)."""
        grouped: dict[str, list[Alert]] = {}
        for alert in alerts:
            grouped.setdefault(alert.tenant_id, []).append(alert)
        return grouped

    def has_alerts(self, alerts: list[Alert]) -> bool:
        """True when any alert fired (a red gate signal)."""
        return len(alerts) > 0


__all__ = [
    "SEV_WARNING",
    "SEV_CRITICAL",
    "SEVERITIES",
    "REASON_BREACHED",
    "REASON_AT_RISK",
    "REASON_MISSING_WINDOW",
    "REASON_NO_ATTEMPTS",
    "REASON_COST_OVER_BUDGET",
    "Alert",
    "AlertPolicy",
    "BreachDetector",
]
