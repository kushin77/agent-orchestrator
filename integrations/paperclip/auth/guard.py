"""The boundary auth pipeline: one request, two gates, fail closed (issue #412).

This is where the two auth models meet **once**. A request that reaches the
upstream surface across the process boundary is resolved here, in a fixed order
that mirrors the fleet's own ``identity/cpapi`` facade:

1. **AuthN** — read ``Authorization: Bearer <token>``; a missing header is a 401
   ``unauthorized`` and anything that fails full verification is a 401
   ``invalid_token`` / ``token_expired``. An agent token is verified against the
   registry records; a board token against the board session path.
2. **Scope gate** — a token scoped to another company is a 403 ``cross_tenant``.
   (Applied inside the verifier, so the caller cannot re-scope itself.)
3. **AuthZ** — the route's required permission is checked against the principal's
   derived set; a caller known but not allowed is 403 ``permission_denied``,
   never 404.
4. **Run correlation** — a mutating request binds its ``X-Paperclip-Run-Id`` to
   the fleet's ``correlation_id``; a replayed run id is 409 ``replayed_run_id``.

Every refusal is an :class:`AuthError` naming the rule; no refusal carries the
credential it refused (GR-6).

---knowledge---
module_id: integrations.paperclip.auth.guard
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [header, bearer_token, authenticate, authorize, authenticate_headers, guard_request]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from . import board as _board
from . import jwt as _jwt
from . import policy as _policy
from . import registry as _registry
from .model import (
    AUDIENCE,
    ISSUER,
    KIND_AGENT,
    KIND_HUMAN,
    MUTATING_METHODS,
    AuthContext,
    Principal,
    invalid_token,
    permission_denied,
    unauthorized,
    validation_error,
)

AUTHORIZATION_HEADER = "authorization"
RUN_ID_HEADER = "x-paperclip-run-id"

SessionLookup = Callable[[str], bool]


def header(headers: Mapping[str, str], name: str) -> Optional[str]:
    """Case-insensitive header read (HTTP header names are case-insensitive)."""
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def bearer_token(headers: Mapping[str, str]) -> str:
    """Extract the bearer token, or refuse naming the rule (never the value)."""
    value = header(headers, AUTHORIZATION_HEADER)
    if value is None or not str(value).strip():
        raise unauthorized("a Bearer credential is required")
    scheme, _, rest = str(value).partition(" ")
    if scheme.lower() != "bearer" or not rest.strip():
        raise invalid_token("the Authorization header is not a Bearer credential")
    return rest.strip()


def authenticate(
    token: str,
    *,
    root: Path,
    company: str,
    secret: str,
    now: int,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    leeway: int = _jwt.DEFAULT_LEEWAY,
    session_lookup: Optional[SessionLookup] = None,
) -> Principal:
    """Resolve a token to a principal, routing by its (unverified) kind claim."""
    claims = _jwt.peek_claims(token)
    kind = claims.get("kind")
    if kind == KIND_AGENT:
        return _registry.verify_agent_key(
            root,
            token,
            company=company,
            secret=secret,
            now=now,
            issuer=issuer,
            audience=audience,
            leeway=leeway,
        )
    if kind == KIND_HUMAN:
        return _board.verify_board_token(
            token,
            company=company,
            secret=secret,
            now=now,
            issuer=issuer,
            audience=audience,
            leeway=leeway,
            session_lookup=session_lookup,
        )
    raise invalid_token("the token kind is not recognised")


def authorize(principal: Principal, permission: str) -> None:
    """The permission gate: refuse a known caller that lacks the permission."""
    if not _policy.is_permission(permission):
        raise ValueError(f"unknown permission {permission!r} (not in the closed vocabulary)")
    if permission not in principal.permissions:
        raise permission_denied(permission)


def authenticate_headers(
    headers: Mapping[str, str],
    *,
    root: Path,
    company: str,
    secret: str,
    now: int,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    session_lookup: Optional[SessionLookup] = None,
) -> Principal:
    """AuthN + scope gate straight from a header mapping."""
    token = bearer_token(headers)
    return authenticate(
        token,
        root=root,
        company=company,
        secret=secret,
        now=now,
        issuer=issuer,
        audience=audience,
        session_lookup=session_lookup,
    )


def guard_request(
    method: str,
    headers: Mapping[str, str],
    *,
    root: Path,
    company: str,
    secret: str,
    now: int,
    permission: str,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    bridge: Optional[Any] = None,
    correlation_id: Optional[str] = None,
    session_lookup: Optional[SessionLookup] = None,
) -> AuthContext:
    """Run the whole pipeline for one boundary request, or refuse by name.

    ``bridge`` is a :class:`~.runbridge.RunBridge`; it is required only when a
    mutating request actually carries a run id.
    """
    principal = authenticate_headers(
        headers,
        root=root,
        company=company,
        secret=secret,
        now=now,
        issuer=issuer,
        audience=audience,
        session_lookup=session_lookup,
    )
    authorize(principal, permission)

    run = None
    run_id = header(headers, RUN_ID_HEADER)
    if run_id is not None and str(run_id).strip():
        if method.upper() not in MUTATING_METHODS:
            raise validation_error("a run id is only valid on a mutating request")
        if bridge is None:
            raise validation_error("a run bridge is required to bind a run id")
        if correlation_id is not None:
            run = bridge.bind(str(run_id).strip(), correlation_id)
        else:
            run = bridge.bind_from_fleet(str(run_id).strip())
    return AuthContext(principal=principal, run=run, details={"permission": permission})
