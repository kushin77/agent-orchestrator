"""Health-monitoring configuration (issue #18).

The health monitor is tuned by one immutable ``HealthConfig``:

- **Rolling window** — ``window_size`` recent outcomes are kept per
  (provider, model); the failure rate and latency percentiles are computed
  over that window (fixed-size sliding window, no time aging — deterministic
  and offline-testable).
- **Degradation thresholds** (percent failure rates over the window) —
  cannibalized from the leaderboard ``model-health.sh`` tier-degradation
  policy (degrade > 20%, auto-recover <= 10%) and its circuit-breaker stop
  threshold (> 50%):
  - ``degrade_failure_pct`` — above this the model is reported ``degraded``
    (the gateway routes away; ``is_healthy`` is False).
  - ``trip_failure_pct`` — above this the model state machine trips to
    ``quarantined`` (a hard stop; only a recovery probe may restore it).
  - ``recover_failure_pct`` — a *degraded* (not quarantined) model returns to
    healthy only once its window failure rate falls to/below this level
    (hysteresis: the degrade and recover thresholds differ, so a rate that
    oscillates around the degrade threshold does not flap).
- **Decision floor** — ``min_samples``: no degradation/quarantine decision is
  made until the window holds at least this many outcomes. A window with no
  data is *unknown*, which the monitor treats as healthy for routing purposes
  (safe admission on boot) — but absence of data can never clear an active
  quarantine (no-false-green).
- **Recovery probe** — ``cool_off_seconds`` is how long a quarantined model
  must wait before a recovery probe is permitted; ``probe_success_threshold``
  is how many consecutive probe successes restore it to healthy (mirrors the
  ollama/providers circuit-breaker HALF_OPEN success threshold).
- **Latency advisory** — ``slow_threshold_ms`` labels a model ``slow`` when
  its window p95 latency exceeds it. Latency is measured and reported but the
  state machine is failure-driven (the issue's HA doctrine routes on
  > threshold *failures*).

Error classes form a closed documented vocabulary
``timeout | rate_limit | http_5xx | auth | output_invalid | unavailable |
unknown``. ``classify_error`` maps an exception onto that vocabulary by type
name (duck-typed, so the gateway can pass providers-layer exceptions without
the health lane importing them).

Configuration loads from ``health.yaml`` (shipped defaults) via
``load_config()``; the YAML keys mirror the dataclass field names in
lowerCamelCase (``windowSize``, ``minSamples``, ...) to match the fleet
configuration convention.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

try:  # PyYAML is a declared repo dependency; keep the import local.
    import yaml
except ImportError:  # pragma: no cover - exercised only on broken installs
    yaml = None  # type: ignore[assignment]

# --------------------------------------------------------------------------- #
# Error-class vocabulary (closed, documented)
# --------------------------------------------------------------------------- #
ERR_TIMEOUT = "timeout"
ERR_RATE_LIMIT = "rate_limit"
ERR_HTTP_5XX = "http_5xx"
ERR_AUTH = "auth"
ERR_OUTPUT_INVALID = "output_invalid"
ERR_UNAVAILABLE = "unavailable"
ERR_UNKNOWN = "unknown"

ERROR_CLASSES: frozenset[str] = frozenset(
    {
        ERR_TIMEOUT,
        ERR_RATE_LIMIT,
        ERR_HTTP_5XX,
        ERR_AUTH,
        ERR_OUTPUT_INVALID,
        ERR_UNAVAILABLE,
        ERR_UNKNOWN,
    }
)

#: Transient classes: the gateway retries these and they count toward a
#: provider being unavailable. Non-transient failures (auth, output_invalid)
#: are still failures of the call and count in the window, but they are not
#: evidence of provider availability trouble.
TRANSIENT_ERROR_CLASSES: frozenset[str] = frozenset(
    {ERR_TIMEOUT, ERR_RATE_LIMIT, ERR_HTTP_5XX, ERR_UNAVAILABLE}
)

# Substrings (uppercased) that classify an exception by type name.
_ERROR_NAME_RULES: tuple[tuple[str, str], ...] = (
    ("TIMEOUT", ERR_TIMEOUT),
    ("RATELIMIT", ERR_RATE_LIMIT),
    ("RATE_LIMIT", ERR_RATE_LIMIT),
    ("TOOMANY", ERR_RATE_LIMIT),
    ("429", ERR_RATE_LIMIT),
    ("AUTH", ERR_AUTH),
    ("AUTHENTICATION", ERR_AUTH),
    ("CREDENTIAL", ERR_AUTH),
    ("APIKEY", ERR_AUTH),
    ("OUTPUTVALIDATION", ERR_OUTPUT_INVALID),
    ("SCHEMA", ERR_OUTPUT_INVALID),
    ("CIRCUITOPEN", ERR_UNAVAILABLE),
    ("RETRYEXHAUSTED", ERR_UNAVAILABLE),
    ("UNAVAILABLE", ERR_UNAVAILABLE),
    ("CONNECTION", ERR_UNAVAILABLE),
    ("RESET", ERR_UNAVAILABLE),
    ("5XX", ERR_HTTP_5XX),
    ("SERVERERROR", ERR_HTTP_5XX),
    ("INTERNAL", ERR_HTTP_5XX),
)


def classify_error(exc: BaseException) -> str:
    """Map an exception to a health error class by type name.

    Duck-typed: works with the providers-layer exception taxonomy
    (``ProviderTimeoutError`` -> ``timeout``, ``CircuitOpenError`` /
    ``RetryExhaustedError`` -> ``unavailable``, ``OutputValidationError`` ->
    ``output_invalid``) and with any provider-native exception whose type name
    carries the marker. Unknown exceptions classify as ``unknown`` (counted,
    never crashed on).
    """
    name = type(exc).__name__.upper()
    for marker, cls in _ERROR_NAME_RULES:
        if marker in name:
            return cls
    return ERR_UNKNOWN


@dataclass(frozen=True)
class HealthConfig:
    """Immutable tuning for the health monitor (defaults ship in health.yaml)."""

    window_size: int = 100
    min_samples: int = 10
    degrade_failure_pct: float = 20.0
    trip_failure_pct: float = 50.0
    recover_failure_pct: float = 10.0
    cool_off_seconds: float = 60.0
    probe_success_threshold: int = 2
    slow_threshold_ms: float = 30000.0

    def __post_init__(self) -> None:
        if self.window_size < 1:
            raise ValueError(f"window_size must be >= 1, got {self.window_size}")
        if self.min_samples < 1:
            raise ValueError(f"min_samples must be >= 1, got {self.min_samples}")
        if self.min_samples > self.window_size:
            raise ValueError(
                f"min_samples ({self.min_samples}) cannot exceed "
                f"window_size ({self.window_size})"
            )
        if not (0.0 <= self.recover_failure_pct <= self.degrade_failure_pct
                <= self.trip_failure_pct <= 100.0):
            raise ValueError(
                "thresholds must satisfy 0 <= recover <= degrade <= trip <= 100; "
                f"got recover={self.recover_failure_pct} degrade="
                f"{self.degrade_failure_pct} trip={self.trip_failure_pct}"
            )
        if self.cool_off_seconds < 0.0:
            raise ValueError(f"cool_off_seconds must be >= 0, got {self.cool_off_seconds}")
        if self.probe_success_threshold < 1:
            raise ValueError(
                f"probe_success_threshold must be >= 1, got {self.probe_success_threshold}"
            )
        if self.slow_threshold_ms < 0.0:
            raise ValueError(f"slow_threshold_ms must be >= 0, got {self.slow_threshold_ms}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "windowSize": self.window_size,
            "minSamples": self.min_samples,
            "degradeFailurePct": self.degrade_failure_pct,
            "tripFailurePct": self.trip_failure_pct,
            "recoverFailurePct": self.recover_failure_pct,
            "coolOffSeconds": self.cool_off_seconds,
            "probeSuccessThreshold": self.probe_success_threshold,
            "slowThresholdMs": self.slow_threshold_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HealthConfig":
        known = {
            "windowSize": "window_size",
            "minSamples": "min_samples",
            "degradeFailurePct": "degrade_failure_pct",
            "tripFailurePct": "trip_failure_pct",
            "recoverFailurePct": "recover_failure_pct",
            "coolOffSeconds": "cool_off_seconds",
            "probeSuccessThreshold": "probe_success_threshold",
            "slowThresholdMs": "slow_threshold_ms",
        }
        kwargs: dict[str, Any] = {}
        for yaml_key, field_name in known.items():
            if yaml_key in data:
                kwargs[field_name] = data[yaml_key]
        return cls(**kwargs)


def _default_config_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "health.yaml")


def default_config() -> HealthConfig:
    """The shipped defaults (mirrors the values in ``health.yaml``)."""
    return HealthConfig()


def load_config(path: str | None = None) -> HealthConfig:
    """Load a ``HealthConfig`` from YAML (default: the shipped ``health.yaml``).

    Raises ``ValueError`` for an unreadable/malformed document or an invalid
    threshold combination (fail closed — a broken config must never silently
    fall back to defaults).
    """
    if yaml is None:  # pragma: no cover - broken install only
        raise RuntimeError("PyYAML is required to load health configuration")
    path = path or _default_config_path()
    with open(path, "r", encoding="utf-8") as fh:
        document = yaml.safe_load(fh) or {}
    monitor = document.get("monitor") or {}
    if not isinstance(monitor, dict):
        raise ValueError(f"{path}: 'monitor' must be a mapping")
    config = HealthConfig.from_dict(monitor)
    return config
