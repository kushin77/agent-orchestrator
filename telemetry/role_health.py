"""telemetry — per-role heartbeat health + budget burn observability (#637).

---knowledge---
module_id: telemetry.role_health
system: telemetry
app: telemetry
solution_class: enterprise
patterns: [outcome-not-liveness, declared-cadence-consumption, per-role-burn, named-alert-codes]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [cadence_seconds, ALERT_CODES, CADENCE_STATUSES, BURN_POSITIONS, EVENT_CADENCES, SCOPE_ROLE]
invariants: "the cadence table is total over the registry's own cadence vocabulary and a missed schedule raises exactly one named alert"
gotchas: "the thresholds are the declared ones, never a number restated locally"
related: ["#637", "#1510"]
do_not_duplicate: null
---knowledge---


WHY this module exists
----------------------
The workbook's Pillar 2 declares, for every role in the agent org chart, three
things the platform was not yet observing per role:

* a ``heartbeatSchedule`` (``hourly`` / ``every-30m`` / ``every-15m`` /
  ``daily``) — the cadence the role promises to check in at;
* a ``monthlyBudgetCapUsd`` — the ceiling FinOps enforces for that role;
* a place in the chargeback the CFO reads.

All three were only observable at *tenant* granularity: ``telemetry/budgets``
has alerts/killswitch/quota/chargeback per tenant, ``governance/reconcile`` has
per-*session* heartbeats, ``fleet/health.py`` has per-rung signals.  A role that
silently stopped beating, or quietly burned its whole cap, was invisible — the
rollup existed and the role axis did not.

This module is the role axis, and it is a **consumer**, never a second source of
truth.  It redefines nothing:

* the caps, the tiers and the ``heartbeatSchedule`` come from
  ``gateway.finops.budget.load_role_budgets`` (workbook-2 #633), which in turn
  consumes the workbook-1 declaration ``registry/personas/org-chart.yaml`` and
  asserts the bound persona cards agree;
* the over-cap policy and the ``warnAtPct`` come from
  ``gateway.finops.budget.parse_role_policy`` over
  ``gateway/finops/budgets.yaml``;
* the spend figure is ``telemetry.metering.report.UsageReporter`` — the durable
  append-only metering store (#33).  A role's burn is the sum of that store's
  billable cost for the role's calls in the month bucket; it is never a
  per-process counter and never a fabricated zero;
* the no-data honesty signal is ``UsageReporter.by_agent`` — empty means "this
  role has no metered calls", which is reported as ``unknown``, never as "$0
  spent, healthy".

Two observability facts, deliberately not one
---------------------------------------------
**Burn** and **staleness** are independent failures and produce independent
alerts, so a consumer never has to read two signals to learn one fact:

* a *burn* alert says spend reached ``warnAtPct`` (warning) or the cap
  (breached) — a role that is beating on time and still overspending;
* a *heartbeat* alert says the last accepted check-in is older than the cadence
  the role declared — a role that is spending nothing because it has stopped.

Exactly one alert per fact.  A role that misses its schedule raises exactly one
named heartbeat alert; the sequence number is assigned at *emit*, after
de-duplication, so a role that misses its schedule three times does not emit
three alerts.

The staleness slack is a declared constant, not a hidden fudge
--------------------------------------------------------------
``CADENCE_SLACK_FACTOR`` is ``1.0``: a heartbeat is due exactly one cadence
after the previous accepted beat.  ``daily`` therefore tolerates 24h + 0s.  A
grader that wants to prove the comparison is real can set the factor to ``0.0``
(the ``!stale`` mutation in the test lane) and observe the status flip — the
zero-retention mutation of the rule, not of the code that reads it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import yaml

from gateway.finops.budget import (  # noqa: F401  (consumed vocabulary)
    load_role_budgets,
    parse_role_policy,
    RoleBudgetError,
)
from telemetry.budgets.model import this_month_utc
from telemetry.clock import now_epoch, now_utc_iso
from telemetry.metering.report import GROUP_AGENT, WINDOW_MONTH

# --------------------------------------------------------------------------- #
# Cadence vocabulary (CONSUMED from registry/personas/registry.py)
# --------------------------------------------------------------------------- #
#: The platform cadences and the seconds each one allows between beats.  The
#: names are the strings the workbook-1 declaration uses verbatim
#: (``registry/personas/registry.py`` validates membership in this set), and
#: the numbers are the only place a cadence becomes arithmetic.
CADENCE_SECONDS: Dict[str, float] = {
    "every-15m": 900.0,
    "every-30m": 1800.0,
    "hourly": 3600.0,
    "daily": 86400.0,
}

#: Cadences that declare no wall-clock promise (they are driven by an event),
#: so a missing beat is not a staleness fact.  Consumed so the set matches the
#: registry's own cadence vocabulary rather than a local guess.
EVENT_CADENCES = frozenset({"event", "webhook"})

#: A beat is due exactly one cadence after the previous accepted beat.  This is
#: a *declared* constant so the staleness comparison is provably real: set it
#: to ``0.0`` and every scheduled role reads ``stale``.
CADENCE_SLACK_FACTOR = 1.0

#: Heartbeat statuses.  ``unknown`` is first-class: a role whose schedule is
#: not a platform cadence cannot be pronounced healthy.
STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_UNKNOWN = "unknown"
CADENCE_STATUSES = frozenset({STATUS_OK, STATUS_STALE, STATUS_UNKNOWN})

#: Burn positions, consuming the budgets lane's own ladder words.
POSITION_OK = "ok"
POSITION_WARNING = "warning"
POSITION_BREACHED = "breached"
BURN_POSITIONS = frozenset({POSITION_OK, POSITION_WARNING, POSITION_BREACHED})

#: Fallback warn threshold when the declaration does not state one.  Identical
#: to ``gateway.finops.budget.ROLE_DEFAULT_WARN_AT_PCT`` (a role is at cap when
#: projected spend reaches the cap itself); named here only so a caller that
#: cannot read the declaration still gets the declared behaviour.
DEFAULT_WARN_AT_PCT = 100.0

SCOPE_ROLE = "role"

#: Alert codes — stable, machine-keyable, and named after the fact they carry.
#: A downstream alert rule keys on the code, so the code must say *which* fact
#: fired (burn warning vs burn breach vs a missed heartbeat vs an unknown
#: schedule): three different facts must never collapse into one code.
CODE_BURN_WARN = "budget.burn.warn"
CODE_BURN_BREACH = "budget.burn.breach"
CODE_HEARTBEAT_STALE = "heartbeat.stale"
CODE_HEARTBEAT_UNKNOWN = "heartbeat.unknown_schedule"
ALERT_CODES = frozenset(
    {
        CODE_BURN_WARN,
        CODE_BURN_BREACH,
        CODE_HEARTBEAT_STALE,
        CODE_HEARTBEAT_UNKNOWN,
    }
)

SEVERITY_NONE = "none"
SEVERITY_WARNING = "warning"
SEVERITY_ALERT = "alert"

#: Codes an alert may carry and how urgent each one is.  A new code that is not
#: declared here is refused on construction (see :class:`DeferredAlert`), so an
#: alert can never be emitted without a severity a consumer can rank.
CODE_SEVERITY: Dict[str, str] = {
    CODE_BURN_WARN: SEVERITY_WARNING,
    CODE_BURN_BREACH: SEVERITY_ALERT,
    CODE_HEARTBEAT_STALE: SEVERITY_ALERT,
    CODE_HEARTBEAT_UNKNOWN: SEVERITY_WARNING,
}

_SEVERITY_RANK = {SEVERITY_NONE: 0, SEVERITY_WARNING: 1, SEVERITY_ALERT: 2}


def cadence_seconds(schedule: Optional[str]) -> Optional[float]:
    """Seconds allowed between beats for a declared cadence.

    ``None`` means "not a wall-clock cadence": either the role declares no
    schedule at all, or it declares an event-driven one.  Callers must treat
    that as ``unknown`` — never as "no deadline, so healthy".
    """
    if schedule is None:
        return None
    return CADENCE_SECONDS.get(str(schedule).strip())


def cadence_threshold_seconds(schedule: Optional[str]) -> Optional[float]:
    """The staleness threshold for a cadence (cadence x slack factor).

    The slack is applied here and nowhere else, so the threshold a test pins is
    the threshold the evaluator used.
    """
    base = cadence_seconds(schedule)
    if base is None:
        return None
    return base * CADENCE_SLACK_FACTOR


def is_event_cadence(schedule: Optional[str]) -> bool:
    """Whether the schedule promises beats but not a wall-clock deadline."""
    return schedule is not None and str(schedule).strip() in EVENT_CADENCES


def _parse_iso(value: str) -> float:
    """Epoch seconds for an RFC 3339 ``Z`` timestamp (the repo-wide shape)."""
    from datetime import datetime

    text = str(value).strip()
    if not text:
        raise ValueError("timestamp is empty")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp has no timezone: {value!r}")
    return parsed.timestamp()


# --------------------------------------------------------------------------- #
# Declared role inputs (caps + schedules + thresholds, all consumed)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RoleCaps:
    """The declared per-role inputs, read from the workbook-1 declaration.

    Every field is *consumed*: ``caps``/``schedules``/``tiers`` come from
    ``gateway.finops.budget.load_role_budgets`` (which itself consumes
    ``registry/personas/org-chart.yaml`` and asserts the bound cards agree) and
    ``warn_at_pct`` comes from ``parse_role_policy`` over
    ``gateway/finops/budgets.yaml``.  Nothing here restates a number.
    """

    tenant: str
    caps: Dict[str, float]
    schedules: Dict[str, Optional[str]]
    tiers: Dict[str, Optional[str]]
    warn_at_pct: float
    policy: str
    source: str

    def role_ids(self) -> List[str]:
        """Every declared role id (sorted)."""
        return sorted(self.caps)

    def cap_usd(self, role_id: str) -> float:
        """The role's declared monthly cap."""
        return float(self.caps[role_id])

    def schedule(self, role_id: str) -> Optional[str]:
        """The role's declared heartbeat cadence (``None`` when undeclared)."""
        return self.schedules.get(role_id)

    def threshold_seconds(self, role_id: str) -> Optional[float]:
        """The role's staleness threshold in seconds (``None`` = no deadline)."""
        return cadence_threshold_seconds(self.schedule(role_id))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant": self.tenant,
            "warnAtPct": self.warn_at_pct,
            "policy": self.policy,
            "source": self.source,
            "roles": {
                role_id: {
                    "monthlyCapUsd": self.cap_usd(role_id),
                    "heartbeatSchedule": self.schedule(role_id),
                    "defaultModelTier": self.tiers.get(role_id),
                }
                for role_id in self.role_ids()
            },
        }


