"""Model-health monitor: rolling failure rate, latency, degradation (issue #18).

This is the health signal the gateway proxy (issue #16) and the FinOps
chooser (issue #17) consume. One ``ModelHealth`` entry exists per
(provider, model); the ``HealthMonitor`` facade is the composition root:

- ``record_success`` / ``record_failure`` — the gateway reports every
  provider outcome (latency + error class) and the monitor updates a
  fixed-size rolling window.
- ``is_healthy(provider, model)`` — the boolean routing signal (a degraded or
  quarantined/probing model is NOT healthy, so the caller routes to the
  fallback chain).
- ``health_status(provider, model)`` — the queryable report: state, verdict,
  rolling failure rate, window latency avg/p95, per-error-class counts.
- ``may_probe`` / ``record_probe_success`` / ``record_probe_failure`` — the
  recovery-probe lifecycle for a quarantined model.
- ``mark_healthy`` / ``mark_unhealthy`` — operator overrides (e.g. an
  out-of-band check finds the local Ollama daemon down).

Every state/verdict transition emits a ``HealthEvent`` to the injected audit
and alert sinks (see ``events.py``) — the Phase-5 hook.

No-false-green guarantees (each is negative-tested):

- feeding failures past the trip threshold actually quarantines the model;
- production successes recorded while a model is quarantined can NEVER clear
  the quarantine (only recovery probes can) — a stray success or an absent
  log is not recovery;
- a probe against a dead model (probe failure) keeps it quarantined.
"""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Callable

from health.config import (
    ERR_UNKNOWN,
    HealthConfig,
    classify_error,
    default_config,
)
from health.events import (
    KIND_DEGRADED,
    KIND_HEALTHY,
    KIND_OPERATOR_MARKED_HEALTHY,
    KIND_OPERATOR_MARKED_UNHEALTHY,
    KIND_QUARANTINED,
    KIND_QUARANTINE_REASSERTED,
    KIND_RECOVERY_PROBE_STARTED,
    KIND_RECOVERED,
    HealthEvent,
    HealthSink,
    NoopHealthSink,
    SEV_CRITICAL,
    SEV_INFO,
    SEV_WARNING,
    SEVERITY_RANK,
)
from health.policy import (
    VERDICT_HEALTHY,
    VERDICT_UNKNOWN,
    HealthState,
    cool_off_elapsed,
    failure_rate_pct,
    has_decision_samples,
    should_clear_degraded,
    should_degrade,
    should_quarantine,
    verdict_for,
)

HealthClock = Callable[[], float]


def _p95(sorted_latencies: list[float]) -> float | None:
    """Nearest-rank 95th percentile of an ascending latency list."""
    n = len(sorted_latencies)
    if n == 0:
        return None
    rank = max(1, int((0.95 * n) + 0.999999))  # ceil(0.95*n), 1-based
    return sorted_latencies[rank - 1]


@dataclass(frozen=True)
class Outcome:
    """One recorded provider outcome in a model's rolling window."""

    ok: bool
    latency_ms: float | None = None
    error_class: str | None = None
    ts: float = 0.0


@dataclass(frozen=True)
class WindowStats:
    """Computed statistics over one model's rolling window."""

    total: int = 0
    successes: int = 0
    failures: int = 0
    failure_rate_pct: float = 0.0
    latency_avg_ms: float | None = None
    latency_p95_ms: float | None = None
    slow: bool = False
    error_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "successes": self.successes,
            "failures": self.failures,
            "failure_rate_pct": round(self.failure_rate_pct, 2),
            "latency_avg_ms": (
                round(self.latency_avg_ms, 2) if self.latency_avg_ms is not None else None
            ),
            "latency_p95_ms": (
                round(self.latency_p95_ms, 2) if self.latency_p95_ms is not None else None
            ),
            "slow": self.slow,
            "error_counts": dict(sorted(self.error_counts.items())),
        }


