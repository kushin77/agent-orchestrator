"""Shared fixtures + sys.path bootstrap for the guardrails/sandbox suite.

``guardrails/sandbox`` IS the ``sandbox`` package, so its parent (the
``guardrails`` directory) must be importable for ``import sandbox`` to resolve.
The MCP integration test imports the merged gateway/mcp package (issue #20)
READ-ONLY, so ``gateway/`` is prepended too; ``identity/`` is not required
because the integration test uses an allow-all authz guard to isolate the
sandbox seam under test. Keep this conftest free of sibling constants - pytest
shares the plain module name ``conftest`` across directories (issue #10
lesson).
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)  # guardrails/sandbox == the sandbox package
_GUARDRAILS = os.path.dirname(_PKG_ROOT)  # guardrails/
_GATEWAY = os.path.abspath(os.path.join(_GUARDRAILS, "..", "gateway"))
if _GUARDRAILS not in sys.path:
    sys.path.insert(0, _GUARDRAILS)
if _GATEWAY not in sys.path:
    sys.path.insert(0, _GATEWAY)

import pytest  # noqa: E402

from sandbox.catalog import default_category_map, default_profile_catalog  # noqa: E402
from sandbox.executor import SandboxExecutor  # noqa: E402
from sandbox.offline import OfflineRuntime  # noqa: E402


class RecordingSink:
    """In-memory audit sink with the mcp-shaped append() signature."""

    def __init__(self) -> None:
        self.records = []

    def append(
        self,
        event,
        *,
        status=None,
        ts=None,
        tenant_id=None,
        agent_id=None,
        actor=None,
        detail=None,
    ):
        record = {
            "event": event,
            "status": status,
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "actor": actor,
            "detail": detail,
        }
        self.records.append(record)
        return dict(record)

    @property
    def events(self):
        return list(self.records)


@pytest.fixture
def catalog():
    return default_profile_catalog()


@pytest.fixture
def category_map():
    return default_category_map()


@pytest.fixture
def executor():
    return SandboxExecutor(runtime=OfflineRuntime(enabled=True))


@pytest.fixture
def recording_sink():
    return RecordingSink()
