"""telemetry/budgets — spend alerts with warning/alert thresholds (issue #341).

The alerting rail of the budget lane: per tenant and per limit (cost, tokens,
per-vendor), a **warning** threshold and an **alert** threshold produce a
machine-readable :class:`SpendAlert` whose severity is ``none``, ``warning`` or
``alert``. An alert *fires* when the measured spend reaches the alert
threshold — the breach the single-pane report and any downstream pager watch.

Cap semantics (issue #341): a **soft** cap is a target — crossing it fires the
alert but never refuses a call; a **hard** cap is a limit — crossing it fires
the same alert *and* the enforcer refuses. Alerting is therefore identical for
both caps; only ``blocks`` differs. Each alert carries ``cap``, ``mode`` and
``blocks`` so a consumer never has to infer enforcement from severity.

Honesty (the point of this module's no-data path): a tenant the durable feed
has never metered is **not** at zero spend — it is *unknown*. When the ledger
reports no data for the tenant every limit yields a ``NO_DATA`` verdict with
``current=None`` (never a fabricated ``0.0``, which would read as "no spend,
all good") and a reason naming the probe. Verdicts (``OK`` / ``AT_RISK`` /
``BREACHED`` / ``NO_DATA``) are CONSUMED from ``telemetry/observability``
(issue #32, ``slos.py``) — this lane does not restate that vocabulary.

Pure decision logic: every figure comes from the injected spend ledger
(``ledger.py``) and the injected policies; no I/O, no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from telemetry.budgets.budget import TenantBudgetPolicy, VendorBudgetCap
from telemetry.budgets.ledger import SpendLedger
from telemetry.budgets.model import (
    CAP_HARD,
    MODE_ENFORCE,
    MODES,
    WINDOWS,
)
from telemetry.observability.slos import (
    VERDICT_AT_RISK,
    VERDICT_BREACHED,
    VERDICT_NO_DATA,
    VERDICT_OK,
)

#: Alert severities, weakest to strongest. ``alert`` is the breach that fires.
SEVERITY_NONE = "none"
SEVERITY_WARNING = "warning"
SEVERITY_ALERT = "alert"
SEVERITIES = (SEVERITY_NONE, SEVERITY_WARNING, SEVERITY_ALERT)

#: Which rail produced the alert.
KIND_COST = "cost"
KIND_TOKENS = "tokens"
KIND_VENDOR = "vendor"

#: Units the figures are expressed in (never guess a unit from a bare number).
UNIT_USD = "usd"
UNIT_TOKENS = "tokens"

#: Verdict severity order used to roll several limits up into one verdict.
#: A real breach outranks unknown (NO_DATA), which outranks a known OK — an
#: unmeasured tenant must never be summarised as "fine".
_VERDICT_RANK = {
    VERDICT_OK: 0,
    VERDICT_NO_DATA: 1,
    VERDICT_AT_RISK: 2,
    VERDICT_BREACHED: 3,
}


@dataclass(frozen=True)
class SpendAlert:
    """One limit's alert state for one tenant.

    ``current`` is ``None`` — never 0 — when the ledger holds no data: an
    unmeasured tenant has an unknown spend, not a zero one.
    """

    tenant_id: str
    kind: str
    code: str
    severity: str
    verdict: str
    unit: str
    window: str
    message: str
    current: Optional[float] = None
    limit: Optional[float] = None
    warn_at: Optional[float] = None
    alert_at: Optional[float] = None
    cap: Optional[str] = None
    mode: Optional[str] = None
    vendor: Optional[str] = None

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown alert severity: {self.severity!r}")
        if self.verdict not in _VERDICT_RANK:
            raise ValueError(f"unknown alert verdict: {self.verdict!r}")

    @property
    def fires(self) -> bool:
        """True when this alert is the breach an operator must act on."""
        return self.severity == SEVERITY_ALERT

    @property
    def blocks(self) -> bool:
        """True when the breach is enforced (hard cap + enforce mode).

        Only a fired breach can block: a NO_DATA verdict is not an enforcement
        decision, so it never claims the rail is refusing calls.
        """
        return (
            self.fires
            and self.cap == CAP_HARD
            and self.mode == MODE_ENFORCE
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to the camelCase machine-readable shape."""
        return {
            "tenantId": self.tenant_id,
            "kind": self.kind,
            "vendor": self.vendor,
            "code": self.code,
            "severity": self.severity,
            "verdict": self.verdict,
            "unit": self.unit,
            "window": self.window,
            "current": None if self.current is None else round(self.current, 8),
            "limit": None if self.limit is None else round(self.limit, 8),
            "warnAt": None if self.warn_at is None else round(self.warn_at, 8),
            "alertAt": None if self.alert_at is None else round(self.alert_at, 8),
            "cap": self.cap,
            "mode": self.mode,
            "fires": self.fires,
            "blocks": self.blocks,
            "message": self.message,
        }


