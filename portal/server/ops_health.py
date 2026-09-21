"""portal.server.ops_health — the ops/health/SLO serving surface (issue #342).


WHY this exists: the observability lane (``telemetry/observability``) already
computes everything an operator needs — per-tenant SLO verdicts over the real
span store, breach alerts with the outcome-not-liveness doctrine, usage rows and
a dashboard projection — but all of it is an *offline library plus a static HTML
file*. A shell cannot read a library, and a static file is stale the moment it
is written. This module is the *server half* of the ops/health surface: it
exposes the lane's own evaluation over the console's HTTP surface, live.

Cannibalize, do not duplicate. Every figure and every verdict is read through
the module that owns it:

* the spans — ``telemetry.observability.store.TraceStore`` over the live span
  feed (the fleet model-call-audit JSONL shape);
* the SLOs — ``telemetry.observability.slos``: the parameterized
  ``slo_templates/*.yaml`` are loaded with ``load_slo_templates`` and
  instantiated per tenant with ``definitions_from_templates``; evaluation is
  ``SloEvaluator``'s, over the lane's own attempt/outcome vocabulary;
* the breach alerts — ``telemetry.observability.breach.BreachDetector``;
* the severity ladder — ``telemetry.observability.severity.AlertStateMachine``
  (OK / WARNING / ALERT / NO_DATA / PAUSED, with breach *and* recovery);
* the dashboard projection and burn-rate ordering —
  ``telemetry.observability.dashboard.Dashboard`` (the same
  ``data()`` the static HTML is a thin render of);
* the usage rollups — ``telemetry.observability.usage.UsageReporter``.

Nothing here re-implements a reader, a threshold or a verdict. The adapter owns
transport shape and the honesty rules of the pane:

* **no data is not OK.** A subject with nothing to evaluate is ``NO_DATA`` —
  a tenant whose window holds no serve attempts, or whose newest span is older
  than its SLO window (the silent-tenant case), is *unknown*, never healthy.
* **per-agent verdicts are the tenant's own SLOs.** An agent is evaluated by
  feeding the lane's same definitions a per-agent view of the same store, so
  the numbers cannot drift from the tenant's; the agent's severity then rides
  the same state machine on its own subject key.
* **an unmeasured figure is ``null``.** Latency percentiles and error rates are
  ``null`` when there is no attempt to measure, never a fabricated ``0`` that
  would read as "no errors, instant".
* **figures and verdicts describe one window.** The latency/error/token figures
  are computed over the *same* window the SLO evaluator judged (the availability
  template's window, or the shortest declared one), ending at ``referenceTs`` —
  so a ``NO_DATA`` verdict and an empty figure set always agree instead of the
  pane showing a healthy-looking percentile beside an unknown state.
* **a corrupt store fails closed.** ``TraceStore`` refuses to skip a malformed
  line, so a truncated feed surfaces as a 500 rather than a green pane. A pane
  that cannot read its evidence must not report health.
* **a pause never buys health.** A paused subject keeps ``PAUSED`` *and* the
  state its verdicts justify (``observedState``), so pausing cannot hide a
  breach; pausing a subject that was never observed reports ``NO_DATA``.

The surface ships **feature-flag-gated OFF** (GR-5): the flag is declared in
``infra/feature-flags/registry.yaml`` under ``surfaces`` and read here through
the same reader the fleet and FinOps surfaces use
(``portal.server.fleet.surface_enabled``); while it is off the app refuses every
``/api/ops/*`` route before authentication.


---knowledge---
module_id: portal.server.ops_health
system: portal
app: server
solution_class: pattern
patterns: [read-model, delegate-never-re-derive, no-data-is-not-ok, single-window]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [OpsHealthReports]
invariants: "no data is not OK - a subject with nothing to evaluate is NO_DATA, never healthy; an unmeasured figure is null, never a fabricated 0"
gotchas: "figures and verdicts describe the same window the SLO evaluator judged"
related: ["#342"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from portal.server.fleet import surface_enabled
from telemetry.observability.breach import BreachDetector
from telemetry.observability.dashboard import Dashboard
from telemetry.observability.model import (
    KIND_GUARD,
    KIND_TRACE,
    SERVED_OUTCOMES,
    SpanRecord,
    epoch_of,
    is_attempt_kind,
    now_utc_iso,
    quantile,
)
from telemetry.observability.severity import (
    AlertStateMachine,
    SeverityState,
    STATES,
    STATE_DESCRIPTIONS,
    STATE_NO_DATA,
    STATE_SEVERITY,
)
from telemetry.observability.slos import (
    KIND_AVAILABILITY,
    SloDefinition,
    SloEvaluator,
    SloResult,
    SloTemplate,
    definitions_from_templates,
    load_slo_templates,
)
from telemetry.observability.store import TraceStore
from telemetry.observability.usage import (
    GROUP_AGENT,
    GROUP_MODEL,
    GROUP_PROVIDER,
    GROUP_TENANT,
    UsageReporter,
)

#: The registry surface key that gates this endpoint family.
OPS_HEALTH_SURFACE = "ops_health"
#: The flag declaration read at boot (repo-root relative).
REGISTRY_RELATIVE = Path("infra") / "feature-flags" / "registry.yaml"
#: The live span feed the pane reads (the console's runtime dir, never committed).
DEFAULT_SPANS_STORE = Path(".telemetry") / "spans.jsonl"
#: Where the observability lane's parameterized SLO templates live.
SLO_TEMPLATES_RELATIVE = Path("telemetry") / "observability" / "slo_templates"
#: Operational pauses: an operator-recorded reason a subject is not evaluated.
DEFAULT_PAUSES_FILE = Path(".telemetry") / "ops-pauses.json"
#: Usage dimensions the pane rolls up (the per-agent latency/token figures).
USAGE_DIMENSIONS = (GROUP_TENANT, GROUP_AGENT, GROUP_PROVIDER, GROUP_MODEL)
#: The subject key a span with no ``agentId`` is rolled up under, so an
#: unattributed call is visible instead of silently dropped from the pane.
UNATTRIBUTED_AGENT = "(unattributed)"
#: Span kinds that are not calls (the lane's usage notion of a billable call).
NON_CALL_KINDS = frozenset({KIND_TRACE, KIND_GUARD})


def _error_rate_pct(attempts: int, failed: int) -> Optional[float]:
    """Failed-attempt percentage, or ``None`` when there is nothing to measure."""
    if attempts <= 0:
        return None
    return round(100.0 * failed / attempts, 4)


class OpsHealthReports:
    """Projects the observability lane's health/SLO/breach state over HTTP.

    ``enabled`` is resolved from the feature-flag registry unless supplied
    explicitly (tests pass it; the server lets the registry decide).

    ``spans_store_path`` points the pane at a span feed (the repo's own runtime
    store by default); an absent file is an honest empty feed — every subject
    then reports ``NO_DATA``, never ``OK``. ``reference_ts_iso`` pins the SLO
    window end so an offline check is reproducible; a live server leaves it
    unset and the window ends at the newest span in the feed. ``pauses_path``
    is the operator pause file: a subject listed there is ``PAUSED`` while it is
    listed and resumes the moment it is removed.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        registry_path: Optional[Path | str] = None,
        spans_store_path: Optional[Path | str] = None,
        slo_templates_dir: Optional[Path | str] = None,
        pauses_path: Optional[Path | str] = None,
        reference_ts_iso: Optional[str] = None,
        recovery_confirmations: int = 1,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.registry_path = Path(registry_path) if registry_path is not None else None
        if enabled is None:
            enabled = surface_enabled(
                self.repo_root,
                registry_path=self.registry_path,
                surface=OPS_HEALTH_SURFACE,
            )
        self.enabled = bool(enabled)
        self.spans_store_path = (
            Path(spans_store_path)
            if spans_store_path is not None
            else self.repo_root / DEFAULT_SPANS_STORE
        )
        self.slo_templates_dir = (
            Path(slo_templates_dir)
            if slo_templates_dir is not None
            else self.repo_root / SLO_TEMPLATES_RELATIVE
        )
        self.pauses_path = (
            Path(pauses_path)
            if pauses_path is not None
            else self.repo_root / DEFAULT_PAUSES_FILE
        )
        self.reference_ts_iso = reference_ts_iso
        self.machine = AlertStateMachine(
            recovery_confirmations=recovery_confirmations, clock=clock
        )
        self._templates: Optional[List[SloTemplate]] = None
        self._file_paused: set[str] = set()

    # -- live readers -------------------------------------------------------
    def spans(self) -> List[SpanRecord]:
        """Every span in the live feed (an absent feed is an empty list).

        Read per call, never cached: the pane must show the *next* request's
        telemetry, not the telemetry that existed when the server booted.
        """
        if not self.spans_store_path.is_file():
            return []
        return TraceStore(path=str(self.spans_store_path)).all_spans()

    @property
    def templates(self) -> List[SloTemplate]:
        """The observability lane's parameterized SLO templates."""
        if self._templates is None:
            self._templates = load_slo_templates(str(self.slo_templates_dir))
        return self._templates

    def definitions(self, tenant_id: str) -> List[SloDefinition]:
        """The tenant's SLOs, instantiated from the lane's templates."""
        return definitions_from_templates(self.templates, [tenant_id])

    # -- universe -----------------------------------------------------------
    def tenant_ids(self) -> List[str]:
        """Tenants the live span feed knows, sorted (never a static list)."""
        return sorted({span.tenant_id for span in self.spans() if span.tenant_id})

    def feed_state(self, spans: Sequence[SpanRecord]) -> Dict[str, Any]:
        """What the pane is reading — a silent pane must say so.

        An absent feed is not an empty dashboard an operator can read as "all
        quiet": it is the reason there is nothing to report, so it is stated.
        """
        present = self.spans_store_path.is_file()
        empty_note = (
            "no telemetry is being read: nothing is measured, so nothing is "
            "healthy (every subject would read NO_DATA)"
        )
        return {
            "path": str(self.spans_store_path),
            "present": present,
            "spans": len(spans),
            "lastSpanAt": self._latest_ts(spans),
            "note": empty_note if not spans else None,
        }

    def known_tenant(self, tenant_id: str) -> bool:
        """Whether the live feed knows this tenant (the 404 gate)."""
        return tenant_id in self.tenant_ids()

    # -- reads --------------------------------------------------------------
    def overview(self, tenant_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """Per-tenant state + per-agent states + the fired alerts."""
        spans = self.spans()
        wanted = self._scope(tenant_ids, spans)
        self._apply_pauses(wanted, spans)
        reference = self._reference(spans)
        rows: List[Dict[str, Any]] = []
        agents: List[Dict[str, Any]] = []
        alerts: List[Dict[str, Any]] = []
        for tenant_id in wanted:
            tenant_spans = self._tenant_spans(spans, tenant_id)
            definitions = self.definitions(tenant_id)
            results = self._evaluate(tenant_spans, definitions, reference)
            state = self.machine.evaluate(tenant_id, results)
            agent_rows = self._agent_rows(tenant_spans, definitions, reference)
            agents.extend(agent_rows)
            alerts.extend(
                alert.to_dict() for alert in BreachDetector().detect(results)
            )
            rows.append(
                self._tenant_row(
                    tenant_id, tenant_spans, definitions, results, state,
                    agent_rows, reference,
                )
            )
        return {
            "generatedAt": self._now(),
            "referenceTs": reference,
            "feed": self.feed_state(spans),
            "states": self.state_vocabulary(),
            "totals": self._totals(rows, agents),
            "tenants": rows,
            "agents": agents,
            "firedAlerts": alerts,
        }

    def agents(self, tenant_id: str) -> Dict[str, Any]:
        """Per-agent status: state, latency, error rate and tokens."""
        spans = self.spans()
        tenant_spans = self._tenant_spans(spans, tenant_id)
        reference = self._reference(spans)
        self._apply_pauses([tenant_id], spans)
        rows = self._agent_rows(
            tenant_spans, self.definitions(tenant_id), reference
        )
        return {
            "generatedAt": self._now(),
            "referenceTs": reference,
            "tenantId": tenant_id,
            "feed": self.feed_state(spans),
            "states": self.state_vocabulary(),
            "agents": rows,
        }

    def slos(self, tenant_id: str) -> Dict[str, Any]:
        """The tenant's SLO verdicts (the lane's own evaluation) + burn rate."""
        spans = self.spans()
        tenant_spans = self._tenant_spans(spans, tenant_id)
        reference = self._reference(spans)
        self._apply_pauses([tenant_id], spans)
        definitions = self.definitions(tenant_id)
        results = self._evaluate(tenant_spans, definitions, reference)
        state = self.machine.evaluate(tenant_id, results)
        burn = Dashboard(
            TraceStore(spans=tenant_spans), slo_results=results
        ).data()["burnRate"]
        return {
            "generatedAt": self._now(),
            "referenceTs": reference,
            "tenantId": tenant_id,
            "feed": self.feed_state(spans),
            "figureWindowSeconds": self._figure_window(definitions),
            "state": state.to_dict(),
            "templates": [template.to_dict() for template in self.templates],
            "results": [result.to_dict() for result in results],
            "burnRate": burn,
        }

    def alerts(self, tenant_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """Breach alerts, the severity ladder and the transition history.

        ``fired`` is the observability lane's own ``BreachDetector`` over each
        tenant's SLO results (the per-SLO alarms). The per-agent view is the
        state machine's, so an agent that breaches shows up as ``ALERT`` on its
        own subject key without duplicating the tenant's SLO alarms.
        """
        spans = self.spans()
        wanted = self._scope(tenant_ids, spans)
        self._apply_pauses(wanted, spans)
        reference = self._reference(spans)
        fired: List[Dict[str, Any]] = []
        states: List[Dict[str, Any]] = []
        agents: List[Dict[str, Any]] = []
        for tenant_id in wanted:
            tenant_spans = self._tenant_spans(spans, tenant_id)
            definitions = self.definitions(tenant_id)
            results = self._evaluate(tenant_spans, definitions, reference)
            state = self.machine.evaluate(tenant_id, results)
            agent_rows = self._agent_rows(tenant_spans, definitions, reference)
            agents.extend(row["state"] for row in agent_rows)
            fired.extend(alert.to_dict() for alert in BreachDetector().detect(results))
            states.append(state.to_dict())
        subjects = [state["subject"] for state in states] + [
            agent["subject"] for agent in agents
        ]
        return {
            "generatedAt": self._now(),
            "referenceTs": reference,
            "feed": self.feed_state(spans),
            "states": self.state_vocabulary(),
            "fired": fired,
            "tenants": states,
            "agents": agents,
            "history": {
                subject: [move.to_dict() for move in self.machine.history(subject)]
                for subject in subjects
            },
        }

    def dashboard(self, tenant_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """The observability lane's canonical dashboard projection, scoped.

        The static HTML the lane renders is a thin render of exactly this
        document, so a shell receives what the offline dashboard would draw.
        """
        spans = self.spans()
        wanted = self._scope(tenant_ids, spans)
        self._apply_pauses(wanted, spans)
        reference = self._reference(spans)
        store = TraceStore(spans=spans)
        results: List[SloResult] = []
        alerts = []
        for tenant_id in wanted:
            tenant_results = self._evaluate(
                self._tenant_spans(spans, tenant_id),
                self.definitions(tenant_id),
                reference,
            )
            results.extend(tenant_results)
            alerts.extend(BreachDetector().detect(tenant_results))
        usage = UsageReporter(store, dimensions=USAGE_DIMENSIONS).report()
        data = Dashboard(
            store, slo_results=results, usage_rows=usage, alerts=alerts
        ).data()
        keep = set(wanted)
        data["tenants"] = [
            row for row in data["tenants"] if row["tenantId"] in keep
        ]
        data["sloResults"] = [
            row for row in data["sloResults"] if row["tenantId"] in keep
        ]
        data["alerts"] = [
            row for row in data["alerts"] if row["tenantId"] in keep
        ]
        data["usage"] = [
            row for row in data["usage"] if row["tenantId"] in keep
        ]
        data["burnRate"] = [
            row for row in data["burnRate"] if row["tenantId"] in keep
        ]
        data["scope"] = sorted(keep)
        data["stateVocabulary"] = self.state_vocabulary()
        data["feed"] = self.feed_state(spans)
        return data

    # -- vocabulary ---------------------------------------------------------
    @staticmethod
    def state_vocabulary() -> Dict[str, Dict[str, str]]:
        """The closed state vocabulary a client renders (never a guessed badge)."""
        return {
            state: {
                "severity": STATE_SEVERITY[state],
                "description": STATE_DESCRIPTIONS[state],
            }
            for state in STATES
        }

    # -- building blocks ----------------------------------------------------
    def _scope(
        self, tenant_ids: Optional[Sequence[str]], spans: Sequence[SpanRecord]
    ) -> List[str]:
        """The tenants to serve: the caller's set, narrowed to the live feed."""
        live = sorted({span.tenant_id for span in spans if span.tenant_id})
        if tenant_ids is None:
            return live
        wanted = set(tenant_ids)
        return [tenant for tenant in live if tenant in wanted]

    def _tenant_spans(
        self, spans: Sequence[SpanRecord], tenant_id: str
    ) -> List[SpanRecord]:
        return [span for span in spans if span.tenant_id == tenant_id]

    def _reference(self, spans: Sequence[SpanRecord]) -> Optional[str]:
        """When "now" is: the pinned reference, else the newest span's timestamp.

        Anchoring on the newest span keeps an offline evaluation reproducible
        and makes the silent-tenant case honest — a tenant whose spans stopped
        arriving falls out of its own SLO window and reads ``NO_DATA``.
        """
        if self.reference_ts_iso:
            return self.reference_ts_iso
        if not spans:
            return None
        return max(spans, key=lambda span: epoch_of(span.ts)).ts

    def _evaluate(
        self,
        spans: Sequence[SpanRecord],
        definitions: Sequence[SloDefinition],
        reference: Optional[str],
    ) -> List[SloResult]:
        """Evaluate SLOs over exactly these spans (the lane's own evaluator)."""
        evaluator = SloEvaluator(TraceStore(spans=list(spans)))
        return evaluator.evaluate_all(
            list(definitions), reference_ts_iso=reference
        )

    @staticmethod
    def _figure_window(definitions: Sequence[SloDefinition]) -> int:
        """The window the pane's figures describe.

        The availability SLO's window when one exists (it is the serve-health
        window every other figure is about), else the shortest declared window
        — never a window of the adapter's own invention.
        """
        availability = [
            definition.window_seconds
            for definition in definitions
            if definition.kind == KIND_AVAILABILITY
        ]
        if availability:
            return min(availability)
        return min(
            (definition.window_seconds for definition in definitions),
            default=0,
        )

    @staticmethod
    def _windowed(
        spans: Sequence[SpanRecord], reference: Optional[str], window_seconds: int
    ) -> List[SpanRecord]:
        """Spans inside the figure window (inclusive bounds, as the lane uses)."""
        if reference is None or window_seconds <= 0:
            return []
        end = epoch_of(reference)
        start = end - window_seconds
        return [
            span for span in spans if start <= epoch_of(span.ts) <= end
        ]

    def _agent_rows(
        self,
        tenant_spans: Sequence[SpanRecord],
        definitions: Sequence[SloDefinition],
        reference: Optional[str],
    ) -> List[Dict[str, Any]]:
        """One row per agent, with its own SLO verdicts and severity state.

        The agent is judged by the *tenant's own* SLO definitions fed a
        per-agent view of the same store, so an agent's availability cannot
        drift from the tenant's — it is the same objective over a narrower
        sample. The figures describe the same window the verdicts do.

        A row exists for every agent a span names. A span that names none joins
        the ``(unattributed)`` row only when it is actually a call, so a trace
        root's correlation anchor cannot invent an agent — while a genuinely
        unattributed call stays visible instead of being dropped.
        """
        window_seconds = self._figure_window(definitions)
        grouped: Dict[str, List[SpanRecord]] = {}
        for span in tenant_spans:
            if span.agent_id:
                grouped.setdefault(span.agent_id, []).append(span)
            elif span.kind not in NON_CALL_KINDS:
                grouped.setdefault(UNATTRIBUTED_AGENT, []).append(span)
        rows: List[Dict[str, Any]] = []
        for agent_id in sorted(grouped):
            agent_spans = grouped[agent_id]
            subject = f"{self._tenant_of(agent_spans)}/{agent_id}"
            results = self._evaluate(agent_spans, definitions, reference)
            state = self.machine.evaluate(subject, results)
            rows.append(
                {
                    "agentId": agent_id,
                    "tenantId": self._tenant_of(agent_spans),
                    "subject": subject,
                    "state": state.to_dict(),
                    "verdicts": {
                        result.name: result.verdict for result in results
                    },
                    "windowSeconds": window_seconds,
                    **self._figures(agent_spans, reference, window_seconds),
                }
            )
        return rows

    @staticmethod
    def _tenant_of(spans: Sequence[SpanRecord]) -> str:
        """The tenant a span group belongs to (groups are built per tenant)."""
        return spans[0].tenant_id if spans else ""

    def _figures(
        self,
        spans: Sequence[SpanRecord],
        reference: Optional[str],
        window_seconds: int,
    ) -> Dict[str, Any]:
        """Measured calls/attempts/latency/errors/tokens over the figure window.

        Every rate is ``None`` when its sample is empty — an unmeasured figure
        is unknown, never a comforting ``0``.
        """
        windowed = self._windowed(spans, reference, window_seconds)
        attempts = self._attempts(windowed)
        served = sum(1 for span in attempts if span.outcome in SERVED_OUTCOMES)
        failed = len(attempts) - served
        return {
            "spans": len(windowed),
            "calls": sum(1 for span in windowed if span.kind not in NON_CALL_KINDS),
            "attempts": len(attempts),
            "served": served,
            "failed": failed,
            "errorRatePct": _error_rate_pct(len(attempts), failed),
            "latencyMs": self._latency([span.latency_ms for span in attempts]),
            "tokens": self._tokens(windowed),
            "costUsd": round(
                sum(span.estimated_cost_usd for span in windowed), 6
            ),
            "cacheHits": sum(
                1 for span in windowed if span.outcome == "cache_hit"
            ),
            "services": sorted({span.service for span in windowed}),
            "lastSpanAt": self._latest_ts(windowed),
        }

    def _tenant_row(
        self,
        tenant_id: str,
        tenant_spans: Sequence[SpanRecord],
        definitions: Sequence[SloDefinition],
        results: Sequence[SloResult],
        state: SeverityState,
        agent_rows: Sequence[Dict[str, Any]],
        reference: Optional[str],
    ) -> Dict[str, Any]:
        window_seconds = self._figure_window(definitions)
        counts: Dict[str, int] = {}
        for row in agent_rows:
            key = row["state"]["state"]
            counts[key] = counts.get(key, 0) + 1
        return {
            "tenantId": tenant_id,
            "state": state.to_dict(),
            "verdicts": {result.name: result.verdict for result in results},
            "hasData": bool(tenant_spans),
            "windowSeconds": window_seconds,
            "agents": {
                "total": len(agent_rows),
                "byState": {
                    known: counts.get(known, 0) for known in sorted(STATES)
                },
            },
            **self._figures(tenant_spans, reference, window_seconds),
        }

    @staticmethod
    def _attempts(spans: Sequence[SpanRecord]) -> List[SpanRecord]:
        """Completed serve attempts — the same sample the SLO evaluator uses."""
        return [
            span
            for span in spans
            if is_attempt_kind(span.kind) and span.is_attempt
        ]

    @staticmethod
    def _latency(latencies: Sequence[float]) -> Dict[str, Any]:
        """Latency percentiles, ``null`` (never ``0``) with nothing to measure."""
        values = list(latencies)
        p50 = quantile(values, 0.5)
        p95 = quantile(values, 0.95)
        return {
            "samples": len(values),
            "p50": round(p50, 3) if p50 is not None else None,
            "p95": round(p95, 3) if p95 is not None else None,
            "max": round(max(values), 3) if values else None,
        }

    @staticmethod
    def _tokens(spans: Sequence[SpanRecord]) -> Dict[str, int]:
        return {
            "input": sum(span.input_tokens for span in spans),
            "output": sum(span.output_tokens for span in spans),
            "total": sum(span.tokens for span in spans),
        }

    @staticmethod
    def _latest_ts(spans: Sequence[SpanRecord]) -> Optional[str]:
        if not spans:
            return None
        return max(spans, key=lambda span: epoch_of(span.ts)).ts

    @staticmethod
    def _totals(
        tenant_rows: Sequence[Dict[str, Any]],
        agent_rows: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Fleet-wide counts, including how many subjects are not OK."""
        tenants: Dict[str, int] = {}
        agents: Dict[str, int] = {}
        for row in tenant_rows:
            tenants[row["state"]["state"]] = tenants.get(row["state"]["state"], 0) + 1
        for row in agent_rows:
            agents[row["state"]["state"]] = agents.get(row["state"]["state"], 0) + 1
        return {
            "tenants": {known: tenants.get(known, 0) for known in sorted(STATES)},
            "agents": {known: agents.get(known, 0) for known in sorted(STATES)},
            "attention": sum(
                agents.get(state, 0)
                for state in ("WARNING", "ALERT", STATE_NO_DATA)
            ),
        }

    # -- operational pauses -------------------------------------------------
    def _apply_pauses(
        self, tenant_ids: Sequence[str], spans: Sequence[SpanRecord]
    ) -> None:
        """Reconcile the operator pause file with the state machine.

        A subject listed in the file is paused (with its reason and actor); a
        subject *removed* from the file is resumed, so a pause cannot outlive
        the record that justified it. Subjects paused elsewhere in the process
        are left alone.
        """
        desired = {
            entry["subject"]: entry
            for entry in self._pause_entries()
            if entry.get("subject")
        }
        for subject in sorted(self._file_paused - set(desired)):
            self.machine.resume(subject)
            self._file_paused.discard(subject)
        for subject, entry in sorted(desired.items()):
            if subject in self._file_paused:
                continue
            if self._known_subject(subject, tenant_ids, spans):
                self.machine.pause(
                    subject,
                    reason=str(entry.get("reason") or "operator pause"),
                    actor=str(entry.get("actor") or ""),
                )
                self._file_paused.add(subject)

    def _pause_entries(self) -> List[Dict[str, Any]]:
        """The pause file's entries (an absent/unreadable file is no pauses)."""
        if not self.pauses_path.is_file():
            return []
        try:
            document = json.loads(self.pauses_path.read_text(encoding="utf-8"))
        except ValueError:
            return []
        entries = document.get("pauses") if isinstance(document, dict) else document
        if not isinstance(entries, list):
            return []
        return [entry for entry in entries if isinstance(entry, dict)]

    @staticmethod
    def _known_subject(
        subject: str, tenant_ids: Sequence[str], spans: Sequence[SpanRecord]
    ) -> bool:
        """Only a subject that exists (tenant, or tenant/agent) can be paused."""
        if "/" not in subject:
            return subject in tenant_ids
        tenant_id, _, agent_id = subject.partition("/")
        if tenant_id not in tenant_ids:
            return False
        return any(
            (span.agent_id or UNATTRIBUTED_AGENT) == agent_id
            for span in spans
            if span.tenant_id == tenant_id
        )

    def _now(self) -> str:
        return now_utc_iso()


__all__ = [
    "OPS_HEALTH_SURFACE",
    "DEFAULT_SPANS_STORE",
    "SLO_TEMPLATES_RELATIVE",
    "DEFAULT_PAUSES_FILE",
    "USAGE_DIMENSIONS",
    "UNATTRIBUTED_AGENT",
    "NON_CALL_KINDS",
    "OpsHealthReports",
]