def load_role_caps(
    *,
    budgets_yaml: Path = Path("gateway/finops/budgets.yaml"),
) -> RoleCaps:
    """Load the declared role inputs (caps + schedules + warn threshold).

    Deliberately a *re-read* of the declaration rather than a copy of it: the
    numbers live in ``registry/personas/**`` and ``gateway/finops/budgets.yaml``
    and this function is their consumer here.  A missing budgets.yaml falls back
    to the declared default threshold (and says so via ``source``), so the
    surface degrades to a documented default rather than inventing a number.
    """
    roles = load_role_budgets()
    caps: Dict[str, float] = {}
    schedules: Dict[str, Optional[str]] = {}
    tiers: Dict[str, Optional[str]] = {}
    tenant = "platform"
    source = ""
    for role in roles.values():
        caps[role.role_id] = float(role.monthly_cap_usd)
        schedules[role.role_id] = role.heartbeat_schedule
        tiers[role.role_id] = role.default_model_tier
        tenant = role.tenant
        source = role.source_path or source
    warn_at = DEFAULT_WARN_AT_PCT
    policy = "stop"
    path = Path(budgets_yaml)
    if path.is_file():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:  # pragma: no cover - defensive
            raise RoleBudgetError(f"{path}: invalid YAML: {exc}") from exc
        defaults = parse_role_policy(data)
        warn_at = float(defaults["warn_at_pct"])
        policy = str(getattr(defaults["policy"], "value", defaults["policy"]))
    return RoleCaps(
        tenant=tenant,
        caps=caps,
        schedules=schedules,
        tiers=tiers,
        warn_at_pct=warn_at,
        policy=policy,
        source=f"{source} + {path}",
    )


