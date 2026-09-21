"""telemetry/observability/exposition — the OTLP/HTTP push exporter (#497).

---knowledge---
module_id: telemetry.observability.exposition
system: telemetry
app: observability
solution_class: enterprise
patterns: [flag-gated-off, otlp-http-push, frozen-exit-boundary, fail-closed]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [read_surface_default, PRODUCER_SERVICE, SIGNAL_METRICS, SIGNAL_LOGS, EXPOSITION_SURFACE, REASON_*]
invariants: "the exporter is OFF unless the surface flag is on; every refusal names a reason rather than failing quietly"
gotchas: "ADR-0022 froze the telemetry exit boundary, so this is the single declared way telemetry leaves the process"
related: ["#497", "#1510"]
do_not_duplicate: null
---knowledge---


WHY this module exists
----------------------
The telemetry pillar is real and complete — per-tenant SLOs with a closed
kind + verdict vocabulary (``slos.py``), the machine-readable budget/quota/audit
state snapshot (``telemetry/budgets/exporter.py``), the metering usage
aggregate (``telemetry/metering/report.py``) — and **none of it can leave the
process**: the only outputs are local JSONL files.  The fleet monitoring plane
therefore cannot observe this control plane at all.

ADR-0022 froze the exit boundary *before* any lane wrote code.  This module is
the one implementation of that decision, and it implements the decided shape
literally rather than the issue title's original framing:

* **D2 — push, not pull.**  The service is a Google Cloud Run v2 service:
  ephemeral, scaling to zero, no pod, no sidecar, no ``ServiceMonitor`` CRD, no
  Kubernetes annotation discovery path.  So the transport is an **OTLP/HTTP
  push** from the producer to the plane (Prometheus remote-write is the accepted
  alternate encoding, and is a plane-owned configuration value, not a second
  code path here).  This module ships **no HTTP exposition route**: no
  ``/metrics``, no server, no bound port, no route to gate.
* **D4 — the SLO vocabulary is imported and rendered verbatim.**  The kinds and
  the verdicts come from :mod:`telemetry.observability.slos`; this module holds
  **no second literal** for either, and it renders the verdict the evaluator
  computed rather than re-deriving one from raw counters.  ``BREACHED`` and
  ``NO_DATA`` leave the process as exactly those tokens — never coerced to a
  healthy verdict, never dropped.
* **D5 — a closed, bounded label set.**  Every metric identity carries exactly
  ``{service, slo_kind, slo_id, verdict, tenant}`` and nothing else.  Request /
  span / trace / run / session ids, agent ids, prompt or model text, free-form
  errors, id-bearing paths, commit SHAs and host identity are **refused as
  identity** — they are precisely the dimensions the plane's own cardinality
  rule (``kushin77/monitoring-stack#178``) drops.
* **GR-5 — flag-gated OFF.**  The surface is declared ``off`` in
  ``infra/feature-flags/registry.yaml`` and read here; while it is off nothing
  is rendered and nothing is sent.
* **GR-6 — no secret in code.**  The endpoint and any credential come from the
  environment only, default to nothing, and are never logged.
* **Inert when unconfigured.**  No endpoint means no export, no retry storm and
  never a fabricated healthy signal.
* **Periodic.**  :meth:`TelemetryExposition.run_periodically` re-renders on the
  configured interval so a window that could not be evaluated is published as
  the evaluator's ``NO_DATA`` verdict rather than vanishing into silence.

What deliberately does **not** ship here (ADR-0022's binding refusals): no
second Prometheus/Grafana/Alertmanager/Loki/Jaeger, no dashboard, no collector
or agent deployment, no scrape config, no recording or alert rules, and no write
of any kind to fleet state — a signal drives a ticket, never a silent action.

Consumption, never derivation
-----------------------------
The renderer is handed the *facts* the pillar already owns — ``SloResult``
objects, the ``BudgetStateExporter.snapshot()`` document and the metering
``UsageReporter.by_tenant()`` aggregate — via :class:`ExpositionInputs`, and it
transports them.  It never imports the budgets or metering packages, so it
cannot silently become a second authority for their content, and it never
re-derives a verdict, a position or a total.

Offline by construction
-----------------------
Importing this module performs no I/O and opens no socket: the push is made only
by an explicit :meth:`TelemetryExposition.export_once` call, and only once the
flag is on *and* an endpoint is configured.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Sequence

from telemetry.observability import slos

# --------------------------------------------------------------------------- #
# ADR-0022 D5 — the closed identity label set
# --------------------------------------------------------------------------- #
#: The constant ``service`` label every rendered signal carries.
PRODUCER_SERVICE = "agent-orchestrator"

#: The **complete** set of label dimensions an exported metric identity may
#: carry (ADR-0022 D5).  Ordered: the emitted attribute order follows it.
IDENTITY_LABELS: tuple[str, ...] = (
    "service",
    "slo_kind",
    "slo_id",
    "verdict",
    "tenant",
)

#: Label names refused as identity — a defect, not a style preference (D5).
#: These are the unbounded or user-controlled dimensions the plane's
#: cardinality rule drops; refusing them here means the plane's drop rule never
#: has to catch one of ours.  A session id is this fleet's ``pod_id``.
REFUSED_IDENTITY_LABELS: frozenset[str] = frozenset(
    {
        "request_id",
        "span_id",
        "trace_id",
        "run_id",
        "session_id",
        "session",
        "agent_id",
        "agent",
        "agent_name",
        "prompt",
        "prompt_text",
        "model",
        "model_name",
        "error",
        "error_string",
        "error_detail",
        "path",
        "http_path",
        "url",
        "uri",
        "sha",
        "commit",
        "git_sha",
        "host",
        "hostname",
        "instance",
        "instance_id",
        "pod",
        "pod_id",
        "container",
        "timestamp",
        "ts",
        "time",
    }
)

#: The OTLP signal paths appended to the configured base endpoint.
SIGNAL_METRICS = "metrics"
SIGNAL_LOGS = "logs"
SIGNALS: tuple[str, ...] = (SIGNAL_METRICS, SIGNAL_LOGS)

#: The instrumentation scope name carried on every emitted record.
SCOPE_NAME = "agent-orchestrator.telemetry.exposition"

# --------------------------------------------------------------------------- #
# Configuration (environment / secret manager only — GR-6)
# --------------------------------------------------------------------------- #
ENV_ENDPOINT = "AO_TELEMETRY_OTLP_ENDPOINT"
ENV_HEADERS = "AO_TELEMETRY_OTLP_HEADERS"
ENV_TIMEOUT_SECONDS = "AO_TELEMETRY_OTLP_TIMEOUT_SECONDS"
ENV_INTERVAL_SECONDS = "AO_TELEMETRY_EXPORT_INTERVAL_SECONDS"

#: Deliberately no default endpoint: an unset endpoint is an *unconfigured*
#: exporter, never a request aimed at a URL this repo guessed.
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_INTERVAL_SECONDS = 60.0

# --------------------------------------------------------------------------- #
# The feature-flag surface (GR-5)
# --------------------------------------------------------------------------- #
#: The ``surfaces`` key this exporter is gated by in the flag registry.
EXPOSITION_SURFACE = "telemetry_exposition"
REGISTRY_RELATIVE = Path("infra") / "feature-flags" / "registry.yaml"

# --------------------------------------------------------------------------- #
# Honest outcome reasons (never a silent success, never an SLO verdict)
# --------------------------------------------------------------------------- #
REASON_FLAG_OFF = "flag_off"
REASON_UNCONFIGURED = "unconfigured_endpoint"
REASON_DELIVERED = "delivered"
REASON_TRANSPORT_ERROR = "transport_error"
REASON_REJECTED = "rejected"

# --------------------------------------------------------------------------- #
# Snapshot kinds carried on the log signal
# --------------------------------------------------------------------------- #
SNAPSHOT_BUDGET = "budget"
SNAPSHOT_QUOTA = "quota"
SNAPSHOT_USAGE = "usage"
SNAPSHOT_BUDGET_AUDIT = "budget_audit"


def read_surface_default(
    repo_root: Path | str,
    *,
    registry_path: Optional[Path | str] = None,
    surface: str = EXPOSITION_SURFACE,
) -> str:
    """The flag registry's declared default for ``surface`` ("on" or "off").

    Fails closed: a missing registry, an unreadable or invalid document, a
    missing ``surfaces`` section, or a missing entry all read as ``"off"``.
    Only an explicit ``default: on`` (or boolean ``True``) turns it on, so the
    surface cannot ship enabled by accident.

    The reader is duplicated from ``portal/server/fleet.py`` rather than
    imported: ``portal`` is a *consumer* of this pillar, and telemetry importing
    portal would invert the dependency to save four lines.
    """
    path = (
        Path(registry_path)
        if registry_path is not None
        else Path(repo_root) / REGISTRY_RELATIVE
    )
    try:
        import yaml
    except ImportError:
        return "off"
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        # A malformed registry is *unusable configuration*, not a fatal error:
        # an unreadable document must leave the surface off, never on.
        return "off"
    if not isinstance(document, dict):
        return "off"
    surfaces = document.get("surfaces")
    if not isinstance(surfaces, dict):
        return "off"
    entry = surfaces.get(surface)
    if not isinstance(entry, dict):
        return "off"
    default = entry.get("default")
    if default is True or (
        isinstance(default, str) and default.strip().lower() == "on"
    ):
        return "on"
    return "off"


def surface_enabled(
    repo_root: Path | str,
    *,
    registry_path: Optional[Path | str] = None,
    surface: str = EXPOSITION_SURFACE,
) -> bool:
    """True only when the registry explicitly promotes ``surface``."""
    return (
        read_surface_default(repo_root, registry_path=registry_path, surface=surface)
        == "on"
    )


@dataclass(frozen=True)
class ExpositionConfig:
    """Where the export goes, and how often (never *whether* — that is the flag).

    ``endpoint`` is the plane-owned base URL.  It is read from the environment
    only (GR-6); it has **no code default**, so an environment without it
    produces an unconfigured, inert exporter rather than a request to a URL this
    repo invented.
    """

    endpoint: Optional[str] = None
    headers: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS

    @property
    def configured(self) -> bool:
        """True only when an endpoint was actually configured."""
        return bool(self.endpoint)

    @classmethod
    def from_env(
        cls, environ: Optional[Mapping[str, str]] = None
    ) -> "ExpositionConfig":
        """Build the config from the environment (or ``os.environ``)."""
        env = os.environ if environ is None else environ
        endpoint = str(env.get(ENV_ENDPOINT, "") or "").strip() or None
        return cls(
            endpoint=endpoint,
            headers=parse_headers(env.get(ENV_HEADERS, "")),
            timeout_seconds=_positive_float(
                env.get(ENV_TIMEOUT_SECONDS), DEFAULT_TIMEOUT_SECONDS
            ),
            interval_seconds=_positive_float(
                env.get(ENV_INTERVAL_SECONDS), DEFAULT_INTERVAL_SECONDS
            ),
        )

    def signal_url(self, signal: str) -> str:
        """The OTLP/HTTP URL for ``signal`` under this base endpoint.

        A base endpoint that already names the signal path is used as-is, so a
        plane that configures the full path is not silently given a doubled one.
        """
        if signal not in SIGNALS:
            raise ValueError(f"unknown OTLP signal: {signal!r}")
        base = str(self.endpoint or "").rstrip("/")
        suffix = f"/v1/{signal}"
        if base.endswith(suffix):
            return base
        return f"{base}{suffix}"


def _positive_float(raw: Any, default: float) -> float:
    """Parse a positive float, falling back to ``default`` when unusable."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0.0 or value != value:
        return default
    return value


