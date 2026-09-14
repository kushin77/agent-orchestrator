"""Transport-seam tests: URL shape, headers, error mapping (issue #428)."""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.paperclip import client as client_mod
from integrations.paperclip import model as model_mod

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "api.json"


def make_client(**kwargs):
    transport = client_mod.FixtureTransport(FIXTURE, token="tok-123", run_id="run-1")
    return client_mod.PaperclipClient(company_id="acme", transport=transport, **kwargs), transport


def test_paths_prefix_and_company_scoping():
    client, transport = make_client()
    client.health()
    client.openapi()
    client.agents()
    client.issues()
    assert [r["path"] for r in transport.requests] == [
        "/api/health",
        "/api/openapi.json",
        "/api/companies/acme/agents",
        "/api/companies/acme/issues",
    ]
    assert all(r["path"].startswith("/api") for r in transport.requests)
    assert all(
        r["path"].startswith("/api/companies/acme/")
        for r in transport.requests
        if r["path"].endswith(("agents", "issues"))
    )


def test_auth_header_on_every_request():
    client, transport = make_client()
    client.agents()
    client.health()
    assert all(r["headers"]["Authorization"] == "Bearer tok-123" for r in transport.requests)


def test_run_header_only_on_mutating_calls():
    client, transport = make_client()
    client.issues()
    client.create_issue({"id": "x"})
    client.update_issue("428", {"status": "done"})
    get_req = transport.requests[0]
    post_req = transport.requests[1]
    patch_req = transport.requests[2]
    assert "X-Paperclip-Run-Id" not in get_req["headers"]
    assert post_req["headers"]["X-Paperclip-Run-Id"] == "run-1"
    assert patch_req["headers"]["X-Paperclip-Run-Id"] == "run-1"


def test_mutating_call_without_run_id_omits_header():
    transport = client_mod.FixtureTransport(FIXTURE, token="tok")
    client = client_mod.PaperclipClient(company_id="acme", transport=transport)
    client.request_topup({"kind": "topup"})
    assert "X-Paperclip-Run-Id" not in transport.requests[-1]["headers"]


def test_error_status_mapping():
    transport = client_mod.FixtureTransport(FIXTURE, token="tok")
    client = client_mod.PaperclipClient(company_id="acme", transport=transport)
    with pytest.raises(model_mod.NotFoundError):
        transport.request("GET", "/api/companies/acme/missing")
    with pytest.raises(model_mod.ConflictError):
        transport.request("POST", "/api/companies/acme/locked")
    with pytest.raises(model_mod.ServiceUnavailableError):
        transport.request("GET", "/api/companies/acme/unavailable")


def test_unmatched_fixture_is_loud():
    transport = client_mod.FixtureTransport(FIXTURE, token="tok")
    with pytest.raises(KeyError):
        transport.request("GET", "/api/companies/acme/nope")


def test_path_must_be_api_prefixed():
    transport = client_mod.FixtureTransport(FIXTURE, token="tok")
    with pytest.raises(ValueError):
        transport.request("GET", "/health")
    http = client_mod.HttpTransport("http://localhost:3100", token="tok")
    assert http.url_for("/api/health") == "http://localhost:3100/api/health"
    with pytest.raises(ValueError):
        http.url_for("/health")


def test_company_id_required_for_scoped_calls():
    transport = client_mod.FixtureTransport(FIXTURE, token="tok")
    client = client_mod.PaperclipClient(transport=transport)
    with pytest.raises(ValueError):
        client.agents()
