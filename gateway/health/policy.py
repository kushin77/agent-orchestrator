"""Degradation policy: the health state machine (issue #18).

The state machine lives here as pure decision helpers over the rolling-window
measurements so it is unit-testable without a monitor. ``monitor.ModelHealth``
holds the actual state (one per provider+model) and drives these helpers on
every recorded outcome.

States (``HealthState``):

    healthy  --trip threshold exceeded (>= min_samples)--> quarantined
    quarantined --cool-off elapsed (clock)--> probing
    probing  --probe_success_threshold consecutive probe successes--> healthy
    probing  --a probe failure--> quarantined  (cool-off resets)

``quarantined`` is a hard stop: production traffic is diverted to the
fallback chain and **only a recovery probe may change state** — recorded
production successes/failures update the window but never clear a quarantine
(no-false-green; recovery must be earned by probes, never by an absent log or
a stray success).

Alongside the hard state, a per-model **degraded** verdict is sticky with
hysteresis (cannibalized from the leaderboard ``model-health.sh`` degradation
policy): the window failure rate crossing ``degrade_failure_pct`` marks the
model degraded (the gateway routes away, ``is_healthy`` False) and it returns
to healthy only once the rate falls to/below ``recover_failure_pct`` (which
is strictly below the degrade threshold, so a rate oscillating around the
threshold does not flap).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from health.config import HealthConfig

# Verdict vocabulary surfaced by ``health_status()``.
VERDICT_HEALTHY = "healthy"
VERDICT_DEGRADED = "degraded"
VERDICT_UNHEALTHY = "unhealthy"
VERDICT_UNKNOWN = "unknown"


class HealthState(str, Enum):
    """Machine state of one provider+model."""

    HEALTHY = "healthy"
    QUARANTINED = "quarantined"
    PROBING = "probing"


def has_decision_samples(samples: int, config: HealthConfig) -> bool:
    """Whether the window has enough outcomes for a degradation decision.

    No data is never "healthy": below ``min_samples`` the verdict is
    ``unknown`` and the model is *admitted* (treated as healthy for routing)
    but the monitor records no transition. Absence of data can never clear a
    quarantine (that requires probes).
    """
    return samples >= config.min_samples


def failure_rate_pct(failures: int, total: int) -> float:
    """Percent failure rate over the window (0.0 when the window is empty)."""
    if total <= 0:
        return 0.0
    return (failures / total) * 100.0


def should_quarantine(failure_pct: float, samples: int, config: HealthConfig) -> bool:
    """Whether the window warrants a hard stop (trip threshold exceeded)."""
    if not has_decision_samples(samples, config):
        return False
    return failure_pct > config.trip_failure_pct


def should_degrade(failure_pct: float, samples: int, config: HealthConfig) -> bool:
    """Whether the window warrants marking the model degraded."""
    if not has_decision_samples(samples, config):
        return False
    return failure_pct > config.degrade_failure_pct


def should_clear_degraded(failure_pct: float, config: HealthConfig) -> bool:
    """Whether a degraded model has earned recovery (window rate <= recover)."""
    return failure_pct <= config.recover_failure_pct


def cool_off_elapsed(quarantined_at: float | None, now: float, config: HealthConfig) -> bool:
    """Whether a quarantined model may now be probed (cool-off elapsed)."""
    if quarantined_at is None:
        return False
    return (now - quarantined_at) >= config.cool_off_seconds


def verdict_for(
    state: HealthState,
    *,
    degraded: bool,
    failure_pct: float,
    samples: int,
    config: HealthConfig,
) -> str:
    """The reported verdict for one model.

    - quarantined/probing state -> ``unhealthy`` (not serving production)
    - sticky degraded flag      -> ``degraded`` (routing avoids the model)
    - healthy with < min samples-> ``unknown`` (admitted, not yet measured)
    - otherwise                 -> ``healthy``
    """
    if state in (HealthState.QUARANTINED, HealthState.PROBING):
        return VERDICT_UNHEALTHY
    if degraded:
        return VERDICT_DEGRADED
    if not has_decision_samples(samples, config):
        return VERDICT_UNKNOWN
    # A healthy-state window may still exceed degrade while the flag has not
    # been evaluated yet; report the truth rather than a stale flag.
    if failure_pct > config.degrade_failure_pct:
        return VERDICT_DEGRADED
    return VERDICT_HEALTHY


def to_dict_verbose() -> dict[str, Any]:
    """Machine-readable vocabulary for docs/CLI (no logic)."""
    return {
        "states": [s.value for s in HealthState],
        "verdicts": [
            VERDICT_HEALTHY,
            VERDICT_DEGRADED,
            VERDICT_UNHEALTHY,
            VERDICT_UNKNOWN,
        ],
    }