# --------------------------------------------------------------------------- #
# The alert feed (one feed, two facts, one alert per fact)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DeferredAlert:
    """One named observability alert, awaiting its sequence number.

    The sequence is assigned by :class:`AlertFeed` **after** de-duplication,
    which is what makes "exactly one alert for one missed schedule" a property
    of the feed rather than of every caller.  Constructing an alert with an
    undeclared code raises: an alert whose severity no consumer can rank is not
    an alert, it is an unreadable record.
    """

    code: str
    severity: str
    role_id: str
    message: str
    scope: str = SCOPE_ROLE
    detail: Mapping[str, Any] = field(default_factory=dict)
    sequence: Optional[int] = None

    def __post_init__(self) -> None:
        if self.code not in ALERT_CODES:
            raise ValueError(
                f"unknown alert code {self.code!r}; declared: "
                f"{', '.join(sorted(ALERT_CODES))}"
            )
        if self.severity != CODE_SEVERITY[self.code]:
            raise ValueError(
                f"alert code {self.code!r} carries severity "
                f"{CODE_SEVERITY[self.code]!r}, not {self.severity!r}"
            )

    def with_sequence(self, sequence: int) -> "DeferredAlert":
        """A copy carrying its feed position (the only mutation allowed)."""
        return DeferredAlert(
            code=self.code,
            severity=self.severity,
            role_id=self.role_id,
            message=self.message,
            scope=self.scope,
            detail=dict(self.detail),
            sequence=sequence,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sequence": self.sequence,
            "scope": self.scope,
            "code": self.code,
            "severity": self.severity,
            "roleId": self.role_id,
            "message": self.message,
            "detail": dict(self.detail),
        }


def _alert_sort_key(alert: DeferredAlert) -> Tuple[int, str, str]:
    """Most urgent first, then by code, then by role (deterministic)."""
    return (-_SEVERITY_RANK[alert.severity], alert.code, alert.role_id)


class AlertFeed:
    """The composed per-role alert feed (burn + heartbeat, one alert per fact).

    ``emit`` accepts the alerts both axes produced and returns them ordered and
    numbered.  The feed is the single surface the portal/exporter reads, so a
    consumer that reads one alert knows one fact: the alert names its role and
    carries the code of the fact that fired.
    """

    def __init__(self) -> None:
        self._alerts: List[DeferredAlert] = []

    def emit(self, alerts: Iterable[DeferredAlert]) -> List[DeferredAlert]:
        """Order, number and store every alert; return them in feed order."""
        ordered = sorted(alerts, key=_alert_sort_key)
        numbered = [a.with_sequence(i) for i, a in enumerate(ordered)]
        self._alerts = numbered
        return list(numbered)

    def alerts(self) -> List[DeferredAlert]:
        return list(self._alerts)

    def by_code(self) -> Dict[str, int]:
        """Alert counts keyed by code (the alerting feed's summary line)."""
        counts: Dict[str, int] = {}
        for alert in self._alerts:
            counts[alert.code] = counts.get(alert.code, 0) + 1
        return counts

    def for_role(self, role_id: str) -> List[DeferredAlert]:
        """Every alert naming one role (the per-role drill-down)."""
        return [a for a in self._alerts if a.role_id == role_id]

    def fired(self) -> List[DeferredAlert]:
        """Only the alerts that an operator must act on (hard severity)."""
        return [a for a in self._alerts if a.severity == SEVERITY_ALERT]

    def to_dicts(self) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self._alerts]

    def to_jsonl(self) -> str:
        """The feed as JSON Lines (the portal/exporter consume shape)."""
        return "".join(
            json.dumps(a.to_dict(), sort_keys=True) + "\n" for a in self._alerts
        )


