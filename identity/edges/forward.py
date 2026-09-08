"""Build the outbound request - built, never copied (saas-rbac doctrine).

The public edge is a thin front door: it authenticates (authN), matches the
explicit allowlist, and forwards the verified identity downstream - it never
authorizes.  The outbound request is **built**, not **copied**: method, path,
query and body come from the allowlist route + the validated request; headers
are set here, so a client can never smuggle ``authorization``,
``x-tenant-id`` or any other identity-bearing header into the internal call.
Only an explicitly allowlisted trace header is forwarded verbatim, and the
backend treats it as an opaque log field (never as identity).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from identity.edges.model import (
    CLAIM_EMAIL,
    CLAIM_SUBJECT,
    CLAIM_SUBJECT_TYPE,
    CLAIM_TENANT_ID,
    IDENTITY_BACKEND_PARAM,
    METHODS_WITH_BODY,
    TRACE_HEADER,
    EdgeRequest,
    EdgeRoute,
    ForwardedIdentity,
    ForwardedRequest,
)
from identity.edges.paths import backend_params_in

#: Client headers the edge forwards verbatim - the trace header only.  Every
#: other client header is dropped (built, not copied).
ALLOWED_FORWARD_HEADERS: frozenset[str] = frozenset({TRACE_HEADER})


class ForwardError(ValueError):
    """The outbound request could not be built (fail closed)."""


def forward_headers(client_headers: Mapping[str, str]) -> dict[str, str]:
    """The client headers the edge is willing to forward (trace only)."""
    forwarded: dict[str, str] = {}
    if not isinstance(client_headers, Mapping):
        return forwarded
    for key, value in client_headers.items():
        if isinstance(key, str) and key.lower() in ALLOWED_FORWARD_HEADERS:
            if isinstance(value, str) and value:
                forwarded[key.lower()] = value
    return forwarded


def render_backend_path(
    route: EdgeRoute,
    path_params: Mapping[str, str],
    identity: Optional[ForwardedIdentity],
) -> str:
    """Render the downstream path from the allowlist route template.

    ``{name}`` parameters are filled from the matched public path parameters.
    The reserved ``{tenantId}`` parameter is filled from the *verified*
    session claims (never from the client path/headers).  Any other
    unreferenced parameter is a configuration error - fail closed rather than
    forward an unrendered template.
    """
    available: dict[str, str] = dict(path_params or {})
    if identity is not None:
        available.setdefault(IDENTITY_BACKEND_PARAM, identity.tenant_id)
    template = route.backend_path
    for name in backend_params_in(template) or []:
        if name not in available:
            raise ForwardError(
                f"route {route.route_id!r}: backend template references "
                f"{{{{{name}}}}} but no such public path parameter exists and "
                f"it is not the identity-derived {{tenantId}}"
            )
    rendered = template
    for name, value in available.items():
        rendered = rendered.replace("{" + name + "}", value)
    return rendered


def build_forwarded_request(
    request: EdgeRequest,
    route: EdgeRoute,
    path_params: Mapping[str, str],
    identity: Optional[ForwardedIdentity],
    role_snapshot: tuple[str, ...] = (),
) -> ForwardedRequest:
    """Build the outbound request (built, never copied).

    - ``method``/``backend_path`` come from the allowlist route + rendered
      template;
    - ``query`` is forwarded (part of the URL, not identity);
    - ``body`` is forwarded only for body-carrying methods;
    - ``headers`` contain only the allowlisted trace header;
    - the verified identity + role snapshot ride as dedicated fields (the
      offline transport model of the internal IAM-injected identity).
    """
    backend_path = render_backend_path(route, path_params, identity)
    headers = forward_headers(request.headers)
    body = request.body if request.method in METHODS_WITH_BODY else None
    return ForwardedRequest(
        method=request.method,
        backend_path=backend_path,
        query=request.query,
        body=body,
        headers=headers,
        identity=identity,
        role_snapshot=tuple(role_snapshot),
    )


def claims_identity(claims: Mapping[str, Any]) -> ForwardedIdentity:
    """Build the forwarded identity from verified session claims.

    Helper for tests/harnesses that already hold verified claims (mirrors the
    extraction in ``authn._caller_from_claims`` without re-verifying).
    """
    subject_type = claims.get(CLAIM_SUBJECT_TYPE) or "user"
    return ForwardedIdentity(
        tenant_id=str(claims.get(CLAIM_TENANT_ID, "")),
        subject_id=str(claims.get(CLAIM_SUBJECT, "")),
        subject_type=subject_type,
        email=str(claims.get(CLAIM_EMAIL, "")),
    )
