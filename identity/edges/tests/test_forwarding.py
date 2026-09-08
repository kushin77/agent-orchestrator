"""Forwarding contract - built, never copied; authn never authz (AC #2).

The edge forwards the verified identity + role snapshot and the allowlisted
request, and passes the backend answer back untouched.  It never lets a
client smuggle identity headers into the internal call, never trusts a client
tenant, and never makes (or re-makes) an authorization decision - a backend
403 (authz denial) passes through unchanged.
"""

from __future__ import annotations

from identity.edges.edge import PublicEdge
from identity.edges.tests._support import StubBackend, accepting_verifier


def _edge(routes, backend=None):
    if callable(backend):
        edge = PublicEdge(
            routes, verifier=accepting_verifier(), backend=backend
        )
        return edge, None
    stub = backend or StubBackend(status=200, body={"ok": True})
    edge = PublicEdge(routes, verifier=accepting_verifier(), backend=stub.call)
    return edge, stub


AUTH = {"authorization": "Bearer valid-token"}


# --------------------------------------------------------------------------- #
# Build-don't-copy: client headers never cross except the allowlisted trace
# --------------------------------------------------------------------------- #


def test_only_trace_header_is_forwarded(routes):
    edge, backend = _edge(routes)
    edge.handle_request(
        "POST",
        "/v1/agents/agent_1/tasks",
        headers={
            "authorization": "Bearer valid-token",
            "x-tenant-id": "globex",
            "x-user-id": "mallory",
            "x-api-key": "sekrit",
            "cookie": "session=steal-me",
            "x-request-id": "trace-123",
        },
        body={"taskType": "classify"},
    )
    forwarded = backend.last
    assert forwarded is not None
    # Only the allowlisted trace header survived; nothing identity-bearing.
    assert forwarded.headers == {"x-request-id": "trace-123"}


def test_client_cannot_smuggle_identity_headers(routes):
    edge, backend = _edge(routes)
    edge.handle_request(
        "GET",
        "/v1/tenants/me/usage",
        headers={
            "authorization": "Bearer valid-token",
            "x-tenant-id": "globex",  # attacker claims another tenant
            "x-user-id": "attacker",
        },
    )
    forwarded = backend.last
    assert forwarded is not None
    # The edge derived identity from the *verified* claims, not the headers.
    assert forwarded.identity.tenant_id == "acme"
    assert forwarded.identity.subject_id == "u_alice"
    assert "x-tenant-id" not in forwarded.headers
    assert "authorization" not in forwarded.headers


def test_body_only_forwarded_for_body_methods(routes):
    edge, backend = _edge(routes)
    edge.handle_request(
        "POST",
        "/v1/admin/tenants",
        headers=AUTH,
        body={"name": "acme"},
    )
    assert backend.last.body == {"name": "acme"}
    backend.reset()
    edge.handle_request("GET", "/v1/admin/agents", headers=AUTH, body={"junk": 1})
    assert backend.last.body is None  # GET bodies are never forwarded


def test_me_route_renders_tenant_from_claims_not_client(routes):
    """GET /v1/tenants/me/usage -> backend /v1/tenants/{claims tenant}/usage."""
    edge, backend = _edge(routes)
    edge.handle_request(
        "GET",
        "/v1/tenants/me/usage",
        headers={
            "authorization": "Bearer valid-token",
            "x-tenant-id": "globex",  # ignored
        },
    )
    forwarded = backend.last
    assert forwarded.backend_path == "/v1/tenants/acme/usage"
    assert forwarded.identity.tenant_id == "acme"


def test_path_params_flow_into_backend_path(routes):
    edge, backend = _edge(routes)
    edge.handle_request("POST", "/v1/agents/agent_99/tasks", headers=AUTH)
    assert backend.last.backend_path == "/v1/agents/agent_99/tasks"


def test_role_snapshot_is_forwarded_as_context(routes):
    edge, backend = _edge(routes)
    edge.handle_request("GET", "/v1/admin/agents", headers=AUTH)
    # accepting_verifier's default claims role = "member"
    assert backend.last.role_snapshot == ("member",)


# --------------------------------------------------------------------------- #
# Authn never authz - the edge performs no authorization
# --------------------------------------------------------------------------- #


def test_backend_authz_denial_passes_through_untouched(routes):
    """A 403 from the backend stays a 403 - the edge never turns it into 200."""
    denial = {"error": {"code": "authorization_denied", "permission": "agent:run"}}
    backend = StubBackend(status=403, body=denial)
    edge, _ = _edge(routes, backend=backend)
    response = edge.handle_request(
        "POST", "/v1/agents/agent_1/tasks", headers=AUTH
    )
    assert response.status == 403
    assert response.body == denial  # verbatim, untouched
    assert response.to_dict()["body"] == denial


def test_downstream_statuses_pass_through_verbatim(routes):
    for status in (200, 404, 409, 422, 429, 500, 503):
        body = {"status": status}
        backend = StubBackend(status=status, body=body)
        edge, _ = _edge(routes, backend=backend)
        response = edge.handle_request(
            "GET", "/v1/admin/agents", headers=AUTH
        )
        assert response.status == status, f"status {status} altered"
        assert response.body == body


def test_edge_does_not_decide_admin_eligibility(routes):
    """The edge forwards even a non-admin caller to the admin route.

    Whether the caller may act is answered by the backend: here the backend
    denies a 'member' role and the edge relays that denial.  The edge itself
    never compares the role snapshot to the route.
    """

    def authorizing_backend(request):
        if request.backend_path.startswith("/v1/admin/"):
            if "admin" not in request.role_snapshot:
                return 403, {"error": {"code": "permission_denied"}}
        return 200, {"ok": True}

    edge, _ = _edge(routes, backend=authorizing_backend)
    # Caller role = member (default claims) -> backend denies admin.
    denied = edge.handle_request("GET", "/v1/admin/agents", headers=AUTH)
    assert denied.status == 403
    # A non-admin route is unaffected (backend allows).
    allowed = edge.handle_request(
        "GET", "/v1/tenants/me/usage", headers=AUTH
    )
    assert allowed.status == 200
