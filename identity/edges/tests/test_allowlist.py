"""Allowlist boundary tests - which routes the world may reach (AC #1/#3).

Covers the explicit path+method allowlist and the path-traversal guard, with
negative controls proving the edge fails closed: unknown routes, internal-only
backend routes, wrong methods, and every traversal/encoded-traversal shape are
rejected before any backend call.
"""

from __future__ import annotations

import pytest

from identity.edges.allowlist import (
    AllowlistError,
    EdgeRoute,
    PublicRoutes,
)
from identity.edges.edge import PublicEdge
from identity.edges.envelope import (
    CODE_BACKEND_UNAVAILABLE,
    CODE_METHOD_NOT_ALLOWED,
    CODE_UNKNOWN_ROUTE,
)
from identity.edges.tests._support import StubBackend, accepting_verifier


def _edge_with(routes: PublicRoutes) -> tuple[PublicEdge, StubBackend]:
    backend = StubBackend(status=200, body={"ok": True})
    edge = PublicEdge(routes, verifier=accepting_verifier(), backend=backend.call)
    return edge, backend


def _unauth_edge(routes: PublicRoutes) -> PublicEdge:
    """Edge whose every route is unauthenticated (isolate the allowlist)."""
    unauth_routes = PublicRoutes(
        EdgeRoute(
            route_id=r.route_id,
            method=r.method,
            api_path=r.api_path,
            backend_path=r.backend_path,
            authenticated=False,
            description=r.description,
        )
        for r in routes.routes
    )
    backend = StubBackend(status=200, body={"ok": True})
    return PublicEdge(unauth_routes, verifier=None, backend=backend.call)


# --------------------------------------------------------------------------- #
# Positive: the AC #1 public surface is reachable
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "method,path,route_id",
    [
        ("POST", "/v1/agents/agent_1/tasks", "tasks.create"),
        ("GET", "/v1/tenants/me/usage", "usage.me"),
        ("GET", "/v1/tenants/me/audit/export", "audit.export"),
        ("GET", "/v1/admin/agents", "admin.agents.list"),
        ("POST", "/v1/admin/tenants", "admin.tenants.create"),
        ("GET", "/healthz", "health.live"),
    ],
)
def test_public_surface_is_allowlisted(routes, method, path, route_id):
    decision = routes.match(method, path)
    assert decision.allowed is True
    assert decision.route.route_id == route_id


def test_allowlisted_request_reaches_backend(routes):
    edge, backend = _edge_with(routes)
    response = edge.handle_request("POST", "/v1/agents/agent_1/tasks",
                                   headers={"authorization": "Bearer t"},
                                   body={"taskType": "classify"})
    assert response.status == 200
    assert response.ok
    assert backend.calls  # the backend was reached


def test_path_params_are_extracted(routes):
    edge, backend = _edge_with(routes)
    edge.handle_request("POST", "/v1/agents/agent_1/tasks",
                        headers={"authorization": "Bearer t"})
    assert backend.last is not None
    assert backend.last.backend_path == "/v1/agents/agent_1/tasks"


def test_healthz_is_public_without_authn(routes):
    edge = _unauth_edge(routes)
    response = edge.handle_request("GET", "/healthz")
    assert response.status == 200


# --------------------------------------------------------------------------- #
# Negative: unknown routes (AC #3 - not allowlisted = not reachable)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    [
        "/v1/does-not-exist",
        "/v2/agents/agent_1/tasks",  # unversioned/other version - never public
        "/agents/agent_1/tasks",  # missing version prefix
        "/internal/reindex",
        "/v1/admin/internal/jobs",
        "/v1/internal/secrets",
        "/v1/agents",  # collection root not allowlisted
        "/v1/tenants/globex/usage",  # raw tenant id never public - only /me
        "/v1/tenants/me",  # partial path not allowlisted
        "/",
        "",
    ],
)
def test_unknown_routes_are_rejected(routes, path):
    edge, backend = _edge_with(routes)
    response = edge.handle_request("GET", path,
                                   headers={"authorization": "Bearer t"})
    assert response.status == 404
    assert response.error_code == CODE_UNKNOWN_ROUTE
    assert backend.calls == []  # never reached the backend


def test_internal_backend_route_is_not_published(routes):
    """The backend serves /internal/reindex, but the edge never publishes it."""
    edge, backend = _edge_with(routes)
    # The stub backend would answer anything - an internal route "exists"
    # downstream.  Without an allowlist entry the edge still rejects it.
    response = edge.handle_request("POST", "/internal/reindex",
                                   headers={"authorization": "Bearer t"})
    assert response.status == 404
    assert response.error_code == CODE_UNKNOWN_ROUTE
    assert backend.calls == []
    assert not edge.is_public_path("/internal/reindex")


