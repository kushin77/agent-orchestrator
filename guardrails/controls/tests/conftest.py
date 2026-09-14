"""Pytest bootstrap: make the ``controls`` namespace package importable.

``guardrails/`` has no ``__init__.py`` (mirroring ``engine/``), so this inserts
``guardrails/`` — two levels above this file — at the front of ``sys.path``.
Every test can then ``from controls.model import ...``.  Kept free of sibling
constants (the plain module name ``conftest`` is shared across test directories
when suites run together).
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# guardrails/controls/tests -> guardrails/controls -> guardrails
_guardrails_root = os.path.dirname(os.path.dirname(_here))
if _guardrails_root not in sys.path:
    sys.path.insert(0, _guardrails_root)
if _here not in sys.path:
    sys.path.insert(0, _here)

import pytest


@pytest.fixture()
def registry_path():
    """Absolute path of the shipped controls registry."""
    from controls.registry import default_controls_path

    return default_controls_path()


@pytest.fixture()
def controls(registry_path):
    """The adapted control descriptors (all default OFF)."""
    from controls.registry import load_controls

    return load_controls(registry_path)


@pytest.fixture()
def control_set(controls):
    """A fresh, all-OFF control set."""
    from controls.model import ControlSet

    return ControlSet(controls)


@pytest.fixture()
def audit_log():
    """A fresh in-memory audit sink."""
    from controls.audit import InMemoryControlAuditLog

    return InMemoryControlAuditLog()
