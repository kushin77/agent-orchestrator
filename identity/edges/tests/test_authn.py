"""Authentication at the edge - authN, never authz (AC #2).

Negative controls prove the edge fails closed on a missing/invalid/expired/
revoked/cross-tenant token (401, no backend call), and positive tests prove a
verified session authenticates.  The integration tests inject the *real*
offline issue #35 ``identity.sso`` ``SsoService.verify_session`` - the edge
consumes the platform's real session semantics rather than a stand-in.
"""

from __future__ import annotations

import pytest

from identity.edges.allowlist import EdgeRoute, PublicRoutes, default_public_routes
from identity.edges.authn import UNAUTHENTICATED_MESSAGE, sso_verifier, verify_caller
from identity.edges.edge import PublicEdge
from identity.edges.envelope import CODE_UNAUTHENTICATED
from identity.edges.tests._support import (
    NOW,
    StubBackend,
    accepting_verifier,
    claims_for,
    rejecting_verifier,
)

AUTH_HEADER = {"authorization": "Bearer valid-token"}


def _edge(routes, verifier=None, backend=None) -> tuple[PublicEdge, StubBackend]:
    stub = backend or StubBackend(status=200, body={"ok": True})
    edge = PublicEdge(routes, verifier=verifier or accepting_verifier(), backend=stub.call)
    return edge, stub


# --------------------------------------------------------------------------- #
# Missing / malformed tokens (negative)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "headers",
    [
        {},  # no Authorization header at all
        {"authorization": ""},
        {"authorization": "Basic dXNlcjpwYXNz"},  # not a bearer token
        {"authorization": "Bearer"},  # bearer with no token
        {"authorization": "Bearer   "},  # blank token
        {"x-tenant-id": "acme"},  # a client header is never identity
    ],
)
def test_missing_or_malformed_token_is_rejected(routes, headers):
    edge, backend = _edge(routes)
    response = edge.handle_request("GET", "/v1/tenants/me/usage", headers=headers)
    assert response.status == 401
    assert response.error_code == CODE_UNAUTHENTICATED
    assert response.edge_origin
    assert backend.calls == []


def test_invalid_token_is_rejected(routes):
    edge, backend = _edge(routes, verifier=rejecting_verifier())
    response = edge.handle_request(
        "GET", "/v1/tenants/me/usage", headers={"authorization": "Bearer garbage"}
    )
    assert response.status == 401
    assert response.error_code == CODE_UNAUTHENTICATED
    assert backend.calls == []


def test_no_verifier_configured_fails_closed_for_authenticated_route(routes):
    backend = StubBackend(status=200, body={"ok": True})
    edge = PublicEdge(routes, verifier=None, backend=backend.call)
    response = edge.handle_request(
        "GET", "/v1/tenants/me/usage", headers={"authorization": "Bearer t"}
    )
    assert response.status == 401
    assert response.error_code == CODE_UNAUTHENTICATED
    assert backend.calls == []


# --------------------------------------------------------------------------- #
# Valid tokens authenticate (positive)
# --------------------------------------------------------------------------- #


def test_valid_token_authenticates_and_reaches_backend(routes):
    edge, backend = _edge(routes)
    response = edge.handle_request(
        "GET", "/v1/tenants/me/usage",
        headers={"authorization": "Bearer valid-token"},
    )
    assert response.status == 200
    assert backend.last is not None
    assert backend.last.identity is not None
    assert backend.last.identity.tenant_id == "acme"
    assert backend.last.identity.subject_id == "u_alice"


def test_unauthenticated_route_skips_authn(routes):
    backend = StubBackend(status=200, body={"ok": True})
    edge = PublicEdge(
        routes, verifier=rejecting_verifier(), backend=backend.call
    )
    # /healthz is allowlisted unauthenticated - even a rejecting verifier is
    # never consulted, and no token is needed.
    response = edge.handle_request("GET", "/healthz")
    assert response.status == 200
    assert backend.last is not None
    assert backend.last.identity is None


def test_verify_caller_extracts_identity_and_role_snapshot():
    claims = claims_for(tenant_id="acme", subject_id="u_bob", role="admin")
    result = verify_caller(accepting_verifier(claims), "tok", now=NOW)
    assert result.ok
    assert result.identity.tenant_id == "acme"
    assert result.identity.subject_id == "u_bob"
    assert result.identity.subject_type == "user"
    assert result.role_snapshot == ("admin",)


# --------------------------------------------------------------------------- #
# Real issue #35 SsoService integration (offline HS256 sessions)
# --------------------------------------------------------------------------- #


