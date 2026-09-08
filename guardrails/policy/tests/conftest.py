"""Pytest bootstrap: make the ``policy`` package importable from any cwd.

``guardrails/`` has no ``__init__.py`` (mirroring ``engine/``), so this inserts
``guardrails/`` — two levels above this file — at the front of ``sys.path``.
Every test can then ``from policy import ...``.  Also exposes this tests
directory so tests can ``import support`` if needed.

Kept free of sibling constants (the plain module name ``conftest`` is shared
across test directories when suites run together).
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# guardrails/policy/tests -> guardrails/policy -> guardrails
_guardrails_root = os.path.dirname(os.path.dirname(_here))
if _guardrails_root not in sys.path:
    sys.path.insert(0, _guardrails_root)
if _here not in sys.path:
    sys.path.insert(0, _here)

import pytest


@pytest.fixture()
def make_policy():
    """Build a validated Policy from an in-code policy document mapping."""
    from policy import Policy
    from policy.loader import policy_from_mapping

    def _make(document: dict, source: str = "test") -> Policy:
        return policy_from_mapping(document, source=source)

    return _make


@pytest.fixture()
def shipped_bundle_dir():
    """Absolute path of the shipped example bundle (bundles/platform)."""
    from policy.startup import default_bundle_dir

    return default_bundle_dir()


@pytest.fixture()
def shipped_controls():
    """The shipped controls registry (all controls default OFF)."""
    from policy.startup import default_controls_file
    from policy.controls import ControlRegistry

    return ControlRegistry.load_yaml(default_controls_file())


@pytest.fixture()
def all_controls_on():
    """A controls registry with every shipped control flipped ON (for tests).

    Shipping-ON controls carry the required on_since_rationale; this registry
    exists only to exercise the engine, never as the deployed default.
    """
    from policy.controls import ControlRegistry

    return ControlRegistry.from_mapping(
        {
            "version": 1,
            "controls": [
                {
                    "id": control_id,
                    "name": control_id,
                    "description": f"{control_id} enabled for tests",
                    "enabled": True,
                    "mode": "block",
                    "implemented_by": ["guardrails/policy/tests"],
                    "since": "issue #26 tests",
                    "on_since_rationale": "test-only registry enabling the control",
                }
                for control_id in ("model-call-budget", "tool-use-guard", "data-egress-guard")
            ],
        }
    )
