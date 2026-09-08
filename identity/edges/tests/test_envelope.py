"""Versioned, envelope-standardized responses (AC #4).

Every public response rides in the uniform envelope - ``apiVersion`` +
``requestId`` + ``status`` plus either the downstream body (verbatim) or a
closed edge-error code.
"""

from __future__ import annotations

import pytest

from identity.edges.edge import PublicEdge
from identity.edges.envelope import edge_rejection, passthrough
from identity.edges.model import (
    API_VERSION,
    CODE_BACKEND_UNAVAILABLE,
    CODE_METHOD_NOT_ALLOWED,
    CODE_UNAUTHENTICATED,
    CODE_UNKNOWN_ROUTE,
    EDGE_ERROR_CODES,
)
from identity.edges.tests._support import StubBackend, accepting_verifier

AUTH = {"authorization": "Bearer valid-token"}


def _edge(routes, status=200, body=None):
    stub = StubBackend(status=status, body=body)
    edge = PublicEdge(routes, verifier=accepting_verifier(), backend=stub.call)
    return edge


def test_success_envelope_shape(routes):
    edge = _edge(routes, status=200, body={"usage": {"tokens": 12}})
    response = edge.handle_request("GET", "/v1/tenants/me/usage", headers=AUTH)
    payload = response.to_dict()
    assert set(payload) == {"apiVersion", "requestId", "status", "body"}
    assert payload["apiVersion"] == API_VERSION
    assert payload["status"] == 200
    assert payload["requestId"].startswith("req_")
    assert payload["body"] == {"usage": {"tokens": 12}}


def test_edge_rejection_envelope_shape():
    response = edge_rejection(CODE_UNAUTHENTICATED, request_id="req_x")
    payload = response.to_dict()
    assert set(payload) == {"apiVersion", "requestId", "status", "error"}
    assert payload["status"] == 401
    assert payload["error"]["code"] == CODE_UNAUTHENTICATED
    assert "body" not in payload
    assert payload["error"]["message"]


def test_passthrough_envelope(routes):
    denial = {"error": {"code": "authorization_denied"}}
    response = passthrough(403, denial, request_id="req_y")
    payload = response.to_dict()
    assert payload["status"] == 403
    assert payload["body"] == denial
    assert "error" not in payload  # downstream body is authoritative


@pytest.mark.parametrize(
    "code,status",
    [
        (CODE_UNAUTHENTICATED, 401),
        (CODE_UNKNOWN_ROUTE, 404),
        (CODE_METHOD_NOT_ALLOWED, 405),
        (CODE_BACKEND_UNAVAILABLE, 502),
    ],
)
def test_edge_error_status_map(code, status):
    assert edge_rejection(code).status == status
    assert edge_rejection(code).error_code == code


def test_all_edge_codes_are_closed():
    # The closed vocabulary is small and explicit.
    assert EDGE_ERROR_CODES == {
        CODE_UNAUTHENTICATED,
        CODE_UNKNOWN_ROUTE,
        CODE_METHOD_NOT_ALLOWED,
        CODE_BACKEND_UNAVAILABLE,
    }


def test_unknown_route_envelope(routes):
    edge = _edge(routes)
    response = edge.handle_request("GET", "/v1/nope", headers=AUTH)
    payload = response.to_dict()
    assert payload["status"] == 404
    assert payload["error"]["code"] == CODE_UNKNOWN_ROUTE


def test_request_id_is_present_and_stable(routes):
    edge = _edge(routes)
    first = edge.handle_request("GET", "/v1/tenants/me/usage", headers=AUTH)
    request_id = first.request_id
    assert request_id.startswith("req_")
    assert first.to_dict()["requestId"] == request_id
    # A second request gets its own id.
    second = edge.handle_request("GET", "/v1/tenants/me/usage", headers=AUTH)
    assert second.request_id != request_id


def test_edge_origin_flag():
    assert edge_rejection(CODE_UNKNOWN_ROUTE).edge_origin is True
    assert passthrough(200, {"ok": True}).edge_origin is False


def test_response_is_serializable(routes):
    edge = _edge(routes, status=200, body={"ok": True})
    response = edge.handle_request("POST", "/v1/agents/a1/tasks", headers=AUTH)
    import json

    json.dumps(response.to_dict())  # must not raise
