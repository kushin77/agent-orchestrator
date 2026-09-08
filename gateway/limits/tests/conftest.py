"""Pytest bootstrap: make the ``limits`` package importable from any cwd.

``gateway/`` has no ``__init__.py`` (the gateway pillar lane may add one
later), so this inserts ``gateway/`` - three levels above this file - at the
front of ``sys.path``.  Prepending (not appending) is required because the
PyPI ``limits`` rate-limiting library ships a top-level ``limits`` package in
site-packages; appending ``gateway/`` after it would let that third-party
package shadow this one.  Every test can then ``from limits import ...`` no
matter where pytest is invoked from.
"""

from __future__ import annotations

import os
import sys

import pytest

_here = os.path.dirname(os.path.abspath(__file__))
# gateway/limits/tests -> gateway/limits -> gateway
_gateway_root = os.path.dirname(os.path.dirname(_here))
if _gateway_root not in sys.path:
    sys.path.insert(0, _gateway_root)


class FakeClock:
    """Deterministic wall clock for TTL / window / refill tests."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    """A fresh fake clock starting at a fixed epoch."""
    return FakeClock()
