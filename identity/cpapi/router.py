"""Transport-free REST router + standardized response envelope.

This module is the HTTP-handler abstraction the control-plane issue calls for:
a declarative ``Route`` table mapping ``(method, path-template)`` to a handler,
with path-parameter extraction and envelope-standardized responses. It is
transport-free by design — no socket, no framework — so every route is fully
testable offline by calling ``ControlPlane.handle(method, path, ...)``. A real
deployment mounts a thin adapter (asyncio/http.server or a vendor gateway) in
front of the same ``handle`` method; nothing in this subtree opens a port.

Envelope
--------

Every response is one object with a fixed shape (the capital-underwriting /
gateway-proxy middleware envelope pattern):

.. code-block:: json

    {"ok": true,  "status": 200, "requestId": "req_...", "data": {...}, "error": null}
    {"ok": false, "status": 403, "requestId": "req_...", "data": null,
     "error": {"code": "permission_denied", "message": "...", "details": {...}}}

Handlers return ``(status, data_dict)`` or raise :class:`ApiError`; the facade
builds the envelope. A path template ``/v1/agents/{agentId}`` matches literal
segments and captures ``{name}`` placeholders into the params dict passed to
the handler.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .errors import ApiError, invalid_body, not_found

_PATH_PARAM_RE = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")


def compile_template(path_template: str) -> Tuple[str, Tuple[str, ...]]:
    """Compile ``/v1/agents/{agentId}/tasks`` to a regex + param-name list."""
    names: List[str] = []
    pieces: List[str] = []
    for index, segment in enumerate(path_template.split("/")):
        if not segment:
            continue
        match = _PATH_PARAM_RE.fullmatch(segment)
        if match:
            names.append(match.group(1))
            pieces.append(r"([^/]+)")
        else:
            pieces.append(re.escape(segment))
    pattern = "^/" + "/".join(pieces) + "/?$"
    return pattern, tuple(names)


@dataclass(frozen=True)
class Route:
    """One control-plane route.

    ``permission`` is the ``resource:action`` string the authorizer requires
    at the tenant's org node (the RBAC two-gate guard; see ``access.py``).
    ``destructive`` marks a route whose mutation is approval-gated before it
    executes (govctl-style; see ``approvals.py``).
    """

    method: str
    path_template: str
    permission: str
    name: str
    destructive: bool = False
    requires_tenant_scope: bool = True

    def __post_init__(self) -> None:
        if self.method not in ("GET", "POST"):
            raise ValueError(f"unsupported method {self.method!r} on {self.name}")
        if not self.path_template.startswith("/"):
            raise ValueError(f"path template must start with '/': {self.path_template}")
        # Validate the template compiles up front (fail fast at table build).
        compile_template(self.path_template)


@dataclass
class MatchedRoute:
    route: Route
    params: Dict[str, str] = field(default_factory=dict)


class Router:
    """Registers routes and matches ``(method, path)`` → ``(route, params)``.

    First-match-wins; an unknown path is ``None`` (the facade returns 404).
    Methods are matched exactly; ``GET`` vs ``POST`` never alias.
    """

    def __init__(self) -> None:
        self._routes: List[Tuple[str, Tuple[str, ...], Route]] = []

    def register(self, route: Route) -> "Router":
        pattern, names = compile_template(route.path_template)
        self._routes.append((pattern, names, route))
        return self

    def register_all(self, routes: List[Route]) -> "Router":
        for route in routes:
            self.register(route)
        return self

    def match(self, method: str, path: str) -> Optional[MatchedRoute]:
        if not isinstance(path, str) or not path.startswith("/"):
            return None
        for pattern, names, route in self._routes:
            if route.method != method:
                continue
            match = re.match(pattern, path)
            if not match:
                continue
            params = dict(zip(names, match.groups()))
            return MatchedRoute(route=route, params=params)
        return None

    @property
    def routes(self) -> List[Route]:
        return [route for _, _, route in self._routes]

    def names(self) -> List[str]:
        return [route.name for _, _, route in self._routes]


# --- envelope ---------------------------------------------------------------


def ok_envelope(data: Any, request_id: str, status: int = 200) -> Dict[str, Any]:
    """The standardized success envelope."""
    return {
        "ok": True,
        "status": status,
        "requestId": request_id,
        "data": data,
        "error": None,
    }


def error_envelope(
    error: ApiError, request_id: str, *, status: Optional[int] = None
) -> Dict[str, Any]:
    """The standardized error envelope from an :class:`ApiError`."""
    status = status if status is not None else error.status
    return {
        "ok": False,
        "status": status,
        "requestId": request_id,
        "data": None,
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        },
    }


def not_found_envelope(request_id: str, path: str) -> Dict[str, Any]:
    return error_envelope(
        not_found(f"no route for this request: {path}", code="not_found"),
        request_id,
    )