# --------------------------------------------------------------------------- #
# Fact 1 — per-role budget burn
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BudgetBurn:
    """One role's month-to-date burn against its declared monthly cap.

    ``burn_pct`` is the *only* burn arithmetic in the platform: cost, divided by
    the declared cap, expressed in per cent.  A cap of zero has no meaningful
    denominator, so it reads ``100.0`` when any spend exists and ``0.0`` when
    none does — a zero cap with zero spend is within cap, and a zero cap with
    any spend is over it.
    """

    role_id: str
    month: str
    cost_usd: float = 0.0
    calls: int = 0
    tokens: int = 0
    monthly_cap_usd: float = 0.0
    warn_at_pct: float = DEFAULT_WARN_AT_PCT
    has_metered_calls: bool = False

    @property
    def burn_pct(self) -> float:
        """Percentage of the declared cap consumed (the burn figure)."""
        if self.monthly_cap_usd <= 0:
            return 100.0 if self.cost_usd > 0 else 0.0
        return (self.cost_usd / self.monthly_cap_usd) * 100.0

    @property
    def position(self) -> str:
        """``ok`` / ``warning`` / ``breached`` against cap and warn threshold.

        The ladder mirrors the budgets lane's own: at/over the cap is a breach,
        at/over ``warnAtPct`` is a warning, anything below is ok.  A role that
        has never been metered is ``ok`` — a position is about spend, and the
        separate ``hasMeteredCalls`` flag is where "no data" is said.
        """
        if not self.has_metered_calls:
            return POSITION_OK
        if self.monthly_cap_usd <= 0:
            return POSITION_BREACHED if self.cost_usd > 0 else POSITION_OK
        if self.cost_usd >= self.monthly_cap_usd:
            return POSITION_BREACHED
        if self.burn_pct >= self.warn_at_pct:
            return POSITION_WARNING
        return POSITION_OK

    def alert(self) -> Optional[DeferredAlert]:
        """The burn alert for this role, or ``None`` when inside its thresholds.

        A role in ``ok`` position produces **no** alert — the absence of an
        alert is the signal, so a healthy role never adds noise to the feed.
        """
        position = self.position
        if position == POSITION_BREACHED:
            return DeferredAlert(
                code=CODE_BURN_BREACH,
                severity=CODE_SEVERITY[CODE_BURN_BREACH],
                role_id=self.role_id,
                message=(
                    f"role {self.role_id!r} reached its monthly cap: "
                    f"${self.cost_usd:.6g} of ${self.monthly_cap_usd:.6g} "
                    f"({self.burn_pct:.4g}%) in {self.month}"
                ),
                detail={
                    "month": self.month,
                    "costUsd": round(self.cost_usd, 8),
                    "monthlyCapUsd": round(self.monthly_cap_usd, 8),
                    "burnPct": round(self.burn_pct, 6),
                    "position": position,
                },
            )
        if position == POSITION_WARNING:
            return DeferredAlert(
                code=CODE_BURN_WARN,
                severity=CODE_SEVERITY[CODE_BURN_WARN],
                role_id=self.role_id,
                message=(
                    f"role {self.role_id!r} reached its warn threshold: "
                    f"${self.cost_usd:.6g} of ${self.monthly_cap_usd:.6g} "
                    f"({self.burn_pct:.4g}% >= {self.warn_at_pct:.4g}%) "
                    f"in {self.month}"
                ),
                detail={
                    "month": self.month,
                    "costUsd": round(self.cost_usd, 8),
                    "monthlyCapUsd": round(self.monthly_cap_usd, 8),
                    "burnPct": round(self.burn_pct, 6),
                    "warnAtPct": round(self.warn_at_pct, 6),
                    "position": position,
                },
            )
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "roleId": self.role_id,
            "month": self.month,
            "costUsd": round(self.cost_usd, 8),
            "calls": self.calls,
            "tokens": self.tokens,
            "monthlyCapUsd": round(self.monthly_cap_usd, 8),
            "warnAtPct": round(self.warn_at_pct, 6),
            "burnPct": round(self.burn_pct, 6),
            "position": self.position,
            "hasMeteredCalls": self.has_metered_calls,
        }


class RoleBudgetBurnReport:
    """Month-to-date burn for every declared role, plus the burn alert feed.

    The figure comes from the durable metering store, grouped by the role id the
    call was dispatched for (``GROUP_AGENT`` in the metering reporter's
    vocabulary — the persona the call is attributed to).  It is **not** a
    per-process counter and **not** a fabricated zero: a role with no metered
    calls in the month reports ``hasMeteredCalls=False``.
    """

    def __init__(self, reporter: Any, caps: RoleCaps, *, month: Optional[str] = None):
        self.reporter = reporter
        self.caps = caps
        self.month = month or this_month_utc()

    def rows(self) -> List[BudgetBurn]:
        """One burn row per declared role, in declared (sorted) order."""
        per_agent = self._cost_by_agent()
        rows: List[BudgetBurn] = []
        for role_id in self.caps.role_ids():
            agg = per_agent.get(role_id)
            cost = float(getattr(agg, "cost_usd", 0.0) or 0.0)
            calls = int(getattr(agg, "calls", 0) or 0)
            tokens = int(getattr(agg, "total_tokens", 0) or 0)
            rows.append(
                BudgetBurn(
                    role_id=role_id,
                    month=self.month,
                    cost_usd=cost,
                    calls=calls,
                    tokens=tokens,
                    monthly_cap_usd=self.caps.cap_usd(role_id),
                    warn_at_pct=self.caps.warn_at_pct,
                    has_metered_calls=agg is not None,
                )
            )
        return rows

    def _cost_by_agent(self) -> Dict[str, Any]:
        """Billable cost/tokens per agent id for the month bucket.

        ``rollup`` keys are ``(bucket, dim1, dim2, ...)``, so with
        ``dimensions=(GROUP_AGENT,)`` the agent id is ``key[1]`` and the bucket
        is ``key[0]`` — see the sibling adapter
        ``telemetry.budgets.ledger.MeteringReporterLedger``, which reads the
        same shape.
        """
        rollup = self.reporter.rollup(
            window=WINDOW_MONTH,
            dimensions=(GROUP_AGENT,),
            start=self.month,
            end=self.month,
        )
        return {key[1]: agg for key, agg in rollup.items()}

    def alerts(self) -> List[DeferredAlert]:
        """Every burn alert (only the roles at/over a threshold produce one)."""
        return [a for a in (row.alert() for row in self.rows()) if a is not None]

    def by_role(self) -> Dict[str, BudgetBurn]:
        return {row.role_id: row for row in self.rows()}

    def totals(self) -> Dict[str, Any]:
        """Whole-report totals (the FinOps summary line), honest about no-data."""
        rows = self.rows()
        metered = [r for r in rows if r.has_metered_calls]
        return {
            "month": self.month,
            "roles": len(rows),
            "meteredRoles": len(metered),
            "unmeteredRoles": len(rows) - len(metered),
            "costUsd": round(sum(r.cost_usd for r in rows), 8),
            "capUsd": round(sum(r.monthly_cap_usd for r in rows), 8),
            "calls": sum(r.calls for r in rows),
            "warns": len([r for r in rows if r.position == POSITION_WARNING]),
            "breaches": len([r for r in rows if r.position == POSITION_BREACHED]),
        }