@dataclass(frozen=True)
class TenantSpendAlerts:
    """Every limit's alert state for one tenant, plus the rolled-up verdict."""

    tenant_id: str
    mode: str
    has_data: bool
    alerts: Tuple[SpendAlert, ...] = ()

    @property
    def verdict(self) -> str:
        """The most urgent verdict across the tenant's limits."""
        if not self.alerts:
            return VERDICT_NO_DATA if not self.has_data else VERDICT_OK
        return max((a.verdict for a in self.alerts), key=lambda v: _VERDICT_RANK[v])

    @property
    def fired(self) -> Tuple[SpendAlert, ...]:
        """The alerts that fired (breaches only)."""
        return tuple(alert for alert in self.alerts if alert.fires)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenantId": self.tenant_id,
            "mode": self.mode,
            "hasData": self.has_data,
            "verdict": self.verdict,
            "fired": [alert.to_dict() for alert in self.fired],
            "alerts": [alert.to_dict() for alert in self.alerts],
        }


class SpendAlertEvaluator:
    """Evaluates the warning/alert thresholds of every budget limit.

    ``ledger`` supplies durable current spend; ``policies`` maps tenant id ->
    ``TenantBudgetPolicy``.  ``data_probe`` answers "does the ledger hold any
    billable record for this tenant?" — it defaults to the ledger's own
    ``has_data`` capability (``ledger.py``), and when neither is available the
    ledger is assumed data-bearing (a plain figure then means what it says).
    """

    def __init__(
        self,
        ledger: SpendLedger,
        policies: Optional[Mapping[str, TenantBudgetPolicy]] = None,
        *,
        data_probe: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.ledger = ledger
        self.policies: Dict[str, TenantBudgetPolicy] = dict(policies or {})
        probe = data_probe
        if probe is None:
            capability = getattr(ledger, "has_data", None)
            if callable(capability):
                probe = capability
        self._data_probe = probe

    # ------------------------------------------------------------------ #
    def has_data(self, tenant_id: str) -> bool:
        """Whether the ledger holds metered data for the tenant.

        With no probe configured the answer is ``True``: absent a capability to
        prove otherwise, a ledger figure is taken at face value.
        """
        if self._data_probe is None:
            return True
        return bool(self._data_probe(tenant_id))

    def policy_for(self, tenant_id: str) -> Optional[TenantBudgetPolicy]:
        """The tenant's budget policy, or ``None`` when none is declared."""
        return self.policies.get(tenant_id)

    def tenant_ids(self) -> List[str]:
        """Every tenant the evaluator holds a policy for (sorted)."""
        return sorted(self.policies)

    # ------------------------------------------------------------------ #
    def evaluate(
        self,
        tenant_id: str,
        *,
        day: Optional[str] = None,
        month: Optional[str] = None,
    ) -> TenantSpendAlerts:
        """Evaluate every declared limit for one tenant.

        ``day``/``month`` pin the evaluation buckets (``YYYY-MM-DD`` /
        ``YYYY-MM``) for reproducible offline checks; they default to the
        current UTC day/month.  A tenant with no policy has no limits and
        therefore no alerts.
        """
        policy = self.policy_for(tenant_id)
        if policy is None:
            return TenantSpendAlerts(
                tenant_id=tenant_id,
                mode="",
                has_data=self.has_data(tenant_id),
                alerts=(),
            )
        data = self.has_data(tenant_id)
        alerts: List[SpendAlert] = []
        if policy.cost_limit is not None:
            limit = policy.cost_limit
            current = self._cost(tenant_id, limit.window, month=month)
            alerts.append(
                self._alert(
                    tenant_id=tenant_id,
                    kind=KIND_COST,
                    base="budget.cost",
                    unit=UNIT_USD,
                    window=limit.window,
                    current=current,
                    limit=float(limit.limit),
                    warn_at=float(limit.warn_at),
                    alert_at=float(limit.alert_at),
                    cap=limit.cap,
                    mode=policy.mode,
                    data=data,
                )
            )
        if policy.token_limit is not None:
            limit = policy.token_limit
            current = float(self._tokens(tenant_id, day=day))
            alerts.append(
                self._alert(
                    tenant_id=tenant_id,
                    kind=KIND_TOKENS,
                    base="budget.tokens",
                    unit=UNIT_TOKENS,
                    window=limit.window,
                    current=current,
                    limit=float(limit.limit),
                    warn_at=float(limit.warn_at),
                    alert_at=float(limit.alert_at),
                    cap=limit.cap,
                    mode=policy.mode,
                    data=data,
                )
            )
        for cap in policy.vendor_caps:
            current = float(
                self.ledger.vendor_monthly_cost(tenant_id, cap.vendor, month=month)
            )
            alerts.append(
                self._vendor_alert(
                    tenant_id=tenant_id,
                    cap=cap,
                    current=current,
                    mode=policy.mode,
                    data=data,
                )
            )
        return TenantSpendAlerts(
            tenant_id=tenant_id,
            mode=policy.mode,
            has_data=data,
            alerts=tuple(alerts),
        )

    def alerts(
        self,
        tenant_id: Optional[str] = None,
        *,
        day: Optional[str] = None,
        month: Optional[str] = None,
    ) -> List[SpendAlert]:
        """Every alert for one tenant (or all policy tenants), limits flattened."""
        rows: List[SpendAlert] = []
        for tenant in ([tenant_id] if tenant_id else self.tenant_ids()):
            rows.extend(self.evaluate(tenant, day=day, month=month).alerts)
        return rows

    def fired(
        self,
        tenant_id: Optional[str] = None,
        *,
        day: Optional[str] = None,
        month: Optional[str] = None,
    ) -> List[SpendAlert]:
        """Only the alerts that fired (the breach feed)."""
        return [
            alert
            for alert in self.alerts(tenant_id, day=day, month=month)
            if alert.fires
        ]

    # ------------------------------------------------------------------ #
    def _cost(self, tenant_id: str, window: str, *, month: Optional[str]) -> float:
        if window == "day":
            return float(self.ledger.daily_cost(tenant_id))
        return float(self.ledger.monthly_cost(tenant_id, month=month))

    def _tokens(self, tenant_id: str, *, day: Optional[str]) -> int:
        return int(self.ledger.daily_tokens(tenant_id, day=day))

    def _alert(
        self,
        *,
        tenant_id: str,
        kind: str,
        base: str,
        unit: str,
        window: str,
        current: float,
        limit: float,
        warn_at: float,
        alert_at: float,
        cap: str,
        mode: str,
        data: bool,
        vendor: Optional[str] = None,
    ) -> SpendAlert:
        """Build one alert from a measured figure and its thresholds."""
        if window not in WINDOWS:
            raise ValueError(f"unknown alert window: {window!r}")
        if mode not in MODES:
            raise ValueError(f"unknown budget mode: {mode!r}")
        if not data:
            return SpendAlert(
                tenant_id=tenant_id,
                kind=kind,
                code=f"{base}.no_data",
                severity=SEVERITY_NONE,
                verdict=VERDICT_NO_DATA,
                unit=unit,
                window=window,
                limit=limit,
                warn_at=warn_at,
                alert_at=alert_at,
                cap=cap,
                mode=mode,
                vendor=vendor,
                message=(
                    f"no metered {kind} data for {tenant_id!r}: spend is unknown, "
                    "not zero (the durable feed holds no billable record)"
                ),
            )
        if current >= alert_at:
            return SpendAlert(
                tenant_id=tenant_id,
                kind=kind,
                code=f"{base}.breach",
                severity=SEVERITY_ALERT,
                verdict=VERDICT_BREACHED,
                unit=unit,
                window=window,
                current=current,
                limit=limit,
                warn_at=warn_at,
                alert_at=alert_at,
                cap=cap,
                mode=mode,
                vendor=vendor,
                message=(
                    f"{kind} spend {current:.6g} reached the alert threshold "
                    f"{alert_at:.6g} (limit {limit:.6g}, {cap} cap)"
                ),
            )
        if current >= warn_at:
            return SpendAlert(
                tenant_id=tenant_id,
                kind=kind,
                code=f"{base}.warn",
                severity=SEVERITY_WARNING,
                verdict=VERDICT_AT_RISK,
                unit=unit,
                window=window,
                current=current,
                limit=limit,
                warn_at=warn_at,
                alert_at=alert_at,
                cap=cap,
                mode=mode,
                vendor=vendor,
                message=(
                    f"{kind} spend {current:.6g} reached the warning threshold "
                    f"{warn_at:.6g} (limit {limit:.6g})"
                ),
            )
        return SpendAlert(
            tenant_id=tenant_id,
            kind=kind,
            code=f"{base}.ok",
            severity=SEVERITY_NONE,
            verdict=VERDICT_OK,
            unit=unit,
            window=window,
            current=current,
            limit=limit,
            warn_at=warn_at,
            alert_at=alert_at,
            cap=cap,
            mode=mode,
            vendor=vendor,
            message=(
                f"{kind} spend {current:.6g} below the warning threshold "
                f"{warn_at:.6g} (limit {limit:.6g})"
            ),
        )

    def _vendor_alert(
        self,
        *,
        tenant_id: str,
        cap: VendorBudgetCap,
        current: float,
        mode: str,
        data: bool,
    ) -> SpendAlert:
        """Build one per-vendor alert (monthly USD cap, same ladder)."""
        return self._alert(
            tenant_id=tenant_id,
            kind=KIND_VENDOR,
            base=f"budget.vendor.{cap.vendor}",
            unit=UNIT_USD,
            window=cap.window,
            current=current,
            limit=float(cap.limit_usd),
            warn_at=float(cap.warn_at),
            alert_at=float(cap.alert_at),
            cap=cap.cap,
            mode=mode,
            data=data,
            vendor=cap.vendor,
        )


def evaluate_all(
    evaluator: SpendAlertEvaluator,
    *,
    day: Optional[str] = None,
    month: Optional[str] = None,
) -> Sequence[TenantSpendAlerts]:
    """Evaluate every tenant the evaluator holds a policy for."""
    return [
        evaluator.evaluate(tenant, day=day, month=month)
        for tenant in evaluator.tenant_ids()
    ]