# --------------------------------------------------------------------------- #
# Negative: path traversal / encoded traversal (fail closed, AC #3)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    [
        # raw dot-dot traversal past the allowlist
        "/v1/agents/../../internal/secret",
        "/v1/agents/a1/../../../internal/secret",
        # percent-encoded dot-dot (%2e%2e == ..)
        "/v1/agents/%2e%2e/internal/secret",
        "/v1/agents/%2e%2e%2f%2e%2e%2finternal/secret",
        "/v1/agents/..%2f..%2finternal/secret",
        # encoded slash smuggled inside a segment
        "/v1%2fagents/a1/tasks",
        "/v1/agents/a1%2ftasks",
        # single-dot and mixed dot segments
        "/v1/agents/./tasks",
        "/v1/agents/a1/%2e/tasks",
        # double-encoded traversal (%252e%252e decodes once to %2e%2e -> ..)
        "/v1/agents/a1/%252e%252e/internal",
        # empty segments / trailing slash
        "/v1//agents/a1/tasks",
        "/v1/agents/a1/tasks/",
        # malformed percent-encoding
        "/v1/agents/%zz/tasks",
        "/v1/agents/a1/%2",
        # backslash is not a forward path (treated as unknown)
        "/v1\\agents\\a1\\tasks",
    ],
)
def test_traversal_and_malformed_paths_are_rejected(routes, path):
    edge, backend = _edge_with(routes)
    response = edge.handle_request("POST", path,
                                   headers={"authorization": "Bearer t"})
    assert response.status == 404
    assert response.error_code == CODE_UNKNOWN_ROUTE
    assert backend.calls == []


def test_traversal_cannot_smuggle_a_param(routes):
    """A traversal attempt disguised as a path parameter is refused."""
    edge, backend = _edge_with(routes)
    response = edge.handle_request(
        "POST", "/v1/agents/..%2f..%2finternal/tasks",
        headers={"authorization": "Bearer t"},
    )
    assert response.status == 404
    assert backend.calls == []


# --------------------------------------------------------------------------- #
# Negative: method allowlist (AC #1 - path allowlist + method allowlist)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1/agents/agent_1/tasks"),  # tasks.create is POST-only
        ("DELETE", "/v1/agents/agent_1/tasks"),
        ("PUT", "/v1/tenants/me/usage"),
        ("PATCH", "/healthz"),
    ],
)
def test_method_not_allowed_is_rejected(routes, method, path):
    edge, backend = _edge_with(routes)
    response = edge.handle_request(method, path,
                                   headers={"authorization": "Bearer t"})
    assert response.status == 405
    assert response.error_code == CODE_METHOD_NOT_ALLOWED
    assert backend.calls == []


# --------------------------------------------------------------------------- #
# Route-table validation (fail closed at construction)
# --------------------------------------------------------------------------- #


def test_duplicate_publication_is_rejected():
    spec = {
        "route_id": "dup",
        "method": "POST",
        "api_path": "/v1/agents/{agentId}/tasks",
        "backend_path": "/v1/agents/{agentId}/tasks",
    }
    with pytest.raises(AllowlistError):
        PublicRoutes([EdgeRoute(**spec), EdgeRoute(**spec)])


def test_non_allowlisted_method_is_rejected():
    spec = {
        "route_id": "x",
        "method": "TRACE",
        "api_path": "/v1/x",
        "backend_path": "/v1/x",
    }
    with pytest.raises(AllowlistError):
        PublicRoutes([EdgeRoute(**spec)])


def test_malformed_template_is_rejected():
    spec = {
        "route_id": "x",
        "method": "GET",
        "api_path": "/v1/a{bad",  # stray brace
        "backend_path": "/v1/x",
    }
    with pytest.raises(AllowlistError):
        PublicRoutes([EdgeRoute(**spec)])


def test_empty_allowlist_is_rejected():
    with pytest.raises(AllowlistError):
        PublicRoutes([])


# --------------------------------------------------------------------------- #
# Backend unavailable (transport failure never leaks internals)
# --------------------------------------------------------------------------- #


def test_backend_transport_failure_is_502(routes):
    backend = StubBackend(transport_error=True)
    edge = PublicEdge(routes, verifier=accepting_verifier(), backend=backend.call)
    response = edge.handle_request(
        "POST", "/v1/agents/agent_1/tasks",
        headers={"authorization": "Bearer t"},
    )
    assert response.status == 502
    assert response.error_code == CODE_BACKEND_UNAVAILABLE
    assert response.edge_origin
