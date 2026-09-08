"""Public API + proxy allowlist boundary - edge vocabulary (issue #37).

Pure frozen data types and closed vocabularies for the platform's public
front door (``identity/edges``).  This lane is the **authn never authz**
edge:

- it **authenticates** callers at the public boundary (consuming issue #35
  ``verify_session`` semantics through an injected verifier seam) and
- it **never authorizes**: it decides only whether a route is publicly
  reachable (an explicit allowlist - a *publication/transport* decision) and
  forwards the verified identity plus a role *snapshot* downstream for the
  backend (#38 REST server, composing ``identity/rbac`` issue #12) to
  authorize.

Field names below are consumed, not redefined.  The token claim keys mirror
the session-token claim vocabulary frozen by ``registry/service`` (issue #10)
and ``identity/sso`` (issue #35); subject kinds mirror ``identity/rbac``
``SUBJECT_USER`` / ``SUBJECT_AGENT`` (issue #12); the HTTP/method + traversal
guard semantics mirror the cannibalized ``saas-rbac`` proxy (see README).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Versioning + HTTP method vocabulary
# --------------------------------------------------------------------------- #

#: The one public API version this edge publishes.  The whole public surface
#: is versioned - every allowlisted route lives under ``/v1/...`` except the
#: single unauthenticated liveness probe.
API_VERSION = "v1"

METHOD_GET = "GET"
METHOD_POST = "POST"
METHOD_PUT = "PUT"
METHOD_PATCH = "PATCH"
METHOD_DELETE = "DELETE"

#: Methods the proxy accepts (mirrors saas-rbac PROXY_METHODS).
METHODS: frozenset[str] = frozenset(
    {METHOD_GET, METHOD_POST, METHOD_PUT, METHOD_PATCH, METHOD_DELETE}
)

#: Methods whose request body is forwarded to the backend.
METHODS_WITH_BODY: frozenset[str] = frozenset({METHOD_POST, METHOD_PUT, METHOD_PATCH})

#: The single client header the edge forwards verbatim - purely for log
#: correlation across the boundary.  The backend treats it as an opaque log
#: field and never as identity (saas-rbac trust-boundary doctrine).
TRACE_HEADER = "x-request-id"

# --------------------------------------------------------------------------- #
# Subject kinds (mirror identity/rbac SUBJECT_* - consumed, not redefined)
# --------------------------------------------------------------------------- #

SUBJECT_USER = "user"
SUBJECT_AGENT = "agent"
SUBJECT_TYPES: tuple[str, ...] = (SUBJECT_USER, SUBJECT_AGENT)
DEFAULT_SUBJECT_TYPE = SUBJECT_USER

# --------------------------------------------------------------------------- #
# Session-token claim keys (registry #10 + identity/sso #35 frozen vocabulary)
# --------------------------------------------------------------------------- #

CLAIM_ISSUER = "iss"
CLAIM_SUBJECT = "sub"
CLAIM_AUDIENCE = "aud"
CLAIM_ISSUED_AT = "iat"
CLAIM_EXPIRY = "exp"
CLAIM_JTI = "jti"
CLAIM_PURPOSE = "purpose"
CLAIM_TENANT_ID = "tenantId"
CLAIM_SUBJECT_TYPE = "subjectType"
CLAIM_ROLE = "role"
CLAIM_EMAIL = "email"

#: Reserved backend-template parameter names that the edge fills from the
#: *verified identity* (never from the client request).  ``{tenantId}`` in a
#: backend path is substituted with the authenticated session's tenant claim.
IDENTITY_BACKEND_PARAM = "tenantId"

# --------------------------------------------------------------------------- #
# Edge-origin envelope error codes (closed set - fail closed)
# --------------------------------------------------------------------------- #

#: No downstream call was made: the caller did not authenticate.
CODE_UNAUTHENTICATED = "unauthenticated"
#: No downstream call was made: the request path is not on the public
#: allowlist (unknown routes and traversal/malformed paths are folded here so
#: a client cannot distinguish "route exists but traversal blocked" from
#: "route does not exist" - saas-rbac folds both into unknown_api_route).
CODE_UNKNOWN_ROUTE = "unknown_route"
#: No downstream call was made: the path is allowlisted but the HTTP method
#: is not (method allowlist).
CODE_METHOD_NOT_ALLOWED = "method_not_allowed"
#: No downstream call was made: the backend transport failed.  The reason is
#: never leaked (it describes internal topology - saas-rbac doctrine).
CODE_BACKEND_UNAVAILABLE = "backend_unavailable"

EDGE_ERROR_CODES: frozenset[str] = frozenset(
    {
        CODE_UNAUTHENTICATED,
        CODE_UNKNOWN_ROUTE,
        CODE_METHOD_NOT_ALLOWED,
        CODE_BACKEND_UNAVAILABLE,
    }
)


def new_request_id() -> str:
    """A fresh request id (``req_<hex>``) - stable correlation id offline."""
    return f"req_{uuid.uuid4().hex[:16]}"


@dataclass(frozen=True)
class EdgeRoute:
    """One publicly reachable route on the allowlist (a *publication*).

    A route becomes public **only** when an operator adds it here on purpose
    (allowlist, not denylist - saas-rbac doctrine): adding an internal route
    to the backend never publishes it.

    ``api_path`` is the versioned public path (a template whose ``{name}``
    segments are matched from the request path).  ``backend_path`` is the
    downstream path the request is forwarded to; it may reference the matched
    path parameters and, for a tenant-scoped read, the reserved
    ``{tenantId}`` parameter which is filled from the *verified* session
    claims - never from the client.
    """

    route_id: str
    method: str  # one HTTP method; use separate routes for extra methods
    api_path: str  # e.g. "/v1/agents/{agentId}/tasks"
    backend_path: str  # e.g. "/v1/agents/{agentId}/tasks"
    authenticated: bool = True
    description: str = ""
    #: Optional tenant pin for authN at the edge (issue #35 no-cross-tenant
    #: use).  ``None`` = the edge verifies the token without a tenant pin; the
    #: downstream resolves tenant scoping with the claims tenant.  Set only
    #: for routes the edge may pin (never inferred from client input).
    expected_tenant: Optional[str] = None

    @property
    def methods(self) -> tuple[str, ...]:
        return (self.method,)


@dataclass(frozen=True)
class EdgeRequest:
    """An inbound request at the public boundary (parsed, offline).

    ``path`` is the raw public request path (may contain traversal attempts -
    the allowlist guard rejects them before any backend call).
    """

    method: str
    path: str
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None
    query: str = ""


@dataclass(frozen=True)
class ForwardedIdentity:
    """Who the caller is, as the *edge* sees them (authN only).

    Derived strictly from the verified session claims - never from client
    headers or path.  This is what the edge vouches for downstream; it is
    context, not a grant.
    """

    tenant_id: str
    subject_id: str
    subject_type: str = DEFAULT_SUBJECT_TYPE
    email: str = ""


@dataclass(frozen=True)
class ForwardedRequest:
    """The outbound request the edge sends downstream.

    Built, never copied (saas-rbac): method/path/body come from the allowlist
    route + validated request; headers are *not* forwarded from the client
    except the allowlisted trace header, so a client can never smuggle
    identity headers into the internal call.  ``identity`` and
    ``role_snapshot`` ride as separate fields (the offline transport model of
    the internal IAM-injected identity in production).
    """

    method: str
    backend_path: str
    query: str = ""
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    identity: Optional[ForwardedIdentity] = None
    #: The session role snapshot forwarded as *context for audit*, never as a
    #: grant - the downstream re-resolves live bindings (identity/rbac #12).
    role_snapshot: tuple[str, ...] = ()

    @property
    def authenticated(self) -> bool:
        return self.identity is not None


@dataclass(frozen=True)
class EdgeResponse:
    """A versioned, envelope-standardized public response.

    ``apiVersion`` + ``requestId`` + ``status`` are always present.  When the
    edge called the backend, ``body`` carries the downstream body **verbatim**
    and the downstream ``status`` is copied untouched - a backend 403 (authz
    denial) stays a 403 and is never turned into a 200 (saas-rbac doctrine).
    ``error`` is present only for edge-origin rejections (no downstream call)
    and carries a closed machine code.
    """

    status: int
    api_version: str = API_VERSION
    request_id: str = ""
    body: Any = None
    error_code: Optional[str] = None
    error_message: str = ""

    @property
    def ok(self) -> bool:
        return self.status < 400

    @property
    def edge_origin(self) -> bool:
        return self.error_code is not None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the uniform public envelope.

        .. code-block:: json

            { "apiVersion": "v1", "requestId": "req_...",
              "status": 200, "body": <downstream body verbatim> }

            { "apiVersion": "v1", "requestId": "req_...",
              "status": 401,
              "error": { "code": "unauthenticated", "message": "..." } }
        """
        payload: dict[str, Any] = {
            "apiVersion": self.api_version,
            "requestId": self.request_id,
            "status": self.status,
        }
        if self.edge_origin:
            payload["error"] = {
                "code": self.error_code,
                "message": self.error_message,
            }
        else:
            payload["body"] = self.body
        return payload