def _real_sso_env(tenant_id: str = "acme"):
    """A real offline SsoService + a signed HS256 session token for tenant."""
    from identity.sso.keystore import KeyStore
    from identity.sso.sessions import SsoService
    from identity.sso.store import InMemoryStore
    from identity.sso.tokens import issue_session_token

    hmac_key = bytes(range(32))
    store = InMemoryStore()
    service = SsoService(
        store=store, keystore=KeyStore(), session_hmac_key=hmac_key
    )
    token, claims = issue_session_token(
        hmac_key,
        tenant_id=tenant_id,
        subject_id="u_alice",
        subject_type="user",
        role="member",
        email="alice@acme.example.com",
        now=NOW,
    )
    return service, store, token, claims


def test_real_sso_session_authenticates_at_the_edge(routes):
    service, _store, token, claims = _real_sso_env("acme")
    backend = StubBackend(status=200, body={"ok": True})
    edge = PublicEdge(
        routes, verifier=sso_verifier(service), backend=backend.call
    )
    response = edge.handle_request(
        "GET", "/v1/tenants/me/usage",
        headers={"authorization": f"Bearer {token}"},
        now=NOW,
    )
    assert response.status == 200
    assert backend.last is not None
    assert backend.last.identity.tenant_id == "acme"
    assert backend.last.identity.subject_id == "u_alice"


def test_real_sso_garbage_token_is_rejected(routes):
    service, _store, _token, _claims = _real_sso_env("acme")
    backend = StubBackend(status=200, body={"ok": True})
    edge = PublicEdge(
        routes, verifier=sso_verifier(service), backend=backend.call
    )
    response = edge.handle_request(
        "GET", "/v1/tenants/me/usage",
        headers={"authorization": "Bearer not-a-real-token"},
        now=NOW,
    )
    assert response.status == 401
    assert response.error_code == CODE_UNAUTHENTICATED
    assert backend.calls == []


def test_real_sso_expired_session_is_rejected(routes):
    from identity.sso.tokens import issue_session_token

    hmac_key = bytes(range(32))
    service, store, _token, _claims = _real_sso_env("acme")
    # Issue a token in the past with ttl that has since elapsed.
    expired, claims = issue_session_token(
        hmac_key,
        tenant_id="acme",
        subject_id="u_alice",
        subject_type="user",
        role="member",
        now=NOW - 7200,
        ttl=3600,
    )
    assert claims["exp"] < NOW  # expired by the fixture clock
    # Rebuild a service bound to the same store/hmac so revocation state and
    # key match (the env above already is; re-verify against it).
    _ = store
    backend = StubBackend(status=200, body={"ok": True})
    edge = PublicEdge(
        routes, verifier=sso_verifier(service), backend=backend.call
    )
    response = edge.handle_request(
        "GET", "/v1/tenants/me/usage",
        headers={"authorization": f"Bearer {expired}"},
        now=NOW,
    )
    assert response.status == 401
    assert response.error_code == CODE_UNAUTHENTICATED
    assert backend.calls == []


def test_real_sso_revoked_session_is_rejected(routes):
    service, store, token, claims = _real_sso_env("acme")
    store.revoke_jti(str(claims["jti"]), NOW)
    backend = StubBackend(status=200, body={"ok": True})
    edge = PublicEdge(
        routes, verifier=sso_verifier(service), backend=backend.call
    )
    response = edge.handle_request(
        "GET", "/v1/tenants/me/usage",
        headers={"authorization": f"Bearer {token}"},
        now=NOW,
    )
    assert response.status == 401
    assert response.error_code == CODE_UNAUTHENTICATED
    assert backend.calls == []


def test_real_sso_cross_tenant_pinned_route_is_rejected():
    """A tenant-pinned route refuses a session minted for another tenant."""
    service, _store, token, _claims = _real_sso_env("globex")
    pinned = PublicRoutes(
        [
            EdgeRoute(
                route_id="usage.pinned",
                method="GET",
                api_path="/v1/tenants/me/usage",
                backend_path="/v1/tenants/{tenantId}/usage",
                authenticated=True,
                expected_tenant="acme",
            )
        ]
    )
    backend = StubBackend(status=200, body={"ok": True})
    edge = PublicEdge(
        pinned, verifier=sso_verifier(service), backend=backend.call
    )
    response = edge.handle_request(
        "GET", "/v1/tenants/me/usage",
        headers={"authorization": f"Bearer {token}"},
        now=NOW,
    )
    assert response.status == 401
    assert response.error_code == CODE_UNAUTHENTICATED
    assert backend.calls == []


def test_authn_rejection_is_opaque():
    """The 401 never leaks the precise failure reason (fail closed)."""
    edge, _backend = _edge(default_public_routes(), verifier=rejecting_verifier())
    response = edge.handle_request(
        "GET", "/v1/tenants/me/usage",
        headers={"authorization": "Bearer anything"},
    )
    assert response.status == 401
    body = response.to_dict()
    assert body["error"]["code"] == CODE_UNAUTHENTICATED
    assert UNAUTHENTICATED_MESSAGE in body["error"]["message"]
    assert "expired" not in body["error"]["message"]
    assert "revoked" not in body["error"]["message"]