class ModelHealth:
    """Rolling-window health + degradation state for one (provider, model)."""

    def __init__(
        self,
        provider: str,
        model: str,
        config: HealthConfig,
        *,
        now: HealthClock,
        emit: Callable[[HealthEvent], None],
    ) -> None:
        self.provider = provider
        self.model = model
        self._config = config
        self._now = now
        self._emit = emit
        self._window: deque[Outcome] = deque(maxlen=config.window_size)
        self.state = HealthState.HEALTHY
        self.degraded = False
        self.quarantined_at: float | None = None
        self.probe_successes = 0
        self.last_error_class: str | None = None
        self._last_verdict: str = VERDICT_UNKNOWN

    # ------------------------------------------------------------------ #
    # Measurement
    # ------------------------------------------------------------------ #
    def _record(self, ok: bool, latency_ms: float | None, error_class: str | None) -> None:
        self._window.append(Outcome(ok=ok, latency_ms=latency_ms,
                                    error_class=error_class, ts=self._now()))
        if not ok and error_class:
            self.last_error_class = error_class

    def stats(self) -> WindowStats:
        """Compute rolling-window statistics (failure rate + latency + errors)."""
        window = self._window
        total = len(window)
        failures = sum(1 for o in window if not o.ok)
        successes = total - failures
        latencies = sorted(o.latency_ms for o in window if o.latency_ms is not None)
        p95 = _p95(latencies)
        avg = (sum(latencies) / len(latencies)) if latencies else None
        error_counts: Counter[str] = Counter(
            o.error_class or ERR_UNKNOWN for o in window if not o.ok
        )
        slow = bool(
            p95 is not None
            and has_decision_samples(total, self._config)
            and p95 > self._config.slow_threshold_ms
        )
        return WindowStats(
            total=total,
            successes=successes,
            failures=failures,
            failure_rate_pct=failure_rate_pct(failures, total),
            latency_avg_ms=avg,
            latency_p95_ms=p95,
            slow=slow,
            error_counts=dict(error_counts),
        )

    # ------------------------------------------------------------------ #
    # Verdict / health query
    # ------------------------------------------------------------------ #
    def failure_pct(self) -> float:
        st = self.stats()
        return st.failure_rate_pct

    def verdict(self) -> str:
        st = self.stats()
        return verdict_for(
            self.state,
            degraded=self.degraded,
            failure_pct=st.failure_rate_pct,
            samples=st.total,
            config=self._config,
        )

    def is_healthy(self) -> bool:
        """Routing signal: healthy/unknown models may serve; others route away."""
        return self.verdict() in (VERDICT_HEALTHY, VERDICT_UNKNOWN)

    def status(self) -> dict[str, Any]:
        """Queryable health report (the ``health_status()`` contract)."""
        st = self.stats()
        verdict = self.verdict()
        now = self._now()
        return {
            "provider": self.provider,
            "model": self.model,
            "state": self.state.value,
            "verdict": verdict,
            "is_healthy": verdict in (VERDICT_HEALTHY, VERDICT_UNKNOWN),
            "window": st.to_dict(),
            "degraded": self.degraded,
            "quarantined_since": self.quarantined_at,
            "probe": {
                "authorized": self.state is HealthState.PROBING,
                "successes": self.probe_successes,
                "success_threshold": self._config.probe_success_threshold,
                "cool_off_seconds": self._config.cool_off_seconds,
                "cool_off_elapsed": cool_off_elapsed(
                    self.quarantined_at, now, self._config
                ),
            },
            "last_error_class": self.last_error_class,
            "ts": now,
        }

    # ------------------------------------------------------------------ #
    # Production outcome recording (state machine driver in HEALTHY only)
    # ------------------------------------------------------------------ #
    def record_success(self, latency_ms: float | None = None) -> None:
        self._record(True, latency_ms, None)
        if self.state is HealthState.HEALTHY:
            self._evaluate_healthy_state(None)

    def record_failure(
        self,
        latency_ms: float | None = None,
        error_class: str | None = None,
    ) -> None:
        cls = error_class or ERR_UNKNOWN
        self._record(False, latency_ms, cls)
        if self.state is HealthState.HEALTHY:
            self._evaluate_healthy_state(cls)

    def _evaluate_healthy_state(self, last_error_class: str | None) -> None:
        """Trip to quarantine or flip the sticky degraded verdict."""
        st = self.stats()
        if should_quarantine(st.failure_rate_pct, st.total, self._config):
            self._enter_quarantine(reason="trip_threshold_exceeded",
                                   error_class=last_error_class)
            return
        prev_degraded = self.degraded
        if not self.degraded and should_degrade(st.failure_rate_pct, st.total,
                                                self._config):
            self.degraded = True
            self._emit_event(KIND_DEGRADED, SEV_WARNING,
                             st=st, error_class=last_error_class)
        elif self.degraded and should_clear_degraded(st.failure_rate_pct,
                                                     self._config):
            self.degraded = False
            self._emit_event(KIND_HEALTHY, SEV_INFO, st=st,
                             error_class=last_error_class)
        if prev_degraded != self.degraded:
            self._last_verdict = self.verdict()

    # ------------------------------------------------------------------ #
    # Recovery-probe lifecycle (the ONLY path out of quarantine)
    # ------------------------------------------------------------------ #
    def may_probe(self) -> bool:
        """Whether a recovery probe may be sent now.

        The only transition QUARANTINED -> PROBING: once the cool-off has
        elapsed the model is authorized for a lightweight probe. Production
        traffic never drives this transition.
        """
        if self.state is HealthState.PROBING:
            return True
        if self.state is HealthState.QUARANTINED and cool_off_elapsed(
            self.quarantined_at, self._now(), self._config
        ):
            self.state = HealthState.PROBING
            self.probe_successes = 0
            self._emit_event(KIND_RECOVERY_PROBE_STARTED, SEV_INFO)
            return True
        return False

    def record_probe_success(self) -> None:
        """Record a successful recovery probe (PROBING -> HEALTHY at threshold)."""
        if self.state is HealthState.PROBING:
            self._record(True, None, None)
            self.probe_successes += 1
            if self.probe_successes >= self._config.probe_success_threshold:
                self._restore_healthy(probe_recovery=True)
        # In any other state a stray probe success never clears a quarantine.

    def record_probe_failure(self, error_class: str | None = None) -> None:
        """Record a failed recovery probe (PROBING -> QUARANTINED, cool-off reset).

        A probe failure while still quarantined (cool-off not elapsed) merely
        refreshes the cool-off — the model stays down.
        """
        cls = error_class or ERR_UNKNOWN
        if self.state is HealthState.PROBING:
            self._record(False, None, cls)
            self._enter_quarantine(
                reason="probe_failed",
                error_class=cls,
                kind=KIND_QUARANTINE_REASSERTED,
            )
        elif self.state is HealthState.QUARANTINED:
            self._record(False, None, cls)
            self.quarantined_at = self._now()  # reset the cool-off

    # ------------------------------------------------------------------ #
    # Transitions
    # ------------------------------------------------------------------ #
    def _enter_quarantine(
        self,
        reason: str,
        error_class: str | None,
        kind: str = KIND_QUARANTINED,
    ) -> None:
        self.state = HealthState.QUARANTINED
        self.quarantined_at = self._now()
        self.probe_successes = 0
        self.degraded = False  # quarantine supersedes the degraded verdict
        self._emit_event(kind, SEV_CRITICAL, error_class=error_class, detail=reason)

    def _restore_healthy(self, probe_recovery: bool) -> None:
        was_quarantined = self.state is not HealthState.HEALTHY
        self.state = HealthState.HEALTHY
        self.quarantined_at = None
        self.probe_successes = 0
        self.degraded = False
        self._window.clear()  # clean slate; recovery is earned, then reset
        self._last_verdict = VERDICT_HEALTHY
        if probe_recovery and was_quarantined:
            self._emit_event(KIND_RECOVERED, SEV_INFO)

    # ------------------------------------------------------------------ #
    # Operator overrides
    # ------------------------------------------------------------------ #
    def mark_healthy(self, detail: str = "operator") -> None:
        """Force the model healthy (operator control, e.g. after a fix)."""
        self._restore_healthy(probe_recovery=False)
        self._emit_event(KIND_OPERATOR_MARKED_HEALTHY, SEV_INFO, detail=detail)

    def mark_unhealthy(self, detail: str = "operator") -> None:
        """Force the model into quarantine (operator control, e.g. Ollama down)."""
        self._enter_quarantine(
            reason=detail,
            error_class=None,
            kind=KIND_OPERATOR_MARKED_UNHEALTHY,
        )

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #
    def _emit_event(
        self,
        kind: str,
        severity: str,
        *,
        st: WindowStats | None = None,
        error_class: str | None = None,
        detail: str = "",
    ) -> None:
        st = st or self.stats()
        event = HealthEvent(
            kind=kind,
            provider=self.provider,
            model=self.model,
            state=self.state.value,
            verdict=self.verdict(),
            failure_rate_pct=round(st.failure_rate_pct, 2),
            window_samples=st.total,
            latency_p95_ms=(
                round(st.latency_p95_ms, 2) if st.latency_p95_ms is not None else None
            ),
            error_class=error_class or self.last_error_class,
            severity=severity,
            detail=detail,
            ts=self._now(),
        )
        self._emit(event)