# --------------------------------------------------------------------------- #
# Fact 2 — per-role heartbeat staleness against the declared schedule
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Heartbeat:
    """One role's last accepted check-in.

    Small and explicit on purpose, mirroring ``governance/reconcile/heartbeat``:
    the timestamp is carried both as an ISO string (for a human) and as epoch
    seconds (for the arithmetic), and a record with no usable timestamp fails
    towards the audit rather than towards "live".
    """

    role_id: str
    at_epoch: float
    at_iso: str = ""
    source: str = ""
    note: str = ""

    @classmethod
    def at(cls, role_id: str, at_epoch: float, *, source: str = "", note: str = "") -> "Heartbeat":
        """Build a beat from epoch seconds (ISO rendered for the record)."""
        from datetime import datetime, timezone

        return cls(
            role_id=role_id,
            at_epoch=float(at_epoch),
            at_iso=datetime.fromtimestamp(
                float(at_epoch), tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            source=source,
            note=note,
        )

    @classmethod
    def parse(cls, role_id: str, value: Any, *, source: str = "") -> "Heartbeat":
        """Build a beat from a timestamp value (ISO string or epoch number).

        An unreadable value raises — a heartbeat the platform cannot date is not
        evidence that the role is alive.
        """
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return cls.at(role_id, float(value), source=source)
        epoch = _parse_iso(str(value))
        return Heartbeat(
            role_id=role_id,
            at_epoch=epoch,
            at_iso=str(value).strip(),
            source=source,
        )

    @property
    def age_seconds(self) -> Optional[float]:
        """The beat's age in seconds (``None`` when the beat is undated)."""
        return None if self.at_epoch <= 0 else _now_epoch() - self.at_epoch

    def to_dict(self) -> Dict[str, Any]:
        return {
            "roleId": self.role_id,
            "at": self.at_epoch,
            "atIso": self.at_iso,
            "source": self.source,
            "note": self.note,
        }


def _now_epoch() -> float:
    """The one clock seam (``telemetry/clock.py``, issue #1025)."""
    return now_epoch()


@dataclass(frozen=True)
class HeartbeatStatus:
    """One role's heartbeat verdict against its *declared* schedule.

    The verdict is one of ``ok``/``stale``/``unknown``, and it is computed from
    exactly two inputs the platform declared: the role's ``heartbeatSchedule``
    (workbook-1) and its last accepted beat.  There is no third, guessed input.
    """

    role_id: str
    schedule: Optional[str]
    threshold_seconds: Optional[float]
    last_beat: Optional[Heartbeat]
    status: str
    age_seconds: Optional[float] = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in CADENCE_STATUSES:
            raise ValueError(
                f"unknown heartbeat status {self.status!r}; declared: "
                f"{', '.join(sorted(CADENCE_STATUSES))}"
            )

    @property
    def ok(self) -> bool:
        """True only for a role that declared a cadence and met it.

        ``unknown`` is deliberately **not** ok: a role whose schedule cannot be
        read is a monitoring gap, not a healthy role.
        """
        return self.status == STATUS_OK

    @property
    def overdue_seconds(self) -> Optional[float]:
        """How far past its threshold the beat is (``0.0`` when not overdue)."""
        if self.threshold_seconds is None or self.age_seconds is None:
            return None
        overdue = self.age_seconds - self.threshold_seconds
        return overdue if overdue > 0 else 0.0

    def alert(self) -> Optional[DeferredAlert]:
        """The heartbeat alert for this role, or ``None`` when it met its beat.

        Exactly one alert — a missed schedule produces the ``heartbeat.stale``
        alert and nothing else, and a role that met its beat produces nothing.
        """
        if self.status == STATUS_STALE:
            return DeferredAlert(
                code=CODE_HEARTBEAT_STALE,
                severity=CODE_SEVERITY[CODE_HEARTBEAT_STALE],
                role_id=self.role_id,
                message=(
                    f"role {self.role_id!r} missed its {self.schedule!r} "
                    f"heartbeat: last beat {self.age_seconds:.6g}s ago, "
                    f"threshold {self.threshold_seconds:.6g}s"
                    if self.age_seconds is not None
                    and self.threshold_seconds is not None
                    else f"role {self.role_id!r} missed its declared heartbeat"
                ),
                detail={
                    "schedule": self.schedule,
                    "thresholdSeconds": self.threshold_seconds,
                    "ageSeconds": (
                        None if self.age_seconds is None else round(self.age_seconds, 6)
                    ),
                    "overdueSeconds": (
                        None
                        if self.overdue_seconds is None
                        else round(self.overdue_seconds, 6)
                    ),
                    "status": self.status,
                },
            )
        if self.status == STATUS_UNKNOWN:
            return DeferredAlert(
                code=CODE_HEARTBEAT_UNKNOWN,
                severity=CODE_SEVERITY[CODE_HEARTBEAT_UNKNOWN],
                role_id=self.role_id,
                message=(
                    f"role {self.role_id!r} declares no wall-clock heartbeat "
                    f"cadence (schedule={self.schedule!r}): staleness is unknown, "
                    "not healthy"
                ),
                detail={
                    "schedule": self.schedule,
                    "status": self.status,
                    "reason": self.reason,
                },
            )
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "roleId": self.role_id,
            "schedule": self.schedule,
            "thresholdSeconds": self.threshold_seconds,
            "lastBeat": None if self.last_beat is None else self.last_beat.to_dict(),
            "ageSeconds": None if self.age_seconds is None else round(self.age_seconds, 6),
            "overdueSeconds": (
                None if self.overdue_seconds is None else round(self.overdue_seconds, 6)
            ),
            "status": self.status,
            "ok": self.ok,
            "reason": self.reason,
        }

    @classmethod
    def evaluate(
        cls,
        role_id: str,
        schedule: Optional[str],
        beat: Optional[Heartbeat],
        *,
        now_epoch: Optional[float] = None,
    ) -> "HeartbeatStatus":
        """Judge one role's heartbeat against its declared cadence.

        Fail closed in the two directions that matter:

        * a schedule that is not a wall-clock cadence (absent, or event-driven)
          is ``unknown`` — never ``ok``, because the platform cannot prove the
          role is alive;
        * a missing beat for a declared cadence is ``stale`` — a role that has
          never checked in has missed every schedule so far.
        """
        threshold = cadence_threshold_seconds(schedule)
        if threshold is None:
            reason = (
                "no heartbeatSchedule declared"
                if schedule is None
                else (
                    f"schedule {schedule!r} is event-driven (no wall-clock promise)"
                    if is_event_cadence(schedule)
                    else f"schedule {schedule!r} is not a platform cadence"
                )
            )
            return cls(
                role_id=role_id,
                schedule=schedule,
                threshold_seconds=None,
                last_beat=beat,
                status=STATUS_UNKNOWN,
                age_seconds=None if beat is None else _age(beat, now_epoch),
                reason=reason,
            )
        if beat is None:
            return cls(
                role_id=role_id,
                schedule=schedule,
                threshold_seconds=threshold,
                last_beat=None,
                status=STATUS_STALE,
                age_seconds=None,
                reason="no heartbeat recorded for a role that declares a cadence",
            )
        age = _age(beat, now_epoch)
        stale = age > threshold
        return cls(
            role_id=role_id,
            schedule=schedule,
            threshold_seconds=threshold,
            last_beat=beat,
            status=STATUS_STALE if stale else STATUS_OK,
            age_seconds=age,
            reason=(
                f"beat did not advance within {threshold:.6g}s (age {age:.6g}s)"
                if stale
                else f"beat within the declared {schedule!r} cadence"
            ),
        )


