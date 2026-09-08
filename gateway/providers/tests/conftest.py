"""Pytest bootstrap: make the ``providers`` package importable from any cwd.

``gateway/`` has no ``__init__.py`` (a later gateway-phase lane owns adding
one, making the package importable as ``gateway.providers``), so this inserts
``gateway/`` - three levels above this file - at the front of ``sys.path``.
Every test can then ``from providers import ...`` no matter where pytest is
invoked from (mirrors identity/rbac/tests/conftest.py).

Also provides the shared offline fixtures: the sentiment output schema (JSON
Schema, also on disk under tests/fixtures/schemas/) and a persistent vault.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

_here = os.path.dirname(os.path.abspath(__file__))
# gateway/providers/tests -> gateway/providers -> gateway
_gateway_root = os.path.dirname(os.path.dirname(_here))
if _gateway_root not in sys.path:
    sys.path.insert(0, _gateway_root)
# Expose the tests dir so test modules can ``import support``.
if _here not in sys.path:
    sys.path.insert(0, _here)

from providers.config import default_provider_configs  # noqa: E402
from providers.contract import (  # noqa: E402
    DEFAULT_TIER,
    SYSTEM,
    USER,
    CallContext,
    ChatMessage,
)
from providers.transport import HttpResponse, RecordingTransport  # noqa: E402
from providers.vault import ApiKeyVault, FernetBackend, new_master_key  # noqa: E402

FAKE_KEY = "fake-api-key-1234567890abcdef"
FAKE_KEY_2 = "fake-api-key-2-abcdef0987654321"
VALID_CONTENT = '{"sentiment": "positive", "confidence": 0.92}'
CONTENT_OBJ = {"sentiment": "positive", "confidence": 0.92}

SCHEMA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "schemas", "sentiment.schema.json"
)


def _load_schema() -> dict:
    with open(SCHEMA_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture()
def sentiment_schema() -> dict:
    """The sentiment output schema (JSON Schema dict + file-backed)."""
    return _load_schema()


@pytest.fixture()
def sentiment_schema_file() -> str:
    """Path to the sentiment output-schema JSON file."""
    return SCHEMA_FILE


@pytest.fixture()
def ctx() -> CallContext:
    return CallContext(tenant_id="acme", agent_id="agent-1", logical_key=DEFAULT_TIER)


@pytest.fixture()
def configs() -> dict:
    """Platform-default provider configs (fresh copy per test)."""
    return default_provider_configs()


@pytest.fixture()
def vault(tmp_path) -> ApiKeyVault:
    """An on-disk, encrypted-at-rest vault (master key generated per test)."""
    return ApiKeyVault(
        FernetBackend(new_master_key().encode("ascii")),
        path=str(tmp_path / "vault.json"),
    )


@pytest.fixture()
def recording() -> RecordingTransport:
    """A recording transport with no scripted responses (fails loudly)."""
    return RecordingTransport()


def http_ok(body: dict) -> HttpResponse:
    """A canned 200 response with a JSON body."""
    return HttpResponse(status=200, headers={"content-type": "application/json"}, body=json.dumps(body))


def make_messages() -> list[ChatMessage]:
    return [
        ChatMessage(SYSTEM, "Classify the sentiment as JSON matching the schema."),
        ChatMessage(USER, "The deal closed on time and under budget."),
    ]
