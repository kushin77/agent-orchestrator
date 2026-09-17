"""The shared seam, from the paperclip side: the extract is real and behaviour did not move (#1208).

`integrations/paperclip/` and `integrations/hermes/` both defined the YAML subset
loader, the JSON-Schema subset validator, `Response`, `error_for_status` and the
transport `Protocol` + fixture transport — body for body, by then already drifted.
They live in `integrations/_seam/` now. Two properties make that worth anything,
and both are asserted here rather than claimed:

* **the adapter exposes the seam, not a copy of it** — `mapping.validate` and
  `mapping.load_yaml` *are* the seam's objects (identity, not equality), and the
  helper bodies are gone from this module, so a re-forked helper fails here;
* **this adapter's own behaviour is unchanged** — the frozen ``/api`` contract,
  the header shape the gate asserts on, and the *superset* validator (the
  stricter side: ``format: date-time``, ``minimum``, ``maximum``) all still hold.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations import _seam
from integrations.paperclip import client as client_mod
from integrations.paperclip import mapping as mapping_mod
from integrations.paperclip import model as model_mod

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "api.json"

#: The helper bodies that used to be defined twice, once per adapter.
SHARED_HELPERS = (
    "_strip_comment",
    "_tokenize",
    "_split_kv",
    "_split_flow",
    "_parse_flow",
    "_scalar",
    "_parse_map",
    "_parse_seq",
    "_parse_block",
    "_type_ok",
    "_validate",
)


def test_the_shared_tools_are_the_seams_objects():
    assert mapping_mod.load_yaml is _seam.load_yaml
    assert mapping_mod.load_yaml_file is _seam.load_yaml_file
    assert mapping_mod.validate is _seam.validate
    assert model_mod.Response is _seam.Response
    assert client_mod.Transport is _seam.Transport


def test_the_adapter_no_longer_defines_the_shared_helpers():
    """A re-forked helper would shadow the seam's and re-open the drift."""
    for name in SHARED_HELPERS:
        assert not hasattr(mapping_mod, name), name
    assert not hasattr(client_mod, "_decode")


def test_the_fixture_transport_is_the_seams_over_this_boundary():
    assert issubclass(client_mod.FixtureTransport, _seam.FixtureTransport)
    assert client_mod.FixtureTransport is not _seam.FixtureTransport, (
        "this adapter narrows the seam (the /api prefix and the auth headers)"
    )
    transport = client_mod.FixtureTransport(FIXTURE, token="tok")
    assert transport.prefix == "/api"
    assert transport.boundary.label == "paperclip"


def test_the_api_prefix_contract_still_holds():
    transport = client_mod.FixtureTransport(FIXTURE, token="tok")
    with pytest.raises(ValueError):
        transport.request("GET", "/health")


def test_the_recorded_headers_are_the_live_paths_headers():
    transport = client_mod.FixtureTransport(FIXTURE, token="tok", run_id="run-1")
    transport.request("POST", "/api/companies/acme/issues", json={"id": "x"})
    recorded = transport.requests[-1]
    assert recorded["headers"]["Authorization"] == "Bearer tok"
    assert recorded["headers"]["X-Paperclip-Run-Id"] == "run-1"
    assert recorded["headers"]["Content-Type"] == "application/json"
    transport.request("GET", "/api/health")
    assert "X-Paperclip-Run-Id" not in transport.requests[-1]["headers"]


def test_error_for_status_renders_through_the_shared_boundary():
    missing = model_mod.error_for_status(404, "/api/companies/acme/nope")
    assert isinstance(missing, model_mod.NotFoundError)
    assert str(missing) == "paperclip /api/companies/acme/nope: HTTP 404"
    assert model_mod.error_for_status(404, "/api/x", "no such record").args[0] == (
        "paperclip /api/x: HTTP 404 — no such record"
    )


def test_the_validator_is_the_supersets_stricter_half():
    schema = {
        "type": "object",
        "properties": {
            "ts": {"type": "string", "format": "date-time"},
            "pct": {"type": "integer", "maximum": 100},
        },
    }
    findings = mapping_mod.validate({"ts": "yesterday", "pct": 101}, schema)
    assert any("ISO-8601" in finding for finding in findings), findings
    assert any("above maximum" in finding for finding in findings), findings


def test_the_loader_is_the_unions_wider_half():
    """The seam's loader reads the flow collections hermes's copy needed."""
    data = mapping_mod.load_yaml(
        "tiers:\n"
        "  - { id: deepseek-v4-flash, provider: deepseek, costPerMTok: 0.21 }\n"
    )
    assert data == {
        "tiers": [{"id": "deepseek-v4-flash", "provider": "deepseek", "costPerMTok": 0.21}]
    }
