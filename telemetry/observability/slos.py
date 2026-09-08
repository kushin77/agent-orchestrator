"""telemetry/observability — per-tenant SLO definition + evaluation (#32).

Per-tenant Service Level Objectives over the telemetry store, parameterized
from offline YAML templates (``slo_templates/`` — availability, latency and
cost kinds, adapted from the monitoring-stack Sloth templates) with honest
evaluation over actual span outcomes.

SLO semantics
-------------

- ``availability`` — target ratio of served attempts (outcome
  ``success``/``cache_hit``) over completed serve attempts in the window.
  Error budget = ``1 - target_ratio``.
- ``latency`` — the ``percentile``-th latency of completed attempts must not
  exceed ``target_ms``.  Allowance = ``1 - percentile`` of attempts may be
  slower.
- ``cost`` — total estimated spend in the window must not exceed
  ``budget_usd``.

Outcome-not-liveness (no-false-green): evaluation is over **actual span
outcomes**.  A tenant with **no data at all in the window** gets
``verdict == NO_DATA`` with ``missed_window == True`` — that is *not* read as
health by the breach detector (a silent tenant alerts; it is never assumed
healthy).  ``make slo-eval`` exits non-zero on any BREACHED/AT_RISK/NO_DATA
verdict.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Optional

import yaml

from telemetry.observability.model import (
    KIND_TRACE,
    SERVED_OUTCOMES,
    SpanRecord,
    epoch_of,
    is_attempt_kind,
    now_utc_iso,
    quantile,
)
from telemetry.observability.store import TraceStore

# --------------------------------------------------------------------------- #
# SLO kinds + verdicts
# --------------------------------------------------------------------------- #
KIND_AVAILABILITY = "availability"
KIND_LATENCY = "latency"
KIND_COST = "cost"
SLO_KINDS = frozenset({KIND_AVAILABILITY, KIND_LATENCY, KIND_COST})

VERDICT_OK = "OK"
VERDICT_AT_RISK = "AT_RISK"
VERDICT_BREACHED = "BREACHED"
VERDICT_NO_DATA = "NO_DATA"
VERDICTS = frozenset({VERDICT_OK, VERDICT_AT_RISK, VERDICT_BREACHED, VERDICT_NO_DATA})

#: Error-budget-consumed ratio above which a window is AT_RISK (but not yet
#: breached).  Consumed >= 1.0 is a breach.
AT_RISK_BUDGET_RATIO = 0.5


@dataclass(frozen=True)
class SloDefinition:
    """One per-tenant SLO.

    Exactly one of ``target_ratio`` (availability), ``target_ms`` (latency)
    or ``budget_usd`` (cost) is set, selected by ``kind``.
    """

    name: str
    tenant_id: str
    kind: str
    description: str = ""
    window_seconds: int = 3600
    target_ratio: Optional[float] = None
    target_ms: Optional[float] = None
    percentile: float = 0.95
    budget_usd: Optional[float] = None
    service: Optional[str] = None
    severity: str = "warning"

    def __post_init__(self) -> None:
        if not self.name or not self.tenant_id:
            raise ValueError("SLO name and tenant_id are required")
        if self.kind not in SLO_KINDS:
            raise ValueError(f"unknown SLO kind: {self.kind!r}")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if self.kind == KIND_AVAILABILITY:
            if self.target_ratio is None:
                raise ValueError("availability SLO requires target_ratio")
            if not 0.0 < self.target_ratio <= 1.0:
                raise ValueError("target_ratio must be in (0, 1]")
        elif self.kind == KIND_LATENCY:
            if self.target_ms is None or self.target_ms <= 0.0:
                raise ValueError("latency SLO requires target_ms > 0")
            if not 0.0 < self.percentile <= 1.0:
                raise ValueError("percentile must be in (0, 1]")
        elif self.kind == KIND_COST:
            if self.budget_usd is None or self.budget_usd < 0.0:
                raise ValueError("cost SLO requires budget_usd >= 0")

    @property
    def error_budget_ratio(self) -> Optional[float]:
        """The allowed bad fraction (availability) or latency allowance."""
        if self.kind == KIND_AVAILABILITY:
            return 1.0 - (self.target_ratio or 0.0)
        if self.kind == KIND_LATENCY:
            return 1.0 - self.percentile
        return None

    @property
    def target(self) -> float:
        """Display target: ratio, ms, or budget dollars."""
        if self.kind == KIND_AVAILABILITY:
            return float(self.target_ratio or 0.0)
        if self.kind == KIND_LATENCY:
            return float(self.target_ms or 0.0)
        return float(self.budget_usd or 0.0)


@dataclass(frozen=True)
class SloResult:
    """Honest evaluation of one SLO over one window."""

    definition: SloDefinition
    window_start_iso: str
    window_end_iso: str
    verdict: str
    has_window_data: bool
    attempts: int = 0
    good_count: int = 0
    bad_count: int = 0
    violation_count: int = 0
    measured_ratio: Optional[float] = None
    measured_ms: Optional[float] = None
    spent_usd: float = 0.0
    budget_consumed_ratio: Optional[float] = None
    missed_window: bool = False

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"unknown SLO verdict: {self.verdict!r}")

    @property
    def is_ok(self) -> bool:
        return self.verdict == VERDICT_OK

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def tenant_id(self) -> str:
        return self.definition.tenant_id

    @property
    def kind(self) -> str:
        return self.definition.kind

    def to_dict(self) -> dict[str, Any]:
        return {
            "slo": self.definition.name,
            "tenantId": self.definition.tenant_id,
            "kind": self.definition.kind,
            "windowStart": self.window_start_iso,
            "windowEnd": self.window_end_iso,
            "verdict": self.verdict,
            "hasWindowData": self.has_window_data,
            "missedWindow": self.missed_window,
            "attempts": self.attempts,
            "goodCount": self.good_count,
            "badCount": self.bad_count,
            "violationCount": self.violation_count,
            "measuredRatio": self.measured_ratio,
            "measuredMs": self.measured_ms,
            "spentUsd": round(self.spent_usd, 6),
            "budgetConsumedRatio": (
                round(self.budget_consumed_ratio, 6)
                if self.budget_consumed_ratio is not None
                else None
            ),
            "target": self.definition.target,
        }


def _iso_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# SLO evaluation
# --------------------------------------------------------------------------- #
class SloEvaluator:
    """Evaluates :class:`SloDefinition` objects against a :class:`TraceStore`.

    All evaluation is over actual span outcomes in the window — never over
    heartbeat/liveness signals.
    """

    def __init__(self, store: TraceStore) -> None:
        self.store = store

    def evaluate(
        self,
        definition: SloDefinition,
        *,
        reference_ts_iso: Optional[str] = None,
    ) -> SloResult:
        """Evaluate one SLO over the window ending at ``reference_ts_iso``.

        The window defaults to ending at the store's newest span so offline
        evaluation is deterministic; callers (CLI/reporters) pass an explicit
        reference for "as of now" windows.
        """
        window_end_iso = reference_ts_iso or self._store_latest_ts()
        window_end_epoch = epoch_of(window_end_iso)
        window_start_epoch = window_end_epoch - definition.window_seconds
        window_start_iso = _iso_from_epoch(window_start_epoch)

        spans = self.store.all_spans()
        in_window: list[SpanRecord] = []
        for span in spans:
            if span.tenant_id != definition.tenant_id:
                continue
            if definition.service is not None and span.service != definition.service:
                continue
            ts = epoch_of(span.ts)
            if ts < window_start_epoch or ts > window_end_epoch:
                continue
            in_window.append(span)

        has_window_data = bool(in_window)
        attempts = [
            s
            for s in in_window
            if s.kind != KIND_TRACE and is_attempt_kind(s.kind) and s.is_attempt
        ]
        good = sum(1 for s in attempts if s.outcome in SERVED_OUTCOMES)
        bad = len(attempts) - good
        latencies = [s.latency_ms for s in attempts]
        spent = round(sum(s.estimated_cost_usd for s in in_window), 6)

        if definition.kind == KIND_AVAILABILITY:
            return self._availability(
                definition, window_start_iso, window_end_iso, has_window_data,
                attempts, good, bad,
            )
        if definition.kind == KIND_LATENCY:
            return self._latency(
                definition, window_start_iso, window_end_iso, has_window_data,
                attempts, latencies,
            )
        return self._cost(
            definition, window_start_iso, window_end_iso, has_window_data,
            spent,
        )

    def _store_latest_ts(self) -> str:
        spans = self.store.all_spans()
        if not spans:
            return now_utc_iso()
        return max(spans, key=lambda s: epoch_of(s.ts)).ts

    # -- per-kind evaluation ---------------------------------------------
    def _availability(self, definition, start_iso, end_iso, has_data,
                      attempts, good, bad):
        n = len(attempts)
        if not has_data or n == 0:
            return SloResult(
                definition, start_iso, end_iso, VERDICT_NO_DATA,
                has_window_data=has_data, attempts=n, good_count=good,
                bad_count=bad, missed_window=not has_data,
            )
        ratio = good / n
        budget = definition.error_budget_ratio or 0.0
        consumed = (bad / n) / budget if budget > 0 else float("inf")
        if ratio < (definition.target_ratio or 0.0) - 1e-9:
            verdict = VERDICT_BREACHED
        elif round(consumed, 9) >= AT_RISK_BUDGET_RATIO:
            verdict = VERDICT_AT_RISK
        else:
            verdict = VERDICT_OK
        return SloResult(
            definition, start_iso, end_iso, verdict,
            has_window_data=True, attempts=n, good_count=good, bad_count=bad,
            measured_ratio=round(ratio, 6), budget_consumed_ratio=consumed,
        )

    def _latency(self, definition, start_iso, end_iso, has_data,
                 attempts, latencies):
        n = len(attempts)
        if not has_data or n == 0:
            return SloResult(
                definition, start_iso, end_iso, VERDICT_NO_DATA,
                has_window_data=has_data, attempts=n,
                violation_count=0, missed_window=not has_data,
            )
        measured_ms = quantile(latencies, definition.percentile)
        violations = sum(1 for ms in latencies if ms > (definition.target_ms or 0.0))
        allowance = definition.error_budget_ratio or 0.0
        consumed = (violations / n) / allowance if allowance > 0 else float("inf")
        breached = (
            measured_ms is not None and measured_ms > (definition.target_ms or 0.0)
        ) or consumed > 1.0
        if breached:
            verdict = VERDICT_BREACHED
        elif round(consumed, 9) >= AT_RISK_BUDGET_RATIO:
            verdict = VERDICT_AT_RISK
        else:
            verdict = VERDICT_OK
        return SloResult(
            definition, start_iso, end_iso, verdict,
            has_window_data=True, attempts=n, violation_count=violations,
            measured_ms=measured_ms, budget_consumed_ratio=consumed,
        )

    def _cost(self, definition, start_iso, end_iso, has_data, spent):
        budget = definition.budget_usd or 0.0
        if not has_data:
            return SloResult(
                definition, start_iso, end_iso, VERDICT_NO_DATA,
                has_window_data=False, spent_usd=0.0,
                budget_consumed_ratio=0.0, missed_window=True,
            )
        consumed = spent / budget if budget > 0 else float("inf")
        if spent > budget:
            verdict = VERDICT_BREACHED
        elif round(consumed, 9) >= 0.8:
            verdict = VERDICT_AT_RISK
        else:
            verdict = VERDICT_OK
        return SloResult(
            definition, start_iso, end_iso, verdict,
            has_window_data=True, spent_usd=spent,
            budget_consumed_ratio=consumed,
        )

    def evaluate_all(
        self,
        definitions: list[SloDefinition],
        *,
        reference_ts_iso: Optional[str] = None,
    ) -> list[SloResult]:
        """Evaluate every definition (stable order)."""
        return [
            self.evaluate(d, reference_ts_iso=reference_ts_iso)
            for d in definitions
        ]


# --------------------------------------------------------------------------- #
# Parameterized SLO templates (offline YAML)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SloTemplate:
    """A parameterized SLO template loaded from ``slo_templates/*.yaml``.

    Instantiate with :meth:`instantiate` to produce a per-tenant
    :class:`SloDefinition`.
    """

    name: str
    kind: str
    description: str
    window_seconds: int
    target_ratio: Optional[float] = None
    target_ms: Optional[float] = None
    percentile: float = 0.95
    budget_usd: Optional[float] = None
    severity: str = "warning"
    source: str = ""

    def instantiate(
        self,
        tenant_id: str,
        *,
        name: Optional[str] = None,
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> SloDefinition:
        """Create a tenant-scoped SLO from this template."""
        overrides = dict(overrides or {})
        return SloDefinition(
            name=name or f"{self.name}:{tenant_id}",
            tenant_id=tenant_id,
            kind=overrides.get("kind", self.kind),
            description=overrides.get("description", self.description),
            window_seconds=int(overrides.get("window_seconds", self.window_seconds)),
            target_ratio=overrides.get("target_ratio", self.target_ratio),
            target_ms=overrides.get("target_ms", self.target_ms),
            percentile=float(overrides.get("percentile", self.percentile)),
            budget_usd=overrides.get("budget_usd", self.budget_usd),
            severity=overrides.get("severity", self.severity),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "windowSeconds": self.window_seconds,
            "targetRatio": self.target_ratio,
            "targetMs": self.target_ms,
            "percentile": self.percentile,
            "budgetUsd": self.budget_usd,
            "severity": self.severity,
            "source": self.source,
        }


def load_slo_templates(directory: str) -> list[SloTemplate]:
    """Load every ``*.yaml`` template from ``directory`` (sorted by name).

    Raises ``ValueError`` on an unparseable or invalid template so a bad
    template cannot silently disappear from the SLO corpus.
    """
    templates: list[SloTemplate] = []
    names = sorted(
        f for f in os.listdir(directory)
        if f.endswith((".yaml", ".yml")) and not f.startswith(".")
    )
    for name in names:
        path = os.path.join(directory, name)
        with open(path, "r", encoding="utf-8") as handle:
            try:
                data = yaml.safe_load(handle)
            except yaml.YAMLError as exc:
                raise ValueError(f"template {name}: invalid YAML: {exc}") from exc
        if not isinstance(data, Mapping):
            raise ValueError(f"template {name}: top level must be a mapping")
        spec = data.get("spec")
        if not isinstance(spec, Mapping):
            raise ValueError(f"template {name}: missing mapping 'spec'")
        templates.append(_template_from_spec(name, spec))
    return templates


def _template_from_spec(name: str, spec: Mapping[str, Any]) -> SloTemplate:
    try:
        kind = str(spec["sloKind"])
    except KeyError as exc:
        raise ValueError(f"template {name}: missing 'spec.sloKind'") from exc
    if kind not in SLO_KINDS:
        raise ValueError(f"template {name}: unknown sloKind {kind!r}")
    return SloTemplate(
        name=name[:-5] if name.endswith(".yaml") else name,
        kind=kind,
        description=str(spec.get("description", "")),
        window_seconds=int(spec.get("windowSeconds", 3600)),
        target_ratio=(
            float(spec["targetRatio"]) if spec.get("targetRatio") is not None else None
        ),
        target_ms=(
            float(spec["targetMs"]) if spec.get("targetMs") is not None else None
        ),
        percentile=float(spec.get("percentile", 0.95)),
        budget_usd=(
            float(spec["budgetUsd"]) if spec.get("budgetUsd") is not None else None
        ),
        severity=str(spec.get("severity", "warning")),
        source=name,
    )


def definitions_from_templates(
    templates: list[SloTemplate],
    tenants: list[str],
    *,
    overrides: Optional[Mapping[str, Any]] = None,
) -> list[SloDefinition]:
    """Instantiate every template for every tenant (cartesian, deterministic).

    ``overrides`` maps a template name to an override mapping, letting callers
    tune targets per tenant without editing the templates.
    """
    out: list[SloDefinition] = []
    for template in sorted(templates, key=lambda t: t.name):
        for tenant_id in tenants:
            tmpl_overrides = dict((overrides or {}).get(template.name, {}))
            out.append(
                template.instantiate(tenant_id, overrides=tmpl_overrides)
            )
    return out


__all__ = [
    "KIND_AVAILABILITY",
    "KIND_LATENCY",
    "KIND_COST",
    "SLO_KINDS",
    "VERDICT_OK",
    "VERDICT_AT_RISK",
    "VERDICT_BREACHED",
    "VERDICT_NO_DATA",
    "VERDICTS",
    "AT_RISK_BUDGET_RATIO",
    "SloDefinition",
    "SloResult",
    "SloEvaluator",
    "SloTemplate",
    "load_slo_templates",
    "definitions_from_templates",
]
