"""telemetry/observability — observability pillar (issue #32, phase 5).

Full-trace telemetry for agent + model calls: span/trace recording with
correlation ids end-to-end (gateway -> guardrails -> engine), an offline
JSONL store, per-tenant SLOs (availability / latency / cost) from
parameterized templates with honest evaluation and breach detection
(outcome-not-liveness), usage/chargeback reports, and offline static
dashboards.

Importable from the repo root as ``telemetry.observability`` (PEP-420
namespace; ``telemetry/`` carries no ``__init__.py``).  The module is fully
offline — stdlib + PyYAML only, no network, no external servers.

Public surface
--------------

- ``model`` — ``SpanRecord`` / ``Trace``, outcome + service + span-kind
  vocabulary (consumed from the gateway contract), latency percentile helper.
- ``intake`` — the emit contract other pillars call: ``TelemetrySink``,
  ``MemorySink``/``NoopSink``, and ``TelemetryRecorder`` (``trace()`` /
  ``span()`` / ``emit()`` / ``ingest_gateway_call()``) with contextvar
  correlation propagation.
- ``store`` — ``JsonlSpanSink`` (offline writer) + ``TraceStore`` (reader /
  query / trace rebuild).
- ``slos`` — ``SloDefinition`` / ``SloTemplate`` / ``SloEvaluator`` +
  ``slo_templates/*.yaml``.
- ``breach`` — ``BreachDetector`` / ``Alert`` (outcome-not-liveness).
- ``usage`` — per-tenant usage/chargeback aggregation (billing feed).
- ``dashboard`` — static HTML + JSON data + terminal health report.
- ``cli`` — ``python3 -m telemetry.observability.cli <cmd>``.
"""

from telemetry.observability import (  # noqa: F401
    breach,
    dashboard,
    intake,
    model,
    slos,
    store,
    usage,
)

__all__ = [
    "model",
    "intake",
    "store",
    "slos",
    "breach",
    "usage",
    "dashboard",
]
