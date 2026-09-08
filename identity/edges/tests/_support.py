"""Shared offline test support for identity/edges (not collected by pytest).

Builds the small doubles the edge tests need: stub verifiers (authN seam),
a recording stub backend (downstream passthrough seam), and edge builders.
Nothing here imports a sibling identity lane - the real issue #35
``SsoService`` integration lives in ``test_authn.py``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from identity.edges.allowlist import PublicRoutes
from identity.edges.edge import PublicEdge
from identity.edges.model import ForwardedRequest

# Deterministic fixture clock (matches identity/sso NOW).
NOW = 1_800_000_000

# --- stub verifiers (authN seam) ------------------------------------------- #


def claims_for(
    tenant_id: str = "acme",
    subject_id: str = "u_alice",
    subject_type: str = "user",
    role: str = "member",
    email: str = "alice@acme.example.com",
    purpose: str = "api-session",
) -> dict[str, Any]:
    """A verified-claims dict in the issue #35 session-token claim shape."""
    return {
        "iss": "urn:agent-orchestrator:sso",
        "sub": subject_id,
        "aud": ["urn:agent-orchestrator:api"],
        "iat": NOW,
        "exp": NOW + 3600,
        "jti": f"session_{subject_id}",
        "purpose": purpose,
        "tenantId": tenant_id,
        "subjectType": subject_type,
        "role": role,
        "email": email,
    }


def accepting_verifier(claims: Optional[dict[str, Any]] = None) -> Callable[..., dict[str, Any]]:
    """A verifier that accepts any non-empty token and returns ``claims``.

    ``expected_tenant`` is honored when provided (raises on mismatch) so the
    cross-tenant pin can be exercised without a real SSO stack.
    """
    verified = claims if claims is not None else claims_for()

    def _verify(token: str, *, expected_tenant: Optional[str] = None, now: Optional[int] = None) -> dict[str, Any]:
        if not token:
            raise ValueError("missing token")
        if expected_tenant is not None and verified.get("tenantId") != expected_tenant:
            raise ValueError("cross-tenant session use")
        return dict(verified)

    return _verify


def rejecting_verifier() -> Callable[..., dict[str, Any]]:
    """A verifier that rejects every token (invalid/expired/revoked...)."""

    def _verify(token: str, **_: object) -> dict[str, Any]:
        raise ValueError("invalid session token")

    return _verify


# --- stub backend (downstream passthrough seam) ----------------------------- #


class StubBackend:
    """Records every forwarded request and answers a canned (status, body)."""

    def __init__(
        self,
        status: int = 200,
        body: Any = None,
        *,
        transport_error: bool = False,
    ) -> None:
        self.status = status
        self.body = body
        self.transport_error = transport_error
        self.calls: list[ForwardedRequest] = []

    def call(self, request: ForwardedRequest) -> tuple[int, Any]:
        self.calls.append(request)
        if self.transport_error:
            raise RuntimeError("backend transport failure (test)")
        return self.status, self.body

    @property
    def last(self) -> Optional[ForwardedRequest]:
        return self.calls[-1] if self.calls else None

    def reset(self) -> None:
        self.calls.clear()


def echo_backend(request: ForwardedRequest) -> tuple[int, dict[str, Any]]:
    """A backend that returns the outbound request as its 200 body."""
    return 200, {
        "method": request.method,
        "backendPath": request.backend_path,
        "headers": dict(request.headers),
        "tenantId": None if request.identity is None else request.identity.tenant_id,
        "subjectId": None if request.identity is None else request.identity.subject_id,
        "roleSnapshot": list(request.role_snapshot),
    }


def make_edge(
    routes: PublicRoutes,
    *,
    verifier: Optional[Callable[..., dict[str, Any]]] = None,
    backend: Optional[Callable[..., tuple[int, Any]]] = None,
) -> PublicEdge:
    """A :class:`PublicEdge` over ``routes`` with the given seams."""
    return PublicEdge(routes, verifier=verifier, backend=backend)
