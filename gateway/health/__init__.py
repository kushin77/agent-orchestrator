"""gateway/health — model-health monitoring + auto-degradation + local fallback.

The health signal for the Model Gateways pillar (issue #18, phase 2). The
gateway proxy (issue #16) and the FinOps chooser (issue #17) consume this
package: it tracks per-provider/model rolling failure rates and latency,
auto-degrades unhealthy models (trip -> quarantine -> cool-off -> recovery
probe -> healthy), routes away along a fallback chain whose final rung is the
local Ollama provider, and emits health/degradation events to audit and alert
sinks (Phase-5 hook).

Public surface
--------------

- ``HealthConfig`` (health.config) + ``load_config()`` — YAML thresholds.
- ``HealthMonitor`` (health.monitor) — the composition root and contract:
  ``record_success`` / ``record_failure``, ``is_healthy(provider, model)``,
  ``health_status(provider, model)``, ``statuses()``, ``may_probe`` /
  ``record_probe_success`` / ``record_probe_failure``, ``mark_healthy`` /
  ``mark_unhealthy``.
- ``ChainRegistry`` (health.fallback) — the cloud -> local fallback chains;
  Ollama is the last resort and is marked healthy/unhealthy like any provider.
- ``HealthEvent`` + sinks (health.events) — audit (all events) and alert
  (severity >= warning) sinks for the Phase-5 observability lane.

The package is importable as ``health`` when ``gateway/`` is on ``sys.path``
(the tests arrange this in ``tests/conftest.py``; consumers of the merged
contract should do the same or run the CLI directly).
"""

from health.config import (
    ERR_AUTH,
    ERR_HTTP_5XX,
    ERR_OUTPUT_INVALID,
    ERR_RATE_LIMIT,
    ERR_TIMEOUT,
    ERR_UNAVAILABLE,
    ERR_UNKNOWN,
    ERROR_CLASSES,
    TRANSIENT_ERROR_CLASSES,
    HealthConfig,
    classify_error,
    default_config,
    load_config,
)
from health.events import (
    HealthEvent,
    HealthSink,
    JsonlHealthSink,
    ListHealthSink,
    NoopHealthSink,
    SEV_CRITICAL,
    SEV_INFO,
    SEV_WARNING,
)
from health.fallback import (
    DEFAULT_LOCAL_MODEL,
    DEFAULT_LOCAL_RUNG,
    LOCAL_PROVIDER,
    ChainRegistry,
    FallbackChain,
    FallbackRung,
    load_default_chains,
)
from health.monitor import HealthClock, HealthMonitor, ModelHealth, WindowStats

__all__ = [
    # config
    "HealthConfig",
    "default_config",
    "load_config",
    "classify_error",
    "ERROR_CLASSES",
    "TRANSIENT_ERROR_CLASSES",
    "ERR_TIMEOUT",
    "ERR_RATE_LIMIT",
    "ERR_HTTP_5XX",
    "ERR_AUTH",
    "ERR_OUTPUT_INVALID",
    "ERR_UNAVAILABLE",
    "ERR_UNKNOWN",
    # events
    "HealthEvent",
    "HealthSink",
    "ListHealthSink",
    "JsonlHealthSink",
    "NoopHealthSink",
    "SEV_INFO",
    "SEV_WARNING",
    "SEV_CRITICAL",
    # monitor
    "HealthMonitor",
    "ModelHealth",
    "HealthClock",
    "WindowStats",
    # fallback
    "ChainRegistry",
    "FallbackChain",
    "FallbackRung",
    "load_default_chains",
    "LOCAL_PROVIDER",
    "DEFAULT_LOCAL_MODEL",
    "DEFAULT_LOCAL_RUNG",
]