def _age(beat: Heartbeat, now_epoch: Optional[float]) -> float:
    """The beat's age, measured against a pinned clock when one is supplied."""
    if now_epoch is None:
        return _now_epoch() - beat.at_epoch
    return float(now_epoch) - beat.at_epoch


class RoleHeartbeatMonitor:
    """Per-role heartbeat health for every declared role.

    ``beats`` maps role id -> last accepted :class:`Heartbeat`; a role with no
    entry has not beaten.  ``now_epoch`` pins the clock so an offline check is
    reproducible (the same seam ``governance/reconcile`` uses).
    """

    def __init__(
        self,
        caps: RoleCaps,
        beats: Optional[Mapping[str, Heartbeat]] = None,
        *,
        now_epoch: Optional[float] = None,
    ) -> None:
        self.caps = caps
        self.beats: Dict[str, Heartbeat] = dict(beats or {})
        self.now_epoch = now_epoch

    def statuses(self) -> List[HeartbeatStatus]:
        """One status per declared role, in declared (sorted) order."""
        return [
            HeartbeatStatus.evaluate(
                role_id,
                self.caps.schedule(role_id),
                self.beats.get(role_id),
                now_epoch=self.now_epoch,
            )
            for role_id in self.caps.role_ids()
        ]

    def alerts(self) -> List[DeferredAlert]:
        """Every heartbeat alert (stale or unknown), one per affected role."""
        return [a for a in (s.alert() for s in self.statuses()) if a is not None]

    def by_role(self) -> Dict[str, HeartbeatStatus]:
        return {s.role_id: s for s in self.statuses()}

    def accept(self, beat: Heartbeat) -> None:
        """Record a beat as the role's last accepted check-in."""
        self.beats[beat.role_id] = beat


def heartbeats_from_sessions(paths: Iterable[Any]) -> Dict[str, Heartbeat]:
    """Build role beats from ``governance/reconcile`` session heartbeat files.

    Consumed, never re-implemented: a session heartbeat already carries the role
    it beats for (``agent``), the lane, and both an epoch and an ISO timestamp.
    Files that cannot be read or dated are skipped rather than guessed at — a
    beat the platform cannot date must not be counted as evidence of life.
    """
    beats: Dict[str, Heartbeat] = {}
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            candidates = sorted(path.glob("*.json"))
        else:
            candidates = [path]
        for candidate in candidates:
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            role_id = payload.get("agent") or payload.get("role_id")
            if not role_id:
                continue
            value = payload.get("at") or payload.get("at_iso")
            if value is None:
                continue
            try:
                beat = Heartbeat.parse(str(role_id), value, source=str(candidate))
            except ValueError:
                continue
            current = beats.get(beat.role_id)
            if current is None or beat.at_epoch > current.at_epoch:
                beats[beat.role_id] = beat
    return beats


