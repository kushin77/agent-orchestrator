"""AuthN (session verify) + AuthZ (RBAC two-gate) wiring for the API.

Front-door vs. backend split (the merged phase-6 doctrine): this control
plane is the **backend** — it consumes an already-verified session credential
and **authorizes**, never trusting the caller to assert its own tenant or
permissions. Everything fails closed.

AuthN
-----
``authenticate(token, expected_tenant=None)`` delegates to the injected
:class:`SessionVerifier` seam. The verifier (real adapter = merged
``identity/sso`` or ``registry/service`` session verifier) returns a
:class:`VerifiedPrincipal` whose ``tenant_id`` is the mandatory session-tenant
claim. A request that carries no token, an invalid/expired/revoked token, or a
token whose tenant does not match the path tenant is refused before any
handler runs.

AuthZ
-----
Every route declares the ``resource:action`` permission it requires. The
facade calls :meth:`authorize_request`, which composes the two-gate guard in
the only safe order, exactly like ``identity/rbac`` ``guard``:

1. **scope gate** — is the subject in scope for the tenant at all? A subject
   with no binding in the tenant is ``scope_denied`` no matter what role
   strings it would otherwise hold. The path ``tenantId`` (when present) must
   equal the session tenant — there is no cross-tenant fallback.
2. **permission gate** — within that resolved scope, does the union of the
   subject's roles grant the route's permission?

A denial is unconditional (403 with the decision fields; the observability
contract in the rbac README reserves ``event``/``tenantId``/``permission``).
Never a fallback that re-tries in another scope.

The permission vocabulary consumed here is the frozen platform pack (admin
holds ``agent:*``/``org:*``/``budget:*``/``audit:read``/``prompt:read``; owner
holds ``*:*``) plus the documented control-plane additions for registry reads
and approval/outbox governance, which a tenant grants via custom roles or the
owner preset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .errors import (
    cross_tenant,
    invalid_token,
    permission_denied,
    scope_denied,
    unauthorized,
)
from .ports import Authorizer, DecisionView, SessionVerifier, VerifiedPrincipal


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """The concrete principal the facade hands to handlers after authN."""

    subject_id: str
    subject_type: str
    tenant_id: str
    roles: tuple = ()
    claims: Dict[str, Any] = None  # type: ignore[assignment]

    @property
    def actor(self) -> str:
        """Ledger actor string ``kind:id`` (telemetry/ledger vocabulary)."""
        return f"{self.subject_type}:{self.subject_id}"


class NoopSessionVerifier:
    """Offline verifier for internal/system callers (never used on the wire).

    Test scaffolding and the wiring smoke tests pass a pre-built principal in
    directly; this verifier exists so a ``ControlPlane`` can be constructed
    with a verifier that refuses real tokens unless one is wired.
    """

    def verify(self, token: str, expected_tenant: Optional[str] = None) -> AuthenticatedPrincipal:
        raise invalid_token("no session verifier is wired; real tokens are refused")


def verify_token(
    verifier: SessionVerifier,
    token: Optional[str],
    expected_tenant: Optional[str] = None,
) -> AuthenticatedPrincipal:
    """AuthN: turn an optional bearer token into a principal (fail closed).

    A missing token is a 401; verification errors from the seam are re-raised
    as the verifier's own :class:`ApiError`. Cross-tenant use (expected tenant
    != session tenant) is refused here — no cross-tenant fallback.
    """
    if not token:
        raise unauthorized()
    principal = verifier.verify(token, expected_tenant=expected_tenant)
    principal = _coerce(principal)
    if expected_tenant is not None and principal.tenant_id != expected_tenant:
        raise cross_tenant()
    return principal


def _coerce(principal: Any) -> AuthenticatedPrincipal:
    if isinstance(principal, AuthenticatedPrincipal):
        return principal
    # Accept any VerifiedPrincipal-shaped object (real verifier adapters map
    # claims onto one).
    return AuthenticatedPrincipal(
        subject_id=principal.subject_id,
        subject_type=getattr(principal, "subject_type", "user"),
        tenant_id=principal.tenant_id,
        roles=tuple(getattr(principal, "roles", ())),
        claims=dict(getattr(principal, "claims", {})),
    )


def enforce_scope(
    principal: AuthenticatedPrincipal, path_tenant_id: Optional[str]
) -> None:
    """The tenant scope gate: the path tenant must be the session tenant.

    Control-plane management acts inside exactly one tenant — the session's
    own. A request addressed to another tenant's resources is refused at the
    scope gate before any permission is evaluated (no cross-tenant fallback).
    """
    if path_tenant_id is not None and path_tenant_id != principal.tenant_id:
        raise scope_denied(code="out_of_scope")


def authorize_request(
    authorizer: Authorizer,
    principal: AuthenticatedPrincipal,
    tenant_id: str,
    permission: str,
) -> None:
    """AuthZ: run the injected two-gate guard; raise 403 on a denial.

    The seam (real adapter: ``identity/rbac`` ``guard`` at the org node)
    already separates the scope gate from the permission gate; this function
    only maps the outcome onto the API error surface and preserves the
    decision fields for the envelope and the observability denial log.
    """
    decision: DecisionView = authorizer.authorize(
        principal.subject_id,
        tenant_id,
        permission,
        subject_type=principal.subject_type,
    )
    if decision.allowed:
        return
    if decision.reason == "scope":
        raise scope_denied(code=decision.code or "out_of_scope")
    raise permission_denied(
        permission,
        missing=list(decision.missing_permissions) if decision.missing_permissions else None,
    )
