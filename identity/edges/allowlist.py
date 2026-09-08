"""The public API allowlist boundary - which routes the world may reach.

Allowlist, not denylist (cannibalized from saas-rbac ``frontend-api/src/
proxy.ts``): the platform's backend is internal-only precisely so it can host
routes that must never be publicly callable (internal service-to-service
endpoints, maintenance/admin internals).  A route becomes public **only**
when an operator adds it to this table on purpose - adding an internal route
to the backend never publishes it.

The matcher is fail closed:

- an unknown path is rejected (``unknown_route``);
- a traversal / malformed path is folded into ``unknown_route`` (a client
  cannot distinguish "route exists but traversal blocked" from "route does
  not exist" - saas-rbac folds both into ``unknown_api_route``);
- a known path with a non-allowlisted HTTP method is ``method_not_allowed``.

The allowlist decides **publication only**.  It never decides whether a
caller *may* perform an action - that is the backend's authorization.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from identity.edges.model import EdgeRoute, METHODS
from identity.edges.paths import (
    parse_path_segments,
    parse_template,
)


class AllowlistError(ValueError):
    """The public route table is invalid (fail closed - no ambiguous edge)."""


@dataclass(frozen=True)
class AllowlistDecision:
    """The result of matching one public request against the allowlist.

    ``reason`` is ``None`` when the request is publicly reachable (and carries
    the matched route + path parameters); otherwise it names the fail-closed
    cause - ``"unknown_route"`` (path not allowlisted / traversal) or
    ``"method_not_allowed"`` (path allowlisted, HTTP method not).
    """

    allowed: bool
    route: Optional[EdgeRoute] = None
    params: Optional[dict[str, str]] = None
    reason: Optional[str] = None


class PublicRoutes:
    """An immutable, validated public route allowlist."""

    def __init__(self, routes: Iterable[EdgeRoute]) -> None:
        self._routes: tuple[EdgeRoute, ...] = tuple(routes)
        self._validate()

    # -- construction / validation ------------------------------------------- #

    def _validate(self) -> None:
        if not self._routes:
            raise AllowlistError("public route allowlist cannot be empty")
        seen: set[tuple[str, str]] = set()
        for route in self._routes:
            if route.method not in METHODS:
                raise AllowlistError(
                    f"route {route.route_id!r}: method {route.method!r} is not "
                    f"allowlisted for the proxy"
                )
            api_segments = parse_template(route.api_path)
            if api_segments is None:
                raise AllowlistError(
                    f"route {route.route_id!r}: api path {route.api_path!r} is "
                    f"not a valid template"
                )
            backend_segments = parse_template(route.backend_path)
            if backend_segments is None:
                raise AllowlistError(
                    f"route {route.route_id!r}: backend path "
                    f"{route.backend_path!r} is not a valid template"
                )
            key = (route.method, route.api_path)
            if key in seen:
                raise AllowlistError(
                    f"route {route.route_id!r}: duplicate publication "
                    f"{route.method} {route.api_path}"
                )
            seen.add(key)

    @property
    def routes(self) -> tuple[EdgeRoute, ...]:
        return self._routes

    def route_ids(self) -> tuple[str, ...]:
        return tuple(route.route_id for route in self._routes)

    def is_public_path(self, path: str) -> bool:
        """Whether any allowlisted route matches ``path`` (any method)."""
        return self._match_path(path) is not None

    # -- matching ------------------------------------------------------------ #

    def _match_path(self, path: str):
        """Return the (route, params) list for an exact public path, else None.

        Traversal/malformed paths are folded here (return ``None``) so a
        probing client sees the same rejection as for an unknown route.
        """
        segments = parse_path_segments(path)
        if segments is None:
            return None
        matched: list[tuple[EdgeRoute, dict[str, str]]] = []
        for route in self._routes:
            template = parse_template(route.api_path)
            params = self._match_one(template, segments)
            if params is not None:
                matched.append((route, params))
        return matched or None

    @staticmethod
    def _match_one(template, segments):
        from identity.edges.paths import match_template

        return match_template(template, segments)

    def match(self, method: str, path: str) -> AllowlistDecision:
        """Match a public (method, path) pair against the allowlist.

        Order of checks: exact path match (fail closed on traversal/unknown),
        then the per-route method allowlist.
        """
        matched = self._match_path(path)
        if matched is None:
            return AllowlistDecision(allowed=False, reason="unknown_route")
        for route, params in matched:
            if method == route.method:
                return AllowlistDecision(
                    allowed=True, route=route, params=params
                )
        return AllowlistDecision(allowed=False, reason="method_not_allowed")


# --------------------------------------------------------------------------- #
# Default allowlist (the AC #1 public surface) + YAML parity
# --------------------------------------------------------------------------- #

#: Single source of truth for the shipped default allowlist.  The committed
#: ``config/public-routes.yaml`` mirrors this table (a parity test keeps the
#: operator-editable YAML in lockstep with this code).
DEFAULT_ROUTE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "route_id": "tasks.create",
        "method": "POST",
        "api_path": "/v1/agents/{agentId}/tasks",
        "backend_path": "/v1/agents/{agentId}/tasks",
        "authenticated": True,
        "description": (
            "Create an agent task - the model-gateway dispatch surface "
            "(gateway/proxy issue #16; the #38 REST server is the downstream "
            "consumer).  The backend authorizes agent:run."
        ),
    },
    {
        "route_id": "usage.me",
        "method": "GET",
        "api_path": "/v1/tenants/me/usage",
        "backend_path": "/v1/tenants/{tenantId}/usage",
        "authenticated": True,
        "description": (
            "Current tenant usage (telemetry metering, issue #33).  The "
            "public path never names a tenant - {tenantId} is filled from the "
            "verified session claims."
        ),
    },
    {
        "route_id": "audit.export",
        "method": "GET",
        "api_path": "/v1/tenants/me/audit/export",
        "backend_path": "/v1/tenants/{tenantId}/audit/export",
        "authenticated": True,
        "description": (
            "Tamper-evident audit export (telemetry audit, issue #31).  "
            "Tenant scoping from verified claims."
        ),
    },
    {
        "route_id": "admin.agents.list",
        "method": "GET",
        "api_path": "/v1/admin/agents",
        "backend_path": "/v1/admin/agents",
        "authenticated": True,
        "description": (
            "Admin agent list (control-plane).  The edge forwards the request; "
            "the backend authorizes the admin permission."
        ),
    },
    {
        "route_id": "admin.tenants.create",
        "method": "POST",
        "api_path": "/v1/admin/tenants",
        "backend_path": "/v1/admin/tenants",
        "authenticated": True,
        "description": (
            "Admin tenant create (control-plane onboarding).  The backend "
            "authorizes; the edge never decides who may create a tenant."
        ),
    },
    {
        "route_id": "health.live",
        "method": "GET",
        "api_path": "/healthz",
        "backend_path": "/healthz",
        "authenticated": False,
        "description": (
            "The single unauthenticated liveness probe on the public edge "
            "(saas-rbac /healthz precedent)."
        ),
    },
)


def default_public_routes() -> PublicRoutes:
    """The shipped default allowlist (AC #1 public surface, offline)."""
    return PublicRoutes(route_from_spec(spec) for spec in DEFAULT_ROUTE_SPECS)


def route_from_spec(spec: Mapping[str, Any]) -> EdgeRoute:
    """Build an :class:`EdgeRoute` from a validated spec mapping (YAML/JSON)."""
    route_id = spec.get("route_id")
    method = spec.get("method")
    api_path = spec.get("api_path")
    backend_path = spec.get("backend_path")
    if not isinstance(route_id, str) or not route_id:
        raise AllowlistError("route spec requires a non-empty route_id")
    if not isinstance(method, str) or not method:
        raise AllowlistError(f"route {route_id!r}: requires a method")
    if not isinstance(api_path, str) or not api_path:
        raise AllowlistError(f"route {route_id!r}: requires an api_path")
    if not isinstance(backend_path, str) or not backend_path:
        raise AllowlistError(f"route {route_id!r}: requires a backend_path")
    authenticated = spec.get("authenticated", True)
    if not isinstance(authenticated, bool):
        raise AllowlistError(f"route {route_id!r}: 'authenticated' must be a boolean")
    expected_tenant = spec.get("expected_tenant")
    if expected_tenant is not None and not isinstance(expected_tenant, str):
        raise AllowlistError(
            f"route {route_id!r}: 'expected_tenant' must be a string or null"
        )
    description = spec.get("description", "")
    return EdgeRoute(
        route_id=route_id,
        method=method,
        api_path=api_path,
        backend_path=backend_path,
        authenticated=authenticated,
        description=str(description),
        expected_tenant=expected_tenant,
    )


def load_public_routes_yaml(stream: Any) -> PublicRoutes:
    """Load + validate a declarative public allowlist from a YAML stream.

    Fails closed on malformed YAML, an unknown shape, or a spec that does not
    pass ``route_from_spec``/``PublicRoutes`` validation (duplicates,
    non-allowlisted methods, malformed templates).
    """
    import yaml

    try:
        data = yaml.safe_load(stream)
    except Exception as exc:  # PyYAML parse error
        raise AllowlistError(f"public-routes YAML is not valid: {exc}") from exc
    if not isinstance(data, Mapping):
        raise AllowlistError("public-routes YAML must be a mapping")
    api_version = data.get("apiVersion", "v1")
    if api_version != "v1":
        raise AllowlistError(
            f"public-routes YAML: unsupported apiVersion {api_version!r}"
        )
    raw_routes = data.get("routes")
    if not isinstance(raw_routes, Sequence) or isinstance(raw_routes, (str, bytes)):
        raise AllowlistError("public-routes YAML: 'routes' must be a list")
    specs = list(raw_routes)
    if not specs:
        raise AllowlistError("public-routes YAML: no routes declared")
    try:
        return PublicRoutes(route_from_spec(spec) for spec in specs)
    except (TypeError, AttributeError) as exc:
        raise AllowlistError(f"public-routes YAML: invalid route spec: {exc}") from exc


def default_config_path() -> str:
    """Absolute path of the committed default allowlist YAML."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "config", "public-routes.yaml")