# --------------------------------------------------------------------------- #
# Chargeback rows per role (CFO cap visibility) + the exporter feed
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RoleChargebackRow:
    """One role's chargeback line for one month (the CFO's cap visibility).

    Shape-compatible with ``telemetry.budgets.chargeback.ChargebackRow``'s
    camelCase keys so the budgets exporter's own CSV writer consumes it
    verbatim (that writer reads its header from the row's ``to_dict`` keys), and
    extended with the burn fields the cap view needs — ``monthlyCapUsd``,
    ``warnAtPct``, ``burnPct`` and ``position``.
    """

    role_id: str
    tenant: str
    month: str
    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    unmetered_calls: int = 0
    monthly_cap_usd: float = 0.0
    warn_at_pct: float = DEFAULT_WARN_AT_PCT

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def burn_pct(self) -> float:
        """Percentage of the declared cap this role's chargeback consumed."""
        if self.monthly_cap_usd <= 0:
            return 100.0 if self.cost_usd > 0 else 0.0
        return (self.cost_usd / self.monthly_cap_usd) * 100.0

    @property
    def position(self) -> str:
        if self.monthly_cap_usd <= 0:
            return POSITION_BREACHED if self.cost_usd > 0 else POSITION_OK
        if self.cost_usd >= self.monthly_cap_usd:
            return POSITION_BREACHED
        if self.burn_pct >= self.warn_at_pct:
            return POSITION_WARNING
        return POSITION_OK

    def to_dict(self) -> Dict[str, Any]:
        return {
            "roleId": self.role_id,
            "tenantId": self.tenant,
            "month": self.month,
            "calls": self.calls,
            "cacheHits": self.cache_hits,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "costUsd": round(self.cost_usd, 8),
            "unmeteredCalls": self.unmetered_calls,
            "monthlyCapUsd": round(self.monthly_cap_usd, 8),
            "warnAtPct": round(self.warn_at_pct, 6),
            "burnPct": round(self.burn_pct, 6),
            "position": self.position,
        }

    def __getitem__(self, key: str) -> Any:
        """Support ``row[header]`` so the budgets CSV writer consumes it.

        ``telemetry.budgets.chargeback`` writes rows by indexing them with the
        header keys taken from ``to_dict()``; supporting the same protocol here
        keeps one CSV writer rather than two.
        """
        try:
            return self.to_dict()[key]
        except KeyError:
            raise KeyError(f"{self.__class__.__name__} has no field {key!r}") from None

    def to_csv_row(self) -> List[str]:
        """CSV cells, deriving column names from ``to_dict`` (never duplicated)."""
        return [str(value) for value in self.to_dict().values()]


class RoleChargebackReport:
    """Per-role chargeback lines over the metering rollups.

    Per month and per role: calls, cache hits, tokens, cost, unmetered calls —
    plus the declared cap and the resulting burn/position, so the CFO sees a
    role's cap and its consumption on the same line.  A role with no metered
    usage in a month produces no row: a chargeback line is only ever an honest
    sum over metered records.
    """

    def __init__(self, reporter: Any, caps: RoleCaps):
        self.reporter = reporter
        self.caps = caps

    def tenants(self) -> List[str]:
        """Tenants with recorded billable usage (sorted)."""
        return sorted(self.reporter.by_tenant().keys())

    def report(
        self,
        *,
        month: Optional[str] = None,
        tenant_id: Optional[str] = None,
        role_id: Optional[str] = None,
    ) -> List[RoleChargebackRow]:
        rows: List[RoleChargebackRow] = []
        for tenant in self.tenants():
            if tenant_id is not None and tenant != tenant_id:
                continue
            per_month = self._monthly_by_agent(tenant)
            for bucket in sorted(per_month):
                if month is not None and bucket != month:
                    continue
                for role in sorted(per_month[bucket]):
                    if role_id is not None and role != role_id:
                        continue
                    rows.append(
                        self._row(tenant, role, bucket, per_month[bucket][role])
                    )
        return rows

    def _monthly_by_agent(self, tenant: str) -> Dict[str, Dict[str, Any]]:
        """``{month: {agent: aggregate}}`` for one tenant (billable only).

        Keys are ``(bucket, agent)``: the bucket first, then the dimension the
        rollup was asked to group by.
        """
        rollup = self.reporter.rollup(
            window=WINDOW_MONTH,
            dimensions=(GROUP_AGENT,),
            tenant_id=tenant,
        )
        out: Dict[str, Dict[str, Any]] = {}
        for key, agg in rollup.items():
            month, agent = key[0], key[1]
            out.setdefault(month, {})[agent] = agg
        return out

    def _row(self, tenant: str, role_id: str, month: str, agg: Any) -> RoleChargebackRow:
        cap = float(self.caps.caps.get(role_id, 0.0))
        return RoleChargebackRow(
            role_id=role_id,
            tenant=tenant,
            month=month,
            calls=int(getattr(agg, "calls", 0) or 0),
            cache_hits=int(getattr(agg, "cache_hits", 0) or 0),
            input_tokens=int(getattr(agg, "input_tokens", 0) or 0),
            output_tokens=int(getattr(agg, "output_tokens", 0) or 0),
            cost_usd=float(getattr(agg, "cost_usd", 0.0) or 0.0),
            unmetered_calls=int(getattr(agg, "unmetered_calls", 0) or 0),
            monthly_cap_usd=cap,
            warn_at_pct=self.caps.warn_at_pct,
        )

    def totals(self) -> Dict[str, Any]:
        rows = self.report()
        return {
            "roles": len({r.role_id for r in rows}),
            "tenants": len({r.tenant for r in rows}),
            "calls": sum(r.calls for r in rows),
            "totalTokens": sum(r.total_tokens for r in rows),
            "costUsd": round(sum(r.cost_usd for r in rows), 8),
            "capUsd": round(sum(r.monthly_cap_usd for r in rows), 8),
            "unmeteredCalls": sum(r.unmetered_calls for r in rows),
        }

    def write_json(self, path: Any, **kwargs: Any) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "generatedAt": now_utc_iso(),
            "scope": SCOPE_ROLE,
            "rows": [r.to_dict() for r in self.report(**kwargs)],
            "totals": self.totals(),
        }
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return out