class HealthMonitor:
    """Composition root: per-(provider, model) health registry + event fan-out.

    The public health-signal contract for the gateway proxy and FinOps
    chooser: ``is_healthy(provider, model)``,
    ``health_status(provider, model)``, ``statuses()``.
    """

    def __init__(
        self,
        config: HealthConfig | None = None,
        *,
        now: HealthClock | None = None,
        audit_sinks: list[HealthSink] | None = None,
        alert_sinks: list[HealthSink] | None = None,
        alert_severity: str = SEV_WARNING,
    ) -> None:
        self._config = config or default_config()
        self._now = now or time.monotonic
        self._audit: list[HealthSink] = list(audit_sinks or [NoopHealthSink()])
        self._alert: list[HealthSink] = list(alert_sinks or [])
        self._alert_severity = alert_severity
        self._entries: dict[tuple[str, str], ModelHealth] = {}

    # -- configuration ------------------------------------------------------ #
    @property
    def config(self) -> HealthConfig:
        return self._config

    def add_audit_sink(self, sink: HealthSink) -> None:
        self._audit.append(sink)

    def add_alert_sink(self, sink: HealthSink) -> None:
        self._alert.append(sink)

    # -- registry ----------------------------------------------------------- #
    def get(self, provider: str, model: str) -> ModelHealth | None:
        return self._entries.get((provider, model))

    def get_or_create(self, provider: str, model: str) -> ModelHealth:
        key = (provider, model)
        entry = self._entries.get(key)
        if entry is None:
            entry = ModelHealth(
                provider, model, self._config, now=self._now, emit=self._emit
            )
            self._entries[key] = entry
        return entry

    # -- outcome recording -------------------------------------------------- #
    def record_success(self, provider: str, model: str, latency_ms: float | None = None) -> None:
        self.get_or_create(provider, model).record_success(latency_ms)

    def record_failure(
        self,
        provider: str,
        model: str,
        latency_ms: float | None = None,
        *,
        error: BaseException | None = None,
        error_class: str | None = None,
    ) -> None:
        if error_class is None and error is not None:
            error_class = classify_error(error)
        self.get_or_create(provider, model).record_failure(latency_ms, error_class)

    def record_probe_success(self, provider: str, model: str) -> None:
        self.get_or_create(provider, model).record_probe_success()

    def record_probe_failure(
        self, provider: str, model: str, error_class: str | None = None
    ) -> None:
        self.get_or_create(provider, model).record_probe_failure(error_class)

    def may_probe(self, provider: str, model: str) -> bool:
        return self.get_or_create(provider, model).may_probe()

    # -- operator overrides ------------------------------------------------- #
    def mark_healthy(self, provider: str, model: str, detail: str = "operator") -> None:
        self.get_or_create(provider, model).mark_healthy(detail)

    def mark_unhealthy(self, provider: str, model: str, detail: str = "operator") -> None:
        self.get_or_create(provider, model).mark_unhealthy(detail)

    # -- the health signal -------------------------------------------------- #
    def is_healthy(self, provider: str, model: str) -> bool:
        """Whether (provider, model) may serve production traffic.

        ``False`` for a degraded, quarantined or probing model — the caller
        routes to the fallback chain instead.
        """
        return self.get_or_create(provider, model).is_healthy()

    def health_status(self, provider: str, model: str) -> dict[str, Any]:
        """Queryable per-model health report (state, verdict, window stats)."""
        return self.get_or_create(provider, model).status()

    def statuses(self) -> dict[str, dict[str, Any]]:
        """All registered models' health reports, keyed ``provider/model``."""
        return {
            f"{provider}/{model}": entry.status()
            for (provider, model), entry in sorted(self._entries.items())
        }

    def reset_all(self) -> None:
        """Clear every entry (operator/testing control)."""
        self._entries.clear()

    # -- event fan-out ------------------------------------------------------ #
    def _emit(self, event: HealthEvent) -> None:
        """Audit sinks receive everything; alert sinks receive severity >= cut.

        A raising sink propagates (fail closed): a lost audit/alert record is
        never silent.
        """
        for sink in self._audit:
            sink.record(event)
        if SEVERITY_RANK[event.severity] >= SEVERITY_RANK[self._alert_severity]:
            for sink in self._alert:
                sink.record(event)
