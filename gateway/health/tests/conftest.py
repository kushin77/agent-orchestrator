"""Pytest bootstrap: make the ``health`` package importable from any cwd.

``gateway/`` has no ``__init__.py`` (the gateway pillar lane may add one
later), so this inserts ``gateway/`` - three levels above this file - at the
front of ``sys.path``, mirroring the sibling ``limits`` lane. Every test can
then ``from health import ...`` no matter where pytest is invoked from.
"""

from __future__ import annotations

import os
import sys

import pytest

_here = os.path.dirname(os.path.abspath(__file__))
# gateway/health/tests -> gateway/health -> gateway
_gateway_root = os.path.dirname(os.path.dirname(_here))
if _gateway_root not in sys.path:
    sys.path.insert(0, _gateway_root)


class FakeClock:
    """Deterministic monotonic clock for cool-off / probe timing tests."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture()
def clock() -> FakeClock:
    """A fresh fake clock starting at a fixed epoch."""
    return FakeClock()


@pytest.fixture()
def config():
    """A tuned-down HealthConfig so tests reach every state quickly."""
    from health import HealthConfig

    return HealthConfig(
        window_size=12,
        min_samples=3,
        degrade_failure_pct=20.0,
        trip_failure_pct=50.0,
        recover_failure_pct=10.0,
        cool_off_seconds=30.0,
        probe_success_threshold=2,
        slow_threshold_ms=2000.0,
    )


@pytest.fixture()
def audit():
    from health import ListHealthSink

    return ListHealthSink()


@pytest.fixture()
def alerts():
    from health import ListHealthSink

    return ListHealthSink()


@pytest.fixture()
def monitor(config, clock, audit, alerts):
    """A HealthMonitor wired to the tuned config, fake clock and sinks."""
    from health import HealthMonitor

    return HealthMonitor(
        config=config,
        now=clock,
        audit_sinks=[audit],
        alert_sinks=[alerts],
    )