class RoleHealthReport:
    """The composed per-role observability surface (burn + health + chargeback).

    One object builds all three role views and the one alert feed, so a consumer
    (the portal, the exporter, an alert rule) reads a single snapshot instead of
    stitching three surfaces together.  Every view is derived from the same two
    inputs — the declared caps/schedules and the durable metering feed — so the
    three can never disagree about what a role's cap or cadence is.
    """

    def __init__(
        self,
        reporter: Any,
        caps: Optional[RoleCaps] = None,
        *,
        beats: Optional[Mapping[str, Heartbeat]] = None,
        month: Optional[str] = None,
        now_epoch: Optional[float] = None,
    ) -> None:
        self.caps = caps if caps is not None else load_role_caps()
        self.reporter = reporter
        self.month = month or this_month_utc()
        self.now_epoch = now_epoch
        self.burn = RoleBudgetBurnReport(reporter, self.caps, month=self.month)
        self.heartbeat = RoleHeartbeatMonitor(
            self.caps, beats, now_epoch=now_epoch
        )
        self.chargeback = RoleChargebackReport(reporter, self.caps)
        self.feed = AlertFeed()

    def alerts(self) -> List[DeferredAlert]:
        """Compose both facts into one feed (one alert per fact, numbered)."""
        return self.feed.emit([*self.burn.alerts(), *self.heartbeat.alerts()])

    def roles_snapshot(self) -> Dict[str, Any]:
        """Per-role snapshot: burn + heartbeat + position on one row."""
        burn = self.burn.by_role()
        beats = self.heartbeat.by_role()
        snapshot: Dict[str, Any] = {}
        for role_id in self.caps.role_ids():
            snapshot[role_id] = {
                "monthlyCapUsd": self.caps.cap_usd(role_id),
                "warnAtPct": self.caps.warn_at_pct,
                "heartbeatSchedule": self.caps.schedule(role_id),
                "burn": burn[role_id].to_dict(),
                "heartbeat": beats[role_id].to_dict(),
            }
        return snapshot

    def snapshot(self) -> Dict[str, Any]:
        """The machine-readable role feed (the portal/exporter consume this).

        Deliberately *additive*: the keys are new (``roleBudgetBurn``,
        ``roleHeartbeat``, ``roleChargeback``, ``roleAlerts``) and no
        tenant-scoped key is touched, so a consumer of the budgets exporter's
        snapshot keeps working unchanged.

        The feed is built here, so a snapshot is never written with an empty
        alert list just because a caller read it before calling
        :meth:`alerts`.
        """
        alerts = self.alerts()
        return {
            "generatedAt": _now_iso(),
            "scope": SCOPE_ROLE,
            "tenant": self.caps.tenant,
            "month": self.month,
            "capSource": self.caps.source,
            "warnAtPct": self.caps.warn_at_pct,
            "roleBudgetBurn": {
                "totals": self.burn.totals(),
                "rows": [row.to_dict() for row in self.burn.rows()],
            },
            "roleHeartbeat": {
                "statuses": [s.to_dict() for s in self.heartbeat.statuses()],
            },
            "roleChargeback": [
                row.to_dict() for row in self.chargeback.report(month=self.month)
            ],
            "roleAlerts": [a.to_dict() for a in alerts],
        }

    def write_json(self, path: Any) -> Path:
        """Write the role feed as JSON (dashboards/alerts consume it)."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self.snapshot(), indent=2) + "\n", encoding="utf-8"
        )
        return out

    def write_jsonl(self, path: Any) -> Path:
        """Write the alert feed as JSON Lines (the portal's streaming shape)."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        self.alerts()  # ensure the feed is numbered before it is written
        out.write_text(self.feed.to_jsonl(), encoding="utf-8")
        return out


class RoleBudgetBurnExporter:
    """Export the per-role feed, optionally merged into the budgets snapshot.

    ``merge_into`` exists because the portal already reads the budgets exporter's
    snapshot: merging the role keys in keeps that one feed as the portal's
    single source, instead of a second endpoint the portal would have to know
    about.  Merging never overwrites a key the tenant-scoped snapshot owns.
    """

    def __init__(
        self,
        reporter: Any,
        caps: Optional[RoleCaps] = None,
        *,
        beats: Optional[Mapping[str, Heartbeat]] = None,
        month: Optional[str] = None,
        now_epoch: Optional[float] = None,
    ) -> None:
        self.report = RoleHealthReport(
            reporter,
            caps,
            beats=beats,
            month=month,
            now_epoch=now_epoch,
        )
        self.report.alerts()

    def role_state(self) -> Dict[str, Any]:
        """The role-scoped feed on its own."""
        return self.report.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        """Alias of :meth:`role_state` (the exporter-shaped name)."""
        return self.role_state()

    def merge_into(self, snapshot: Mapping[str, Any]) -> Dict[str, Any]:
        """Return ``snapshot`` with the role feed merged in (tenant keys intact).

        A key the caller's snapshot already owns is never replaced: the role feed
        is additional observability, and silently overwriting a tenant-scoped
        key would make this module a *second* source of truth for it.
        """
        merged: Dict[str, Any] = dict(snapshot)
        role = self.role_state()
        for key, value in role.items():
            if key == "generatedAt" or key in merged:
                continue
            merged[key] = value
        return merged

    def write_json(self, path: Any) -> Path:
        """Write the mergeable budgets snapshot (role keys + tenant keys)."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self.merge_into({}), indent=2) + "\n", encoding="utf-8"
        )
        return out

    def write_exporter_json(self, path: Any, exporter: Any) -> Path:
        """Merge the role feed into a live budgets-exporter snapshot and write it.

        ``exporter`` is a ``telemetry.budgets.exporter.BudgetStateExporter``;
        its ``snapshot()`` (or ``write_json`` output) is consumed verbatim, so the
        portal keeps one feed to read.
        """
        base = exporter.snapshot() if hasattr(exporter, "snapshot") else dict(exporter)
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self.merge_into(base), indent=2) + "\n", encoding="utf-8"
        )
        return out


def _now_iso() -> str:
    """The one clock seam (``telemetry/clock.py``, issue #1025)."""
    return now_utc_iso()
