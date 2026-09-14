"""portal.server.app — console route table + request pipeline (offline).

Transport-free: :meth:`ConsoleApplication.handle` takes a method/path/query/
body/cookie-map and returns a :class:`Response`. ``httpd.py`` binds it to
``http.server``; tests drive it directly (no sockets). Every API response
uses the control-plane envelope ``{ok, status, requestId, data, error}``
(identity/cpapi shape) and every mutation appends to the tenant audit chain.

Front door: the console has **no login of its own** — the shared-frontend OS
auth gate is the only sign-in surface, and an unauthenticated document request
is redirected there (``/auth/login``). The session pipeline mirrors the control
plane (issue #38): authN first (a verified auth-gate RS256 ``os-session-token``
via ``sso``), then the scope gate, then the permission gate (issue #12 role
vocabulary via ``authz``).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from portal.server import catalog as catalog_mod
from portal.server.auditlog import AuditLedger
from portal.server.authz import Authorizer, Principal
from portal.server.controls import (
    ControlCatalog,
    PolicyEnforcer,
    PolicyStateStore,
    build_control_policy_map,
)
from portal.server.finops import FinOpsReports
from portal.server.fleet import FleetProjection
from portal.server.sso import AUTH_GATE_LOGIN_PATH, ConsoleSso, SESSION_COOKIE
from portal.server.state import Approval, ConsoleState, seed_state

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".md": "text/markdown; charset=utf-8",
}


class ApiError(Exception):
    """An HTTP-addressable console error (envelope ``error.code``)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class Response:
    status: int = 200
    headers: list[tuple[str, str]] = field(default_factory=list)
    payload: Any = None
    is_json: bool = True
    content_type: Optional[str] = None

    def as_bytes(self) -> bytes:
        if self.is_json:
            return json.dumps(self.payload).encode("utf-8")
        if isinstance(self.payload, str):
            return self.payload.encode("utf-8")
        return self.payload if isinstance(self.payload, bytes) else b""


@dataclass
class StreamResponse:
    """A server-sent-events response: an iterator of formatted frames.

    Deliberately not a :class:`Response`: it carries no ``Content-Length`` and
    is written incrementally by the transport (``httpd``) rather than buffered
    into one body. The fleet push channel (``/api/fleet/stream``) is its only
    producer.
    """

    status: int = 200
    headers: list[tuple[str, str]] = field(default_factory=list)
    content_type: str = "text/event-stream; charset=utf-8"
    frames: Iterator[str] = field(default_factory=lambda: iter(()))


