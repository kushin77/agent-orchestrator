"""PublicEdge - the authn-never-authz public front door (issue #37).

The outermost edge of the platform.  It does exactly three things, in order:

1. **Allowlist** - decide whether the request path+method is *publicly
   reachable* (an explicit allowlist, fail closed; unknown routes and
   traversal attempts are rejected before any backend call).  This is a
   publication/transport decision, not authorization.
2. **Authenticate** - verify the caller's session token through the injected
   verifier seam (issue #35 ``verify_session`` semantics).  Missing/invalid
   tokens are rejected (401).  The edge never reimplements token checks.
3. **Forward** - build (never copy) the outbound request carrying the
   verified identity + role snapshot downstream, and pass the backend's
   status + body back untouched.

It **never authorizes**: it contains no scope resolution and no permission
check.  Whether the caller *may* perform the requested action is decided by
the backend (#38 REST server composing ``identity/rbac`` issue #12) after
scope resolution - the front door is not trusted for authorization by design
(authn never authz; saas-rbac ``docs/ARCHITECTURE.md`` split).

The backend is an injected, duck-typed passthrough seam (offline-testable):
``backend.call(ForwardedRequest) -> (status, body)``.  The real downstream
REST server arrives in issue #38; this lane models the boundary only.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Protocol

from identity.edges import allowlist as allowlist_mod
from identity.edges import authn as authn_mod
from identity.edges import envelope as envelope_mod
from identity.edges import forward as forward_mod
from identity.edges.model import (
    EdgeRequest,
    EdgeResponse,
    EdgeRoute,
    ForwardedRequest,
    new_request_id,
)

#: The downstream passthrough seam (duck-typed; the #38 REST server is the
#: real consumer).  Returns ``(status, body)``; raises on transport failure.
BackendCall = Callable[[ForwardedRequest], tuple[int, Any]]


class Backend(Protocol):
    def call(self, request: ForwardedRequest) -> tuple[int, Any]: ...


class PublicEdge:
    """The public API + proxy allowlist boundary."""

    def __init__(
        self,
        routes: allowlist_mod.PublicRoutes,
        *,
        verifier: Optional[authn_mod.Verifier] = None,
        backend: Optional[BackendCall] = None,
    ) -> None:
        self.routes = routes
        self.verifier = verifier
        self.backend = backend

    # -- public surface ------------------------------------------------------ #

    def handle(self, request: EdgeRequest, *, now: Optional[int] = None) -> EdgeResponse:
        """Handle one public request end to end (authN + allowlist + forward).

        Fail-closed ordering:

        1. allowlist match (unknown/traversal route -> 404; wrong method ->
           405) - no backend call;
        2. authentication when the matched route requires it (401 on a
           missing/invalid token) - no backend call;
        3. build the outbound request (built, never copied);
        4. call the backend seam and pass its status + body back untouched.
        """
        request_id = new_request_id()

        # 1. Allowlist (publication decision - never authorization).
        decision = self.routes.match(request.method, request.path)
        if not decision.allowed:
            code = (
                envelope_mod.CODE_UNKNOWN_ROUTE
                if decision.reason == "unknown_route"
                else envelope_mod.CODE_METHOD_NOT_ALLOWED
            )
            return envelope_mod.edge_rejection(
                code, request_id=request_id
            )
        route: EdgeRoute = decision.route
        path_params: dict[str, str] = decision.params or {}

        # 2. Authentication (authN at the edge; no authz here).
        identity = None
        role_snapshot: tuple[str, ...] = ()
        if route.authenticated:
            token = authn_mod.bearer_token(request.headers)
            result = authn_mod.verify_caller(
                self.verifier,
                token,
                expected_tenant=route.expected_tenant,
                now=now,
            )
            if not result.ok:
                return envelope_mod.edge_rejection(
                    result.code or envelope_mod.CODE_UNAUTHENTICATED,
                    request_id=request_id,
                    message=result.message,
                )
            identity = result.identity
            role_snapshot = result.role_snapshot

        # 3. Build the outbound request (built, never copied).
        forwarded = forward_mod.build_forwarded_request(
            request,
            route,
            path_params,
            identity,
            role_snapshot,
        )

        # 4. Downstream passthrough - status + body untouched.
        if self.backend is None:
            return envelope_mod.edge_rejection(
                envelope_mod.CODE_BACKEND_UNAVAILABLE, request_id=request_id
            )
        try:
            downstream_status, downstream_body = self.backend(forwarded)
        except Exception:
            # Transport failure - never leak the reason (internal topology).
            return envelope_mod.edge_rejection(
                envelope_mod.CODE_BACKEND_UNAVAILABLE, request_id=request_id
            )
        return envelope_mod.passthrough(
            downstream_status, downstream_body, request_id=request_id
        )

    # -- helpers ------------------------------------------------------------- #

    def handle_request(
        self,
        method: str,
        path: str,
        headers: Optional[Mapping[str, str]] = None,
        *,
        body: Any = None,
        query: str = "",
        now: Optional[int] = None,
    ) -> EdgeResponse:
        """Convenience wrapper: build an :class:`EdgeRequest` and handle it."""
        return self.handle(
            EdgeRequest(
                method=method,
                path=path,
                headers=dict(headers or {}),
                body=body,
                query=query,
            ),
            now=now,
        )

    def is_public_path(self, path: str) -> bool:
        """Whether ``path`` is on the public allowlist (publication check)."""
        return self.routes.is_public_path(path)


def default_edge(
    *,
    verifier: Optional[authn_mod.Verifier] = None,
    backend: Optional[BackendCall] = None,
) -> PublicEdge:
    """A :class:`PublicEdge` with the shipped default allowlist."""
    return PublicEdge(
        allowlist_mod.default_public_routes(),
        verifier=verifier,
        backend=backend,
    )