def parse_headers(raw: str) -> tuple[tuple[str, str], ...]:
    """Parse ``k=v,k=v`` header configuration (order preserved, blank skipped).

    Header *values* are the only place a credential may appear; they come from
    the environment (GR-6) and are never written to a report, a log or a test
    fixture.  A malformed pair is skipped rather than guessed at.
    """
    pairs: list[tuple[str, str]] = []
    for chunk in str(raw or "").split(","):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        key, _, value = item.partition("=")
        key = key.strip()
        if not key:
            continue
        pairs.append((key, value.strip()))
    return tuple(pairs)


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
class PushTransport(Protocol):
    """The one thing this exporter needs from the outside world."""

    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout: float,
    ) -> int:
        """POST ``body`` and return the HTTP status code."""


class HttpPushTransport:
    """The real OTLP/HTTP push — stdlib only, no SDK, no agent, no collector.

    OTLP/HTTP is JSON-over-HTTP by spec; this posts the JSON encoding directly
    with ``urllib`` so the pillar keeps its "stdlib + PyYAML" dependency
    posture.  A transport error is raised, and :meth:`TelemetryExposition.export_once`
    reports it honestly instead of retrying in a storm.
    """

    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout: float,
    ) -> int:
        request = urllib.request.Request(  # noqa: S310 - the plane's own endpoint
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", **dict(headers)},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return int(getattr(response, "status", 0) or 0)
        except urllib.error.HTTPError as exc:
            return int(exc.code)


class RecordingTransport:
    """A transport that records the pushes it was handed (tests, dry runs).

    It makes no network call at all, so it is safe to use offline and is the
    transport the suite uses to prove inertness: while the exporter is inert the
    recording is **empty**, which is the assertion that matters.
    """

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout: float,
    ) -> int:
        self.calls.append(
            {
                "url": url,
                "body": body,
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        return self.status

    @property
    def signals(self) -> list[str]:
        """The OTLP signals pushed, in order."""
        out: list[str] = []
        for call in self.calls:
            for signal in SIGNALS:
                if call["url"].endswith(f"/v1/{signal}"):
                    out.append(signal)
        return out


# --------------------------------------------------------------------------- #
# Inputs — the facts the pillar already owns
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ExpositionInputs:
    """One render's inputs, all of them *consumed*, none of them re-derived.

    ``tenants`` is the declared tenant registry: a tenant id that is not in it
    is refused, never exported, so a label can never carry an identifier the
    platform has not declared.  ``slo_ids`` is the declared SLO template set
    (``telemetry/observability/slo_templates/**``); an SLO whose id is not
    declared is refused, which is what keeps ``slo_id`` bounded.
    """

    tenants: tuple[str, ...] = ()
    slo_ids: tuple[str, ...] = ()
    slo_results: tuple[Any, ...] = ()
    budget_state: Optional[Mapping[str, Any]] = None
    usage_state: Optional[Mapping[str, Any]] = None

    @classmethod
    def build(
        cls,
        *,
        tenants: Iterable[str] = (),
        slo_ids: Iterable[str] = (),
        slo_results: Iterable[Any] = (),
        budget_state: Optional[Mapping[str, Any]] = None,
        usage_state: Optional[Mapping[str, Any]] = None,
    ) -> "ExpositionInputs":
        """Normalize loose sequences into the frozen, sorted input shape."""
        return cls(
            tenants=tuple(sorted({str(t) for t in tenants if str(t)})),
            slo_ids=tuple(sorted({str(s) for s in slo_ids if str(s)})),
            slo_results=tuple(slo_results),
            budget_state=budget_state,
            usage_state=usage_state,
        )


def budget_state_of(exporter: Any) -> Optional[Mapping[str, Any]]:
    """Consume a ``BudgetStateExporter``'s snapshot document.

    The exporter is duck-typed on its documented ``snapshot()`` method rather
    than imported, so the observability pillar never depends on the budgets
    package: it consumes the *document* the other pillar owns.
    """
    snapshot = getattr(exporter, "snapshot", None)
    if not callable(snapshot):
        return None
    document = snapshot()
    return document if isinstance(document, Mapping) else None


def usage_state_of(reporter: Any) -> Optional[Mapping[str, Any]]:
    """Consume a metering ``UsageReporter``'s per-tenant aggregate.

    Keyed by tenant id, each value the aggregate's own ``to_dict()`` — the
    metering pillar's shape, never a shape this module invents.
    """
    by_tenant = getattr(reporter, "by_tenant", None)
    if not callable(by_tenant):
        return None
    aggregate = by_tenant() or {}
    if not isinstance(aggregate, Mapping):
        return None
    return {
        str(tenant): (row.to_dict() if hasattr(row, "to_dict") else row)
        for tenant, row in aggregate.items()
    }


def declared_tenants(
    *,
    budget_state: Optional[Mapping[str, Any]] = None,
    usage_state: Optional[Mapping[str, Any]] = None,
    extra: Iterable[str] = (),
) -> tuple[str, ...]:
    """The declared tenant registry: the union of the declared sources.

    A tenant exists because a declared policy, a declared usage aggregate or an
    explicitly declared identity record says so — never because a span happened
    to mention it.
    """
    ids = {str(t) for t in extra if str(t)}
    if isinstance(budget_state, Mapping):
        tenants = budget_state.get("tenants")
        if isinstance(tenants, Mapping):
            ids.update(str(t) for t in tenants)
    if isinstance(usage_state, Mapping):
        ids.update(str(t) for t in usage_state)
    return tuple(sorted(ids))


# --------------------------------------------------------------------------- #
# Rendering — bounded series, verbatim vocabulary
# --------------------------------------------------------------------------- #
def _num(value: Any) -> Optional[float]:
    """A finite number, or ``None`` when the fact is genuinely absent."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number else None
    return None


@dataclass(frozen=True)
class Gauge:
    """One exported measurement, read off a consumed SLO result."""

    name: str
    unit: str
    read: Callable[[Any], Optional[float]]


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


#: The gauges rendered per SLO identity.  Every one of them carries the *same*
#: five identity labels, so adding a measurement cannot widen the label set and
#: cannot multiply series beyond the declared tenant × template bound.
SLO_GAUGES: tuple[Gauge, ...] = (
    Gauge("ao.telemetry.slo.verdict", "1", lambda r: 1.0),
    Gauge("ao.telemetry.slo.window_data", "1",
          lambda r: 1.0 if _attr(r, "has_window_data") else 0.0),
    Gauge("ao.telemetry.slo.missed_window", "1",
          lambda r: 1.0 if _attr(r, "missed_window") else 0.0),
    Gauge("ao.telemetry.slo.attempts", "1", lambda r: _num(_attr(r, "attempts"))),
    Gauge("ao.telemetry.slo.good_count", "1",
          lambda r: _num(_attr(r, "good_count"))),
    Gauge("ao.telemetry.slo.bad_count", "1", lambda r: _num(_attr(r, "bad_count"))),
    Gauge("ao.telemetry.slo.violation_count", "1",
          lambda r: _num(_attr(r, "violation_count"))),
    Gauge("ao.telemetry.slo.measured_ratio", "1",
          lambda r: _num(_attr(r, "measured_ratio"))),
    Gauge("ao.telemetry.slo.measured_ms", "ms",
          lambda r: _num(_attr(r, "measured_ms"))),
    Gauge("ao.telemetry.slo.spent_usd", "USD",
          lambda r: _num(_attr(r, "spent_usd"))),
    Gauge("ao.telemetry.slo.budget_consumed_ratio", "1",
          lambda r: _num(_attr(r, "budget_consumed_ratio"))),
    Gauge("ao.telemetry.slo.target", "1",
          lambda r: _num(_attr(_attr(r, "definition"), "target"))),
)


@dataclass(frozen=True)
class Series:
    """One bounded metric series: a metric name, its labels, its value."""

    metric: str
    unit: str
    labels: tuple[tuple[str, str], ...]
    value: float

    @property
    def label_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _ in self.labels)

    @property
    def label_map(self) -> dict[str, str]:
        return dict(self.labels)


def assert_identity_labels(labels: Sequence[tuple[str, str]]) -> None:
    """Refuse a label set that is not exactly the ADR-0022 identity set.

    Both directions are defects: a *missing* identity dimension makes the series
    unattributable, and an *extra* one — especially a refused id-shaped
    dimension — is the cardinality incident the plane's rule exists to prevent.
    """
    keys = [key for key, _ in labels]
    if sorted(keys) != sorted(IDENTITY_LABELS):
        extra = sorted(set(keys) - set(IDENTITY_LABELS))
        missing = sorted(set(IDENTITY_LABELS) - set(keys))
        raise ValueError(
            "identity label set must be exactly "
            f"{list(IDENTITY_LABELS)}; extra={extra} missing={missing}"
        )
    refused = sorted(set(keys) & REFUSED_IDENTITY_LABELS)
    if refused:
        raise ValueError(f"refused identity label(s) present: {refused}")


@dataclass(frozen=True)
class Rendered:
    """The pure render result: two OTLP envelopes plus what was refused."""

    metrics: Mapping[str, Any]
    logs: Mapping[str, Any]
    series: tuple[Series, ...]
    refused_tenants: tuple[str, ...]
    refused_slo_ids: tuple[str, ...]

    @property
    def datapoints(self) -> int:
        return len(self.series)


@dataclass(frozen=True)
class RenderReport:
    """The honest outcome of one export attempt.

    ``delivered`` is about **delivery**, never about health: a delivery failure
    is reported here and is never turned into an SLO verdict, and a refusal to
    export (flag off, unconfigured) is reported as such rather than as a
    successful no-op.
    """

    rendered: bool
    delivered: bool
    reason: str
    signals: tuple[str, ...] = ()
    datapoints: int = 0
    refused_tenants: tuple[str, ...] = ()
    refused_slo_ids: tuple[str, ...] = ()
    status_code: Optional[int] = None
    detail: str = ""

    @property
    def inert(self) -> bool:
        """True when nothing was rendered and nothing was sent."""
        return not self.rendered and not self.delivered


class TelemetryExposition:
    """Renders the SLO / budget / metering facts and pushes them to the plane.

    Construction performs no I/O.  ``enabled`` may be supplied explicitly
    (tests, and the CLI after it has resolved the flag); otherwise it is read
    from the feature-flag registry, fail-closed.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        config: Optional[ExpositionConfig] = None,
        transport: Optional[PushTransport] = None,
        environ: Optional[Mapping[str, str]] = None,
        enabled: Optional[bool] = None,
        surface: str = EXPOSITION_SURFACE,
        registry_path: Optional[Path | str] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.environ = dict(os.environ) if environ is None else dict(environ)
        self.config = config if config is not None else ExpositionConfig.from_env(
            self.environ
        )
        self.transport: PushTransport = (
            transport if transport is not None else HttpPushTransport()
        )
        self.surface = surface
        self.registry_path = registry_path
        self._enabled = enabled
        self._clock = clock if clock is not None else time.time

    # ------------------------------------------------------------------ #
    @property
    def enabled(self) -> bool:
        """The GR-5 gate: OFF unless the registry explicitly promotes it."""
        if self._enabled is not None:
            return bool(self._enabled)
        return surface_enabled(
            self.repo_root,
            registry_path=self.registry_path,
            surface=self.surface,
        )

    # ------------------------------------------------------------------ #
    def slo_series(
        self, inputs: ExpositionInputs
    ) -> tuple[list[Series], list[str], list[str]]:
        """Render the SLO feed into bounded series.

        Returns ``(series, refused_tenants, refused_slo_ids)``.  A refusal is
        reported, never silently dropped: an undeclared tenant and an undeclared
        SLO id are both *absences from the export*, and the caller can see them.
        """
        declared_tenants_set = set(inputs.tenants)
        declared_ids = set(inputs.slo_ids)
        series: list[Series] = []
        refused_tenants: list[str] = []
        refused_slo_ids: list[str] = []

        for result in inputs.slo_results:
            tenant = _attr(result, "tenant_id")
            if not isinstance(tenant, str) or not tenant:
                refused_tenants.append(repr(tenant))
                continue
            if tenant not in declared_tenants_set:
                refused_tenants.append(tenant)
                continue

            kind = _attr(result, "kind")
            slo_id = self.declared_slo_id(result, tenant=tenant)
            if kind not in slos.SLO_KINDS or slo_id is None or slo_id not in declared_ids:
                refused_slo_ids.append(str(_attr(result, "name")))
                continue

            verdict = self.render_verdict(result)
            if verdict not in slos.VERDICTS:
                # A verdict outside the evaluator's closed set is a defect in the
                # producer, not something the boundary may paper over.
                raise ValueError(
                    f"SLO verdict {verdict!r} is not one of the declared "
                    f"verdicts {sorted(slos.VERDICTS)}"
                )

            labels = (
                ("service", PRODUCER_SERVICE),
                ("slo_kind", str(kind)),
                ("slo_id", slo_id),
                ("verdict", verdict),
                ("tenant", tenant),
            )
            assert_identity_labels(labels)
            for gauge in SLO_GAUGES:
                value = gauge.read(result)
                if value is None:
                    # An absent *measurement* is omitted; the verdict itself is
                    # always published above and is never omitted.
                    continue
                series.append(Series(gauge.name, gauge.unit, labels, float(value)))
        return series, refused_tenants, refused_slo_ids

    @staticmethod
    def declared_slo_id(result: Any, *, tenant: str) -> Optional[str]:
        """The declared template id behind an instantiated SLO name.

        ``SloTemplate.instantiate`` names a definition ``<template>:<tenant>``,
        so the bounded identity is the template half.  A name that is not in the
        declared template set survives this normalization and is then refused by
        the caller — which is what keeps ``slo_id`` from becoming unbounded.
        """
        name = _attr(result, "name")
        if not isinstance(name, str) or not name:
            return None
        suffix = f":{tenant}"
        if tenant and name.endswith(suffix):
            return name[: -len(suffix)]
        return name

    @staticmethod
    def render_verdict(result: Any) -> str:
        """The verdict **exactly as the recording pillar produced it** (D4.2).

        This is a render, not an opinion: no re-derivation from raw counters, no
        severity mapping, no numeric code, and above all no coercion of an
        unhealthy verdict into a healthy one.
        """
        return str(_attr(result, "verdict"))

    # ------------------------------------------------------------------ #
    def tenant_log_records(
        self, inputs: ExpositionInputs
    ) -> list[dict[str, Any]]:
        """Project the budget/quota/usage facts into tenant-scoped log records.

        One record per tenant per snapshot kind, built **only** from that
        tenant's own slice, so a per-tenant record cannot carry another tenant's
        identifiers (the ``portal/server/live_feed.py`` authz precedent).  Only
        bounded, closed-vocabulary attributes travel — never the audit feed's
        actor/subject detail and never a free-form error.
        """
        records: list[dict[str, Any]] = []
        budget_state = inputs.budget_state if isinstance(inputs.budget_state, Mapping) else {}
        tenants_state = budget_state.get("tenants")
        tenants_state = tenants_state if isinstance(tenants_state, Mapping) else {}
        usage_state = inputs.usage_state if isinstance(inputs.usage_state, Mapping) else {}

        for tenant in inputs.tenants:
            slice_ = tenants_state.get(tenant)
            slice_ = slice_ if isinstance(slice_, Mapping) else {}

            budget = slice_.get("budget")
            if isinstance(budget, Mapping):
                attributes: dict[str, Any] = {
                    "service": PRODUCER_SERVICE,
                    "tenant": tenant,
                    "snapshot_kind": SNAPSHOT_BUDGET,
                    "mode": str(budget.get("mode") or ""),
                }
                attributes.update(_budget_limit_attributes(budget.get("limits")))
                attributes.update(_vendor_cap_attributes(budget.get("vendorCaps")))
                records.append(
                    _log_record(
                        tenant=tenant,
                        snapshot_kind=SNAPSHOT_BUDGET,
                        attributes=attributes,
                    )
                )

            quotas = slice_.get("quotas")
            if isinstance(quotas, Mapping):
                resources = quotas.get("resources")
                resources = resources if isinstance(resources, Mapping) else {}
                for resource in sorted(resources):
                    limit = resources[resource]
                    if not isinstance(limit, Mapping):
                        continue
                    records.append(
                        _log_record(
                            tenant=tenant,
                            snapshot_kind=SNAPSHOT_QUOTA,
                            attributes={
                                "service": PRODUCER_SERVICE,
                                "tenant": tenant,
                                "snapshot_kind": SNAPSHOT_QUOTA,
                                "plan": str(quotas.get("plan") or ""),
                                "resource": str(resource),
                                "status": str(limit.get("status") or ""),
                                "window": str(limit.get("window") or ""),
                                "current": _num(limit.get("current")),
                                "soft_limit": _num(limit.get("softLimit")),
                                "hard_limit": _num(limit.get("hardLimit")),
                            },
                        )
                    )

            usage = usage_state.get(tenant)
            if isinstance(usage, Mapping):
                records.append(
                    _log_record(
                        tenant=tenant,
                        snapshot_kind=SNAPSHOT_USAGE,
                        attributes={
                            "service": PRODUCER_SERVICE,
                            "tenant": tenant,
                            "snapshot_kind": SNAPSHOT_USAGE,
                            "calls": _num(usage.get("calls")),
                            "cache_hits": _num(usage.get("cacheHits")),
                            "input_tokens": _num(usage.get("inputTokens")),
                            "output_tokens": _num(usage.get("outputTokens")),
                            "cost_usd": _num(usage.get("costUsd")),
                            "unmetered_calls": _num(usage.get("unmeteredCalls")),
                        },
                    )
                )

        audit = budget_state.get("audit")
        if isinstance(audit, Mapping) and audit.get("configured"):
            decisions = audit.get("byDecision")
            decisions = decisions if isinstance(decisions, Mapping) else {}
            records.append(
                _log_record(
                    tenant=None,
                    snapshot_kind=SNAPSHOT_BUDGET_AUDIT,
                    attributes={
                        "service": PRODUCER_SERVICE,
                        "snapshot_kind": SNAPSHOT_BUDGET_AUDIT,
                        # Counts only.  The audit feed's per-event actor/subject
                        # detail stays in the audit read model (#347): it is
                        # identity, and identity is refused at this boundary.
                        "total_events": _num(audit.get("totalEvents")),
                        "decision_counts": json.dumps(
                            {str(k): int(v) for k, v in sorted(decisions.items())},
                            sort_keys=True,
                        ),
                    },
                )
            )
        return records

    # ------------------------------------------------------------------ #
    def render(self, inputs: ExpositionInputs, *, now: Optional[float] = None) -> Rendered:
        """Render both OTLP envelopes.  Pure: no socket, no clock read, no file."""
        timestamp = self._clock() if now is None else now
        time_unix_nano = str(int(timestamp * 1_000_000_000))

        series, refused_tenants, refused_slo_ids = self.slo_series(inputs)
        records = self.tenant_log_records(inputs)
        return Rendered(
            metrics=otlp_metrics_envelope(series, time_unix_nano=time_unix_nano),
            logs=otlp_logs_envelope(records, time_unix_nano=time_unix_nano),
            series=tuple(series),
            refused_tenants=tuple(refused_tenants),
            refused_slo_ids=tuple(refused_slo_ids),
        )

    # ------------------------------------------------------------------ #
    def export_once(
        self, inputs: ExpositionInputs, *, now: Optional[float] = None
    ) -> RenderReport:
        """Render and push once — or do nothing, honestly, when inert.

        Inert means: the flag is off, or no endpoint is configured.  In both
        cases **nothing is rendered, nothing is pushed, and no healthy signal is
        invented** to fill the silence.  A failed push is reported as a delivery
        failure — never as an SLO verdict, and never retried here.
        """
        if not self.enabled:
            return RenderReport(
                rendered=False,
                delivered=False,
                reason=REASON_FLAG_OFF,
                detail=f"surface {self.surface!r} is not promoted",
            )
        if not self.config.configured:
            return RenderReport(
                rendered=False,
                delivered=False,
                reason=REASON_UNCONFIGURED,
                detail=f"{ENV_ENDPOINT} is not set",
            )

        rendered = self.render(inputs, now=now)
        delivered: list[str] = []
        for signal, envelope in (
            (SIGNAL_METRICS, rendered.metrics),
            (SIGNAL_LOGS, rendered.logs),
        ):
            body = encode_body(envelope)
            try:
                code = self.transport.post(
                    self.config.signal_url(signal),
                    body,
                    dict(self.config.headers),
                    float(self.config.timeout_seconds),
                )
            except Exception as exc:  # noqa: BLE001 - reported, never swallowed
                return self._report(
                    rendered,
                    delivered=delivered,
                    reason=REASON_TRANSPORT_ERROR,
                    detail=f"{signal}: {type(exc).__name__}",
                )
            if not 200 <= int(code) < 300:
                return self._report(
                    rendered,
                    delivered=delivered,
                    reason=REASON_REJECTED,
                    status_code=int(code),
                    detail=f"{signal}: HTTP {int(code)}",
                )
            delivered.append(signal)

        return self._report(rendered, delivered=delivered, reason=REASON_DELIVERED)

    @staticmethod
    def _report(
        rendered: Rendered,
        *,
        delivered: Sequence[str],
        reason: str,
        status_code: Optional[int] = None,
        detail: str = "",
    ) -> RenderReport:
        return RenderReport(
            rendered=True,
            delivered=len(delivered) == len(SIGNALS),
            reason=reason,
            signals=tuple(delivered),
            datapoints=rendered.datapoints,
            refused_tenants=rendered.refused_tenants,
            refused_slo_ids=rendered.refused_slo_ids,
            status_code=status_code,
            detail=detail,
        )

    # ------------------------------------------------------------------ #
    def run_periodically(
        self,
        inputs: Callable[[], ExpositionInputs],
        *,
        ticks: Optional[int] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> list[RenderReport]:
        """Export on the configured interval, re-rendering each tick.

        ``ticks`` bounds the run (``None`` means until stopped).  The loop never
        retries inside a tick: a failed push waits for the next interval, so an
        unreachable plane produces one honest failure per interval and not a
        retry storm.  When the exporter is inert the ticks still happen — that
        is how an idle window stays *visible* as ``NO_DATA`` rather than
        becoming indistinguishable from a dead producer.
        """
        waiter = sleep if sleep is not None else time.sleep
        reports: list[RenderReport] = []
        count = 0
        while ticks is None or count < ticks:
            if count:
                waiter(float(self.config.interval_seconds))
            reports.append(self.export_once(inputs()))
            count += 1
        return reports


# --------------------------------------------------------------------------- #
# Bounded projections
# --------------------------------------------------------------------------- #
def _budget_limit_attributes(limits: Any) -> dict[str, Any]:
    """Flatten the two declared budget limits onto fixed attribute names.

    The budget exporter's limit keys are its own closed pair, so the attribute
    names below stay a fixed, bounded set — this is a projection, not a place
    where a caller-supplied key becomes a label.
    """
    if not isinstance(limits, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, prefix, unit in (
        ("costUsd", "spend", "Usd"),
        ("tokens", "tokens", "Tokens"),
    ):
        limit = limits.get(key)
        if not isinstance(limit, Mapping):
            continue
        out[f"{prefix}_position"] = str(limit.get("position") or "")
        out[f"{prefix}_window"] = str(limit.get("window") or "")
        out[f"{prefix}_current{unit}"] = _num(limit.get(f"current{unit}"))
        out[f"{prefix}_limit{unit}"] = _num(limit.get(f"limit{unit}"))
    return out


def _vendor_cap_attributes(caps: Any) -> dict[str, Any]:
    """Count the declared vendor caps by position (never name a vendor)."""
    if not isinstance(caps, Sequence) or isinstance(caps, (str, bytes)):
        return {}
    positions = [
        str(cap.get("position") or "")
        for cap in caps
        if isinstance(cap, Mapping)
    ]
    return {
        "vendor_caps": float(len(positions)),
        "vendor_caps_warning": float(sum(1 for p in positions if p == "warning")),
        "vendor_caps_exceeded": float(sum(1 for p in positions if p == "exceeded")),
    }


def _log_record(
    *,
    tenant: Optional[str],
    snapshot_kind: str,
    attributes: Mapping[str, Any],
) -> dict[str, Any]:
    """One tenant-scoped, closed-attribute log record."""
    return {
        "tenant": tenant,
        "snapshot_kind": snapshot_kind,
        "body": (
            f"{snapshot_kind} snapshot"
            if tenant is None
            else f"{snapshot_kind} snapshot for {tenant}"
        ),
        "attributes": dict(attributes),
    }


# --------------------------------------------------------------------------- #
# OTLP/HTTP JSON encoding
# --------------------------------------------------------------------------- #
def string_value(value: str) -> dict[str, Any]:
    return {"stringValue": value}


def any_value(value: Any) -> dict[str, Any]:
    """Encode a bounded scalar into an OTLP ``AnyValue``."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, (int, float)):
        return {"doubleValue": float(value)}
    if value is None:
        return {"stringValue": ""}
    return {"stringValue": str(value)}


def _resource() -> dict[str, Any]:
    return {
        "attributes": [
            {"key": "service.name", "value": string_value(PRODUCER_SERVICE)},
        ]
    }


def _scope() -> dict[str, Any]:
    return {
        "name": SCOPE_NAME,
        "attributes": [
            {"key": "service", "value": string_value(PRODUCER_SERVICE)},
            {"key": "direction", "value": string_value("push")},
        ],
    }


def otlp_metrics_envelope(
    series: Sequence[Series], *, time_unix_nano: str
) -> dict[str, Any]:
    """The OTLP/HTTP ``resourceMetrics`` envelope for the SLO gauge set."""
    grouped: dict[str, list[Series]] = {}
    order: list[tuple[str, str]] = []
    for item in series:
        if item.metric not in grouped:
            grouped[item.metric] = []
            order.append((item.metric, item.unit))
        grouped[item.metric].append(item)

    metrics: list[dict[str, Any]] = []
    for name, unit in order:
        points = []
        for item in grouped[name]:
            assert_identity_labels(item.labels)
            points.append(
                {
                    "attributes": [
                        {"key": key, "value": string_value(value)}
                        for key, value in item.labels
                    ],
                    "asDouble": float(item.value),
                    "timeUnixNano": time_unix_nano,
                }
            )
        metrics.append(
            {
                "name": name,
                "unit": unit,
                "gauge": {"dataPoints": points},
            }
        )

    return {
        "resourceMetrics": [
            {
                "resource": _resource(),
                "scopeMetrics": [{"scope": _scope(), "metrics": metrics}],
            }
        ]
    }


def otlp_logs_envelope(
    records: Sequence[Mapping[str, Any]], *, time_unix_nano: str
) -> dict[str, Any]:
    """The OTLP/HTTP ``resourceLogs`` envelope for the snapshot projections."""
    log_records: list[dict[str, Any]] = []
    for record in records:
        attributes = record.get("attributes") or {}
        log_records.append(
            {
                "timeUnixNano": time_unix_nano,
                "body": string_value(str(record.get("body") or "")),
                # A genuinely absent fact is omitted, never encoded as a zero or
                # an empty string that a reader could mistake for a value.
                "attributes": [
                    {"key": key, "value": any_value(value)}
                    for key, value in attributes.items()
                    if value is not None
                ],
            }
        )
    return {
        "resourceLogs": [
            {
                "resource": _resource(),
                "scopeLogs": [{"scope": _scope(), "logRecords": log_records}],
            }
        ]
    }


def encode_body(envelope: Mapping[str, Any]) -> bytes:
    """Compact, deterministic JSON bytes for the wire."""
    return json.dumps(
        envelope, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


# --------------------------------------------------------------------------- #
# CLI entry point (`python3 -m telemetry.observability.exposition`)
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    """Render the export locally, or run the push loop.

    ``--dry-run`` prints the rendered payload to stdout and makes no network
    call at all, so an operator can inspect exactly what would leave the
    process.  Without it the loop pushes only when the flag is on *and* an
    endpoint is configured; otherwise it reports inertness and exits 0.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="telemetry.observability.exposition")
    parser.add_argument(
        "--root", default=os.getcwd(), help="repository root (flag registry base)"
    )
    parser.add_argument("--tenants", default="", help="declared tenant ids, comma-separated")
    parser.add_argument("--slo-ids", dest="slo_ids", default="",
                        help="declared SLO template ids, comma-separated")
    parser.add_argument("--dry-run", action="store_true",
                        help="render and print the payload; push nothing")
    parser.add_argument("--ticks", type=int, default=None,
                        help="number of export ticks (default: run until stopped)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    inputs = ExpositionInputs.build(
        tenants=[t for t in args.tenants.split(",") if t.strip()],
        slo_ids=[s for s in args.slo_ids.split(",") if s.strip()],
    )
    exporter = TelemetryExposition(repo_root=args.root)

    if args.dry_run:
        rendered = exporter.render(inputs)
        sys.stdout.write(
            json.dumps(
                {"metrics": rendered.metrics, "logs": rendered.logs},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return 0

    reports = exporter.run_periodically(lambda: inputs, ticks=args.ticks)
    for report in reports:
        sys.stdout.write(
            f"{report.reason}\t delivered={report.delivered}"
            f" rendered={report.rendered} datapoints={report.datapoints}\n"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "PRODUCER_SERVICE",
    "IDENTITY_LABELS",
    "REFUSED_IDENTITY_LABELS",
    "SIGNALS",
    "SIGNAL_METRICS",
    "SIGNAL_LOGS",
    "SCOPE_NAME",
    "ENV_ENDPOINT",
    "ENV_HEADERS",
    "ENV_TIMEOUT_SECONDS",
    "ENV_INTERVAL_SECONDS",
    "EXPOSITION_SURFACE",
    "REASON_FLAG_OFF",
    "REASON_UNCONFIGURED",
    "REASON_DELIVERED",
    "REASON_TRANSPORT_ERROR",
    "REASON_REJECTED",
    "SLO_GAUGES",
    "Gauge",
    "Series",
    "Rendered",
    "RenderReport",
    "ExpositionConfig",
    "ExpositionInputs",
    "PushTransport",
    "HttpPushTransport",
    "RecordingTransport",
    "TelemetryExposition",
    "assert_identity_labels",
    "budget_state_of",
    "declared_tenants",
    "encode_body",
    "otlp_logs_envelope",
    "otlp_metrics_envelope",
    "parse_headers",
    "read_surface_default",
    "surface_enabled",
    "usage_state_of",
]