class ConsoleApplication:
    """The offline console backend (route table + request pipeline)."""

    def __init__(
        self,
        *,
        repo_root: Path,
        static_dir: Optional[Path] = None,
        state: Optional[ConsoleState] = None,
        sso: Optional[ConsoleSso] = None,
        root_admin_emails: Optional[tuple[str, ...]] = None,
        allowlist_only: bool = False,
        fleet_projection: Optional[FleetProjection] = None,
        finops_reports: Optional[FinOpsReports] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.static_dir = Path(static_dir) if static_dir else (
            self.repo_root / "portal" / "static"
        )
        self.state = (
            state if state is not None else seed_state(repo_root=self.repo_root)
        )
        self.sso = sso if sso is not None else ConsoleSso(
            repo_root=self.repo_root,
            root_admin_emails=root_admin_emails,
            allowlist_only=allowlist_only,
        )
        self.authorizer = Authorizer()
        self.catalog = ControlCatalog.build(repo_root=self.repo_root)
        self.control_policy_map = build_control_policy_map(self.catalog)
        self.policy_store = PolicyStateStore(self.catalog, self.state.tenant_ids())
        self.enforcer = PolicyEnforcer(self.policy_store, self.catalog)
        # The fleet projection surface (issue #331) — feature-flag-gated OFF.
        self.fleet = (
            fleet_projection
            if fleet_projection is not None
            else FleetProjection(repo_root=self.repo_root)
        )
        # The FinOps single-pane surface (issue #341) — feature-flag-gated OFF.
        self.finops = (
            finops_reports
            if finops_reports is not None
            else FinOpsReports(repo_root=self.repo_root)
        )

    # -- request pipeline ---------------------------------------------------
    def handle(
        self,
        method: str,
        path: str,
        query: Optional[dict[str, str]] = None,
        body: Optional[dict[str, Any]] = None,
        cookies: Optional[dict[str, str]] = None,
        now_iso: str = "",
    ) -> Response | StreamResponse:
        path = (path or "/").split("?", 1)[0]
        query = query or {}
        cookies = cookies or {}
        body = body or {}
        try:
            if path == "/" or path == "/index.html":
                return self._index(cookies)
            if self._is_static(path):
                return self._serve_static(path)
            if path.startswith("/api/"):
                return self._route_api(
                    method, path[len("/api/"):], query, body, cookies, now_iso
                )
            raise ApiError(404, "not_found", f"no such route: {method} {path}")
        except ApiError as exc:
            return Response(
                status=exc.status,
                is_json=True,
                payload={
                    "ok": False,
                    "status": exc.status,
                    "requestId": _request_id(),
                    "data": None,
                    "error": {"code": exc.code, "message": exc.message},
                },
            )
        except Exception as exc:  # noqa: BLE001 - fail closed with a 500
            return Response(
                status=500,
                is_json=True,
                payload={
                    "ok": False,
                    "status": 500,
                    "requestId": _request_id(),
                    "data": None,
                    "error": {"code": "internal", "message": str(exc)},
                },
            )

    # -- index / static -----------------------------------------------------
    def _index(self, cookies: dict[str, str]) -> Response:
        """The front door: no local login — unauthenticated goes to the gate.

        A verified auth-gate ``os-session-token`` opens the shell; anything
        else (absent, forged, wrong-purpose or expired) is redirected to the
        OS auth gate, the platform's only sign-in surface.
        """
        token = cookies.get(SESSION_COOKIE)
        if token:
            try:
                self.sso.verify(token)
                return Response(
                    status=302,
                    is_json=False,
                    payload="",
                    headers=[("Location", "/views/shell.html")],
                )
            except Exception:  # noqa: BLE001 - a refused session reaches the gate
                pass
        return Response(
            status=302, is_json=False, payload="",
            headers=[("Location", AUTH_GATE_LOGIN_PATH)],
        )

    def _is_static(self, path: str) -> bool:
        segments = path.strip("/").split("/")
        return bool(segments) and segments[0] in {
            "views", "css", "js", "design-tokens", "assets", "favicon.svg",
        }

    def _serve_static(self, path: str) -> Response:
        relative = path.strip("/")
        target = (self.static_dir / relative).resolve()
        static_root = self.static_dir.resolve()
        if not str(target).startswith(str(static_root)) or not target.is_file():
            raise ApiError(404, "not_found", f"static asset not found: {path}")
        suffix = target.suffix.lower()
        content_type = _CONTENT_TYPES.get(suffix, "application/octet-stream")
        return Response(
            status=200,
            is_json=False,
            payload=target.read_bytes(),
            content_type=content_type,
        )

    # -- api routing --------------------------------------------------------
    def _route_api(
        self,
        method: str,
        route: str,
        query: dict[str, str],
        body: dict[str, Any],
        cookies: dict[str, str],
        now_iso: str,
    ) -> Response | StreamResponse:
        parts = [part for part in route.split("/") if part]
        if not parts:
            raise ApiError(404, "not_found", "empty api route")

        # public console endpoints: health only — the console issues no
        # credential of its own, so it has no login/relay/JWKS surface
        if parts == ["healthz"] and method == "GET":
            return self._ok({"status": "ok", "service": "portal-console"})

        # The fleet projection surface ships feature-flag-gated OFF (GR-5), and
        # the gate is checked BEFORE authN so an unpromoted surface is invisible
        # rather than distinguishable by an authentication probe.
        if parts[0] == "fleet" and not self.fleet.enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the fleet projection surface is feature-flag-gated OFF "
                "(infra/feature-flags/registry.yaml surfaces.fleet_projection)",
            )

        # The FinOps single-pane surface ships the same way (GR-5), also before
        # authN: an unpromoted surface must be invisible, not merely protected.
        if parts[0] == "finops" and not self.finops.enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the FinOps reports surface is feature-flag-gated OFF "
                "(infra/feature-flags/registry.yaml surfaces.finops_reports)",
            )

        # authenticated surface
        principal, claims = self._require_session(cookies)
        try:
            if parts[0] == "fleet":
                return self._route_fleet(parts, method, query)
            if parts[0] == "finops":
                return self._route_finops(parts, principal, method, query)
            if parts[:2] == ["console", "logout"] and method == "POST":
                return self._logout(cookies, now_iso)
            if parts[:2] == ["console", "me"] and method == "GET":
                return self._me(principal)
            if parts == ["tenants"] and method == "GET":
                return self._list_tenants(principal)
            if parts and parts[0] == "tenants":
                return self._route_tenant(
                    parts[1:], principal, method, query, body, now_iso
                )
            raise ApiError(404, "not_found", f"no such api route: {route}")
        except ApiError:
            raise
        except KeyError as exc:
            raise ApiError(404, "not_found", f"unknown entity: {exc}") from exc

    # -- fleet projection (issue #331) --------------------------------------
    #: The default ``slog.jsonl`` tail size when ``?limit=`` is absent, matching
    #: the dashboard's own default so the two surfaces agree.
    FLEET_EVENTS_DEFAULT_LIMIT = 8
    #: The largest history window a client may request in one call.
    FLEET_EVENTS_MAX_LIMIT = 500

    def _route_fleet(
        self, parts: list[str], method: str, query: dict[str, str]
    ) -> Response | StreamResponse:
        """The read-only web single-pane-of-glass (issue #331).

        Every read is delegated to ``fleet/console.py`` through the projection,
        so the browser receives exactly the dashboard's projection. The surface
        is GET-only and, when the feature flag is off, never reaches here.
        """
        if method != "GET":
            raise ApiError(405, "method_not_allowed", "the fleet surface is GET only")
        surface = parts[1:]
        if surface == ["snapshot"]:
            return self._ok(self.fleet.snapshot())
        if surface == ["events"]:
            return self._ok(self.fleet.events(self._fleet_events_limit(query)))
        if surface == ["stream"]:
            return StreamResponse(frames=self.fleet.stream())
        raise ApiError(404, "not_found", f"no such fleet surface: {'/'.join(surface)}")

    def _fleet_events_limit(self, query: dict[str, str]) -> int:
        raw = (query.get("limit") or "").strip()
        if not raw:
            return self.FLEET_EVENTS_DEFAULT_LIMIT
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            raise ApiError(400, "invalid_request", "limit must be an integer") from None
        return max(1, min(limit, self.FLEET_EVENTS_MAX_LIMIT))

    # -- finops single-pane (issue #341) ------------------------------------
    def _route_finops(
        self,
        parts: list[str],
        principal: Principal,
        method: str,
        query: dict[str, str],
    ) -> Response:
        """The FinOps single-pane reports (issue #341).

        GET-only. Every figure is delegated to the metering/budgets lanes
        through ``FinOpsReports`` — this route owns authorization and transport
        shape only, and never restates a cost, a threshold or a verdict. Like
        every other tenant surface it is scoped: a principal only ever sees the
        tenants it may read (``budget:read``).
        """
        if method != "GET":
            raise ApiError(405, "method_not_allowed", "the finops surface is GET only")
        surface = parts[1:]
        if surface == ["overview"]:
            return self._ok(self.finops.overview(self._finops_readable(principal)))
        if surface == ["report"]:
            tenant_id = (query.get("tenant") or "").strip()
            if not tenant_id:
                raise ApiError(400, "invalid_request", "tenant is required")
            self._require_finops_tenant(tenant_id)
            self._require(principal, tenant_id, "budget:read")
            return self._ok(self.finops.report(tenant_id))
        if surface == ["alerts"]:
            tenant_id = (query.get("tenant") or "").strip()
            if tenant_id:
                self._require_finops_tenant(tenant_id)
                self._require(principal, tenant_id, "budget:read")
                return self._ok(self.finops.alerts_report([tenant_id]))
            return self._ok(self.finops.alerts_report(self._finops_readable(principal)))
        raise ApiError(404, "not_found", f"no such finops surface: {'/'.join(surface)}")

    def _finops_readable(self, principal: Principal) -> list[str]:
        """The FinOps tenants a principal may read (scope gate + ``budget:read``)."""
        return [
            tenant
            for tenant in self.authorizer.scope_tenants(
                principal, self.finops.tenant_ids()
            )
            if self.authorizer.allow(principal, tenant, "budget:read")
        ]

    def _require_finops_tenant(self, tenant_id: str) -> None:
        """Fail closed on a tenant the live stores do not know (no probing)."""
        if not self.finops.known_tenant(tenant_id):
            raise ApiError(404, "unknown_tenant", f"no such tenant {tenant_id!r}")

    # -- session ------------------------------------------------------------
    def _require_session(self, cookies: dict[str, str]) -> tuple[Principal, dict[str, Any]]:
        """Establish the console principal from a verified auth-gate session.

        The session exists **only** if the ``os-session-token`` the OS shell
        handed this module verifies (RS256, published JWKS, purpose-checked);
        anything else fails closed with a 401. Identity is the token subject and
        the role comes from the local ROOT_ADMIN allowlist plus the org
        directory — never from the token's own ``role`` claim.
        """
        token = cookies.get(SESSION_COOKIE)
        if not token:
            raise ApiError(401, "unauthorized", "missing console session token")
        try:
            claims = self.sso.verify(token)
            identity = self.sso.identity_from_claims(claims)
        except Exception as exc:  # noqa: BLE001 - invalid token is a denial
            raise ApiError(401, "unauthorized", f"invalid console session: {exc}") from exc
        bindings = [
            (binding.tenant_id, binding.role)
            for binding in self.state.roles_for(identity.email)
        ]
        principal = Principal(
            email=identity.email,
            role=identity.role,
            super_admin=identity.super_admin,
            bindings=bindings,
        )
        return principal, claims

    def _logout(self, cookies: dict[str, str], now_iso: str) -> Response:
        token = cookies.get(SESSION_COOKIE, "")
        try:
            claims = self.sso.verify(token)
        except Exception:  # noqa: BLE001 - nothing to revoke, still clear
            claims = {}
        email = str(claims.get("email") or claims.get("sub") or "unknown")
        tenant_id = str(claims.get("tenantId") or "")
        if claims.get("jti"):
            self.sso.revoke(str(claims["jti"]))
        if tenant_id:
            self._audit(
                tenant_id, f"user:{email}", "console.logout",
                resource=f"tenant:{tenant_id}", detail="console logout",
                now_iso=now_iso,
            )
        return Response(
            status=200,
            is_json=True,
            headers=[
                ("Set-Cookie",
                 f"{SESSION_COOKIE}=; HttpOnly; Path=/; SameSite=Strict; Max-Age=0")
            ],
            payload={
                "ok": True, "status": 200, "requestId": _request_id(),
                "data": {"loggedOut": True}, "error": None,
            },
        )

    def _me(self, principal: Principal) -> Response:
        return self._ok(
            {
                "email": principal.email,
                "role": principal.role,
                "superAdmin": principal.super_admin,
                "scopedTenants": self.authorizer.scope_tenants(
                    principal, self.state.tenant_ids()
                ),
                "bindings": [
                    {"tenantId": tenant, "role": role} for tenant, role in principal.bindings
                ],
            }
        )

    # -- tenant routes ------------------------------------------------------
    def _route_tenant(
        self,
        parts: list[str],
        principal: Principal,
        method: str,
        query: dict[str, str],
        body: dict[str, Any],
        now_iso: str,
    ) -> Response:
        if not parts:
            raise ApiError(404, "not_found", "tenant id required")
        tenant_id = parts[0]
        self._require_tenant(tenant_id)
        tail = parts[1:]
        if not tail:
            if method == "GET":
                self._require(principal, tenant_id, "org:read")
                return self._ok(catalog_mod.tenant_overview(self.state, tenant_id))
            raise ApiError(405, "method_not_allowed", "tenant root is GET only")

        surface = tail[0]
        if surface == "overview":
            self._require(principal, tenant_id, "org:read")
            return self._ok(catalog_mod.tenant_overview(self.state, tenant_id))
        if surface == "agents":
            return self._route_agents(
                tail[1:], principal, tenant_id, method, body, now_iso
            )
        if surface == "personas":
            self._require(principal, tenant_id, "prompt:read")
            return self._ok({"tenantId": tenant_id, "personas": catalog_mod.personas(self.state)})
        if surface == "prompts":
            self._require(principal, tenant_id, "prompt:read")
            return self._ok({"tenantId": tenant_id, "prompts": catalog_mod.prompts(self.state)})
        if surface == "controls":
            return self._route_controls(
                tail[1:], principal, tenant_id, method, body, now_iso
            )
        if surface == "budgets":
            return self._route_budgets(
                tail[1:], principal, tenant_id, method, body, now_iso
            )
        if surface == "usage":
            self._require(principal, tenant_id, "budget:read")
            return self._ok(catalog_mod.usage(self.state, tenant_id))
        if surface == "audit":
            return self._route_audit(
                tail[1:], principal, tenant_id, method, now_iso
            )
        if surface == "approvals":
            return self._route_approvals(
                tail[1:], principal, tenant_id, method, body, now_iso
            )
        if surface == "policy-check" and method == "POST":
            self._require(principal, tenant_id, "policy:read")
            action = str(body.get("action") or "")
            if not action:
                raise ApiError(400, "invalid_request", "action is required")
            return self._ok(
                {"decision": self.enforcer.evaluate(tenant_id, action)}
            )
        raise ApiError(404, "not_found", f"no such tenant surface: {surface}")

    def _route_agents(
        self,
        parts: list[str],
        principal: Principal,
        tenant_id: str,
        method: str,
        body: dict[str, Any],
        now_iso: str,
    ) -> Response:
        if not parts:
            self._require(principal, tenant_id, "agent:read")
            return self._ok(
                {"tenantId": tenant_id, "teams": catalog_mod.agents_tree(self.state, tenant_id)}
            )
        agent_id = parts[0]
        agent = self._require_agent(tenant_id, agent_id)
        if len(parts) == 1:
            self._require(principal, tenant_id, "agent:read")
            return self._ok(self._agent_json(agent))
        action = parts[1]
        if method != "POST":
            raise ApiError(405, "method_not_allowed", f"{action} is POST only")
        self._require(principal, tenant_id, "agent:write")
        if action == "activate":
            if agent.status not in ("registered", "paused"):
                raise ApiError(409, "bad_transition",
                               f"cannot activate agent in state {agent.status!r}")
            agent.status = "active"
            self._audit(tenant_id, f"user:{principal.email}", "agent.activate",
                        resource=f"agent:{agent_id}", now_iso=now_iso)
            return self._ok(self._agent_json(agent))
        if action == "pause":
            if agent.status != "active":
                raise ApiError(409, "bad_transition",
                               f"cannot pause agent in state {agent.status!r}")
            agent.status = "paused"
            self._audit(tenant_id, f"user:{principal.email}", "agent.pause",
                        resource=f"agent:{agent_id}", now_iso=now_iso)
            return self._ok(self._agent_json(agent))
        if action == "retire":
            if agent.status == "retired":
                raise ApiError(409, "bad_transition", "agent is already retired")
            pending = [
                approval for approval in self.state.approvals_for(tenant_id)
                if approval.resource == f"agent:{agent_id}" and approval.status == "pending"
            ]
            if pending:
                raise ApiError(409, "approval_pending",
                               f"agent {agent_id!r} already has a pending retire approval")
            approval_id = f"ap_{uuid.uuid4().hex[:8]}"
            self.state.approvals.append(
                Approval(
                    id=approval_id, tenant_id=tenant_id, action="agent.retire",
                    resource=f"agent:{agent_id}",
                    requested_by=f"user:{principal.email}",
                    reason=f"retire {agent_id}", status="pending",
                )
            )
            self._audit(tenant_id, f"user:{principal.email}", "approval.required",
                        resource=f"agent:{agent_id}",
                        detail=f"retire approval {approval_id} requested",
                        now_iso=now_iso)
            return Response(
                status=202,
                is_json=True,
                payload={
                    "ok": True, "status": 202, "requestId": _request_id(),
                    "data": {
                        "approvalId": approval_id, "status": "approval_required",
                        "message": "agent retire is approval-gated",
                    },
                    "error": None,
                },
            )
        raise ApiError(404, "not_found", f"no such agent action: {action}")

    def _route_controls(
        self,
        parts: list[str],
        principal: Principal,
        tenant_id: str,
        method: str,
        body: dict[str, Any],
        now_iso: str,
    ) -> Response:
        if not parts and method == "GET":
            self._require(principal, tenant_id, "policy:read")
            return self._ok({
                "tenantId": tenant_id,
                "controls": self.policy_store.controls_for_tenant(tenant_id, self.catalog),
                "policyMap": self.control_policy_map,
            })
        if len(parts) == 1 and method == "POST":
            control_id = parts[0]
            self._require(principal, tenant_id, "policy:manage")
            enabled = bool(body.get("enabled"))
            try:
                control = self.policy_store.set_control(tenant_id, control_id, enabled)
            except KeyError as exc:
                raise ApiError(404, "unknown_control", str(exc)) from exc
            except ValueError as exc:
                raise ApiError(400, "invalid_control", str(exc)) from exc
            self._audit(
                tenant_id, f"user:{principal.email}", "control.toggle",
                resource=f"control:{control_id}",
                detail=f"enabled={enabled} (mode={control.mode})", now_iso=now_iso,
            )
            decisions = [
                self.enforcer.evaluate(tenant_id, action)
                for action in control.gated_actions
            ]
            return self._ok({
                "control": {**control.as_json(), "enabled": enabled},
                "gatedDecisions": decisions,
            })
        raise ApiError(404, "not_found", "controls surface is GET all / POST one")

    def _route_budgets(
        self,
        parts: list[str],
        principal: Principal,
        tenant_id: str,
        method: str,
        body: dict[str, Any],
        now_iso: str,
    ) -> Response:
        if not parts and method == "GET":
            self._require(principal, tenant_id, "budget:read")
            return self._ok(catalog_mod.budgets(self.state, tenant_id))
        if parts and parts[0] in ("pause", "resume") and method == "POST":
            self._require(principal, tenant_id, "budget:manage")
            tenant = self.state.tenants[tenant_id]
            target = parts[0] == "pause"
            if parts[0] == "pause" and tenant.subscription_status == "trial":
                pending = [
                    approval for approval in self.state.approvals_for(tenant_id)
                    if approval.action == "tenant.pause" and approval.status == "pending"
                ]
                if pending:
                    raise ApiError(409, "approval_pending",
                                   "tenant already has a pending pause approval")
                approval_id = f"ap_{uuid.uuid4().hex[:8]}"
                self.state.approvals.append(
                    Approval(
                        id=approval_id, tenant_id=tenant_id, action="tenant.pause",
                        resource=f"tenant:{tenant_id}",
                        requested_by=f"user:{principal.email}",
                        reason="trial hold", status="pending",
                    )
                )
                self._audit(tenant_id, f"user:{principal.email}", "approval.required",
                            resource=f"tenant:{tenant_id}",
                            detail=f"pause approval {approval_id} requested",
                            now_iso=now_iso)
                return Response(
                    status=202, is_json=True,
                    payload={
                        "ok": True, "status": 202, "requestId": _request_id(),
                        "data": {"approvalId": approval_id, "status": "approval_required"},
                        "error": None,
                    },
                )
            tenant.paused = target
            self._audit(tenant_id, f"user:{principal.email}",
                        "tenant.pause" if target else "tenant.resume",
                        resource=f"tenant:{tenant_id}", now_iso=now_iso)
            return self._ok({"tenantId": tenant_id, "paused": tenant.paused})
        raise ApiError(404, "not_found", "budgets surface is GET / pause / resume")

    def _route_audit(
        self,
        parts: list[str],
        principal: Principal,
        tenant_id: str,
        method: str,
        now_iso: str,
    ) -> Response:
        if not parts and method == "GET":
            self._require(principal, tenant_id, "audit:read")
            return self._ok({
                "tenantId": tenant_id,
                "records": catalog_mod.audit_records(self.state, tenant_id),
            })
        if parts and parts[0] == "verify" and method == "POST":
            self._require(principal, tenant_id, "audit:read")
            ledger: AuditLedger = self.state.audit[tenant_id]
            verdict = ledger.verify()
            return self._ok({"tenantId": tenant_id, "verify": verdict})
        raise ApiError(404, "not_found", "audit surface is GET / verify")

    def _route_approvals(
        self,
        parts: list[str],
        principal: Principal,
        tenant_id: str,
        method: str,
        body: dict[str, Any],
        now_iso: str,
    ) -> Response:
        if not parts and method == "GET":
            self._require(principal, tenant_id, "approval:read")
            return self._ok({
                "tenantId": tenant_id,
                "approvals": catalog_mod.approvals(self.state, tenant_id),
            })
        if parts and method == "POST":
            approval_id = parts[0]
            decision = parts[1] if len(parts) > 1 else ""
            if decision not in ("approve", "deny"):
                raise ApiError(404, "not_found", "approval action must be approve|deny")
            self._require(principal, tenant_id, "approval:approve")
            approval = next(
                (a for a in self.state.approvals_for(tenant_id)
                 if a.id == approval_id), None
            )
            if approval is None:
                raise ApiError(404, "not_found", f"no such approval {approval_id!r}")
            if approval.status != "pending":
                raise ApiError(409, "already_decided",
                               f"approval {approval_id!r} is {approval.status!r}")
            approval.decided_by = f"user:{principal.email}"
            if decision == "approve":
                approval.status = "approved"
                self._apply_approval(approval, now_iso)
            else:
                approval.status = "denied"
                self._audit(tenant_id, f"user:{principal.email}", "approval.deny",
                            resource=approval.resource,
                            detail=f"approval {approval_id} denied", now_iso=now_iso)
            return self._ok({
                "approvalId": approval_id, "status": approval.status,
            })
        raise ApiError(404, "not_found", "approvals surface is GET / approve|deny")

    # -- helpers ------------------------------------------------------------
    def _require_tenant(self, tenant_id: str) -> None:
        if tenant_id not in self.state.tenants:
            raise ApiError(404, "unknown_tenant", f"no such tenant {tenant_id!r}")

    def _require_agent(self, tenant_id: str, agent_id: str):
        for agent in self.state.agents_for(tenant_id):
            if agent.id == agent_id:
                return agent
        raise ApiError(404, "unknown_agent", f"no agent {agent_id!r} in {tenant_id!r}")

    def _require(self, principal: Principal, tenant_id: str, permission: str) -> None:
        if not self.authorizer.in_scope(principal, tenant_id):
            raise ApiError(403, "scope_denied",
                           f"{principal.email!r} cannot act in tenant {tenant_id!r}")
        if not self.authorizer.allow(principal, tenant_id, permission):
            raise ApiError(
                403, "permission_denied",
                f"role {self.authorizer.role_for(principal, tenant_id)!r} lacks "
                f"{permission!r} in tenant {tenant_id!r}",
            )

    def _agent_json(self, agent) -> dict[str, Any]:
        return {
            "agentId": agent.id,
            "team": agent.team,
            "profileId": agent.profile_id,
            "status": agent.status,
            "modelTier": agent.model_tier,
            "capabilities": agent.capabilities,
        }

    def _apply_approval(self, approval, now_iso: str) -> None:
        tenant_id = approval.tenant_id
        self._audit(tenant_id, str(approval.decided_by or "system:approval"),
                    "approval.approve", resource=approval.resource,
                    detail=f"approval {approval.id} approved", now_iso=now_iso)
        if approval.action == "agent.retire":
            resource = str(approval.resource or "").removeprefix("agent:")
            agent = self._require_agent(tenant_id, resource)
            agent.status = "retired"
            self._audit(tenant_id, str(approval.decided_by or "system:approval"),
                        "agent.retire", resource=approval.resource,
                        detail="retire approved", now_iso=now_iso)
        elif approval.action == "tenant.pause":
            self.state.tenants[tenant_id].paused = True
            self._audit(tenant_id, str(approval.decided_by or "system:approval"),
                        "tenant.pause", resource=approval.resource,
                        detail="pause approved", now_iso=now_iso)

    def _audit(
        self,
        tenant_id: str,
        actor: str,
        action: str,
        *,
        resource: Optional[str] = None,
        detail: Optional[str] = None,
        now_iso: str = "",
    ) -> None:
        self.state.audit[tenant_id].append(
            actor, action, resource=resource, detail=detail, ts=now_iso
        )

    def _list_tenants(self, principal: Principal) -> Response:
        scoped = self.authorizer.scope_tenants(principal, self.state.tenant_ids())
        rows = [
            row for row in catalog_mod.tenants_overview(self.state)
            if row["tenantId"] in scoped
        ]
        return self._ok({"tenants": rows})

    def _ok(self, data: Any) -> Response:
        return Response(
            status=200, is_json=True,
            payload={
                "ok": True, "status": 200, "requestId": _request_id(),
                "data": data, "error": None,
            },
        )


def _request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


def build_app(
    *,
    repo_root: Optional[Path] = None,
    state: Optional[ConsoleState] = None,
    **kwargs: Any,
) -> ConsoleApplication:
    repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    return ConsoleApplication(repo_root=repo_root, state=state, **kwargs)
