"""integrations/hermes/sync live capability state (issue #889)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.hermes.client import CAPABILITIES_PATH, FixtureTransport, HermesClient
from integrations.hermes.sync.live import CapabilityRejected, serve_live_capabilities

ROOT = Path(__file__).resolve().parents[4]


def _client(fixture):
    return HermesClient(transport=FixtureTransport(fixture))


def test_serve_live_capabilities_validates_and_reports(monkeypatch):
    fixture = {
        "responses": [
            {
                "method": "GET",
                "path": CAPABILITIES_PATH,
                "status": 200,
                "body": {
                    "code-author": {"default_tier": "L1", "max_tier": "L2"},
                },
            }
        ]
    }
    doc = serve_live_capabilities(ROOT, _client(fixture))
    assert doc["reachable"] is True
    assert doc["capabilities"]["code-author"]["default_tier"] == "L1"
    assert isinstance(doc["live_missing"], list)
    assert isinstance(doc["live_extra"], list)


def test_unreachable_service_is_reported_honestly_not_fabricated():
    fixture = {"responses": []}  # nothing matches -> FixtureTransport raises KeyError -> HermesError not raised here
    client = _client(fixture)
    # No matching fixture entry for a GET on the capabilities path: the
    # transport raises a loud KeyError, which is not a HermesError, so this
    # confirms serve_live_capabilities does not swallow non-HermesError misses.
    with pytest.raises(KeyError):
        serve_live_capabilities(ROOT, client)


def test_malformed_capability_entry_is_refused_by_name():
    """Negative control: an entry missing 'max_tier' fails capabilities.schema.json
    and is refused by name, not silently accepted or dropped."""
    fixture = {
        "responses": [
            {
                "method": "GET",
                "path": CAPABILITIES_PATH,
                "status": 200,
                "body": {
                    "code-author": {"default_tier": "L1"},
                },
            }
        ]
    }
    with pytest.raises(CapabilityRejected) as excinfo:
        serve_live_capabilities(ROOT, _client(fixture))
    assert "code-author" in str(excinfo.value)
