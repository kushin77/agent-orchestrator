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
from portal.server.bridge import LiveBridge
from portal.server.chat import (
    CHAT_ASSETS,
    ChatError,
    ChatSurface,
    parse_turn_request,
)
from portal.server.controls import (
    ControlCatalog,
    PolicyEnforcer,
    PolicyStateStore,
    build_control_policy_map,
)
from portal.server.finops import FinOpsReports
from portal.server.fleet import FleetProjection, surface_enabled
from portal.server.surface_health import (
    SURFACE_CANNOT_ASSESS,
    SURFACE_NOT_READY,
    SURFACE_READY,
    console_readiness,
)
from portal.server.live_feed import MAX_REPLAY_LIMIT, LiveFeed
from portal.server.ops_health import OpsHealthReports
from portal.server.fleet_authz import FleetAuthorizer, FleetDenied
from portal.server.erp import ErpModuleError, ErpModuleSurface
from portal.server.livestore import BoardSurface, TelemetryUnavailableError
from portal.server.org_chart import OrgChartView
from portal.server.skill_studio import (
    ACTION_AUTHOR,
    ACTION_PUBLISH,
    ACTION_TEST,
    SkillStudioError,
    SkillStudioSurface,
)
from portal.server.sso import AUTH_GATE_LOGIN_PATH, ConsoleSso, SESSION_COOKIE
from portal.server.state import Approval, ConsoleState, seed_state
from portal.server.surfaces import PortalSurfacesFeed
from portal.server.task_board import TaskBoardError, TaskBoardSurface

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

#: The registry surface key that gates the operator terminal (issue #774).
OPERATOR_TERMINAL_SURFACE = "operator_terminal"
#: The documents that belong to the operator terminal. Gated BEFORE AuthN, like
#: the chat surface: while the flag is off the view (and its script) answer 404
#: ``feature_disabled`` so an unauthenticated probe cannot tell the surface
#: exists (the ``CHAT_ASSETS`` precedent).
OPERATOR_TERMINAL_ASSETS: tuple[str, ...] = ("views/console.html", "js/operator.js")

#: The path prefix the ERP module's own documents live under (ERP-07, issue
#: #652). Gated BEFORE AuthN and before static serving: while ``erp_module`` is
#: off the whole namespace is *absent* — not merely unauthorised — so a probe
#: cannot enumerate a surface that does not exist yet.
ERP_MODULE_ASSET_PREFIX = "erp/"


def _is_erp_document(path: str) -> bool:
    """True for the module's namespace, the bare directory included.

    The bare ``/erp`` matters: left to the static handler it is a directory and
    answers ``404 not_found``, which is a *different* refusal from the
    ``feature_disabled`` every other path in the namespace gives. One namespace,
    one answer while the flag is off.
    """
    stripped = path.strip("/")
    return stripped == ERP_MODULE_ASSET_PREFIX.rstrip("/") or stripped.startswith(
        ERP_MODULE_ASSET_PREFIX
    )


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
        portal_surfaces: Optional[PortalSurfacesFeed] = None,
        finops_reports: Optional[FinOpsReports] = None,
        live_feed: Optional[LiveFeed] = None,
        ops_health: Optional[OpsHealthReports] = None,
        bridge: Optional[LiveBridge] = None,
        chat_surface: Optional[ChatSurface] = None,
        org_chart_view: Optional[OrgChartView] = None,
        skill_studio_surface: Optional[SkillStudioSurface] = None,
        task_board_surface: Optional[TaskBoardSurface] = None,
        operator_terminal_enabled: Optional[bool] = None,
        erp_module_surface: Optional[ErpModuleSurface] = None,
        board_surface: Optional[BoardSurface] = None,
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
        # The portal-surfaces feed (issue #350) — feature-flag-gated OFF.
        self.surfaces = (
            portal_surfaces
            if portal_surfaces is not None
            else PortalSurfacesFeed(repo_root=self.repo_root)
        )
        # Tenant scoping + RBAC for that surface (issue #333).
        self.fleet_authz = FleetAuthorizer(state=self.state, repo_root=self.repo_root)
        # The FinOps single-pane surface (issue #341) — feature-flag-gated OFF.
        self.finops = (
            finops_reports
            if finops_reports is not None
            else FinOpsReports(repo_root=self.repo_root)
        )
        # The live telemetry event feed (issue #345) — feature-flag-gated OFF.
        self.live = (
            live_feed
            if live_feed is not None
            else LiveFeed(repo_root=self.repo_root)
        )
        # The ops/health/SLO surface (issue #342) — feature-flag-gated OFF.
        self.ops = (
            ops_health
            if ops_health is not None
            else OpsHealthReports(repo_root=self.repo_root)
        )
        # The versioned live-data bridge (issue #339) — feature-flag-gated OFF.
        self.bridge = (
            bridge
            if bridge is not None
            else LiveBridge(repo_root=self.repo_root, live_feed=self.live)
        )
        # The conversational surface (issue #508, ADR-0023) — flag-gated OFF.
        self.chat = (
            chat_surface
            if chat_surface is not None
            else ChatSurface(repo_root=self.repo_root)
        )
        # The workbook-11 views (issue #642) — each flag-gated OFF. Their flags
        # are declared in the portal's OWN config (portal/config/feature-flags
        # .yaml) rather than the control-plane registry, because they are views
        # inside the portal service and add no service or terraform variable.
        self.org_chart = (
            org_chart_view
            if org_chart_view is not None
            else OrgChartView(repo_root=self.repo_root)
        )
        self.skill_studio = (
            skill_studio_surface
            if skill_studio_surface is not None
            else SkillStudioSurface(repo_root=self.repo_root)
        )
        self.task_board = (
            task_board_surface
            if task_board_surface is not None
            else TaskBoardSurface(repo_root=self.repo_root)
        )
        # The ERP module's portal surface (ERP-07, issue #652) — flag-gated OFF.
        # Its switch is declared in the portal's OWN config, beside the three
        # workbook views above and for the same reason: it is a view inside the
        # portal service. The module's manifest records that the *promotion* row
        # (infra/feature-flags/registry.yaml services.erp_module /
        # surfaces.erp_module) lands with this surface; the fail-closed reader is
        # the one those three views already use.
        self.erp = (
            erp_module_surface
            if erp_module_surface is not None
            else ErpModuleSurface(repo_root=self.repo_root)
        )
        # The fleet board (issue #880, EPIC #878 lane L1) — feature-flag-gated
        # OFF, declared beside the workbook-11 views for the same reason: a
        # view inside the portal service, no service or terraform variable.
        self.board = (
            board_surface
            if board_surface is not None
            else BoardSurface(repo_root=self.repo_root)
        )
        # The operator terminal (issue #774) — feature-flag-gated OFF. It
        # composes the fleet projection (read) and the remote control family
        # (steer); this flag gates the route + view only, and the two halves
        # keep their own flags. Read through the same fail-closed reader every
        # other surface uses.
        self.operator_terminal_enabled = (
            operator_terminal_enabled
            if operator_terminal_enabled is not None
            else surface_enabled(
                self.repo_root, surface=OPERATOR_TERMINAL_SURFACE
            )
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
            # The operator terminal's one link (issue #774): `/console` opens
            # the live fleet view behind the same session. Flag-gated inside.
            if path == "/console":
                return self._console(cookies)
            # The conversational surface's own documents (issue #508) are
            # *absent* — not refused — while the surface is unpromoted. The
            # flag is checked here, before any session work, so an
            # unauthenticated probe cannot even tell the view exists.
            if not self.chat.enabled and path.strip("/") in CHAT_ASSETS:
                raise ApiError(
                    404,
                    "feature_disabled",
                    "the chat surface is feature-flag-gated OFF "
                    "(infra/feature-flags/registry.yaml surfaces.chat)",
                )
            # The operator terminal's documents ship the same way (issue #774):
            # *absent* while unpromoted, before any session work.
            if not self.operator_terminal_enabled and path.strip("/") in OPERATOR_TERMINAL_ASSETS:
                raise ApiError(
                    404,
                    "feature_disabled",
                    "the operator terminal is feature-flag-gated OFF "
                    "(infra/feature-flags/registry.yaml surfaces.operator_terminal)",
                )
            # The ERP module's own documents ship the same way (ERP-07, issue
            # #652): *absent* while unpromoted, before any session work and
            # before static serving, so the whole directory is invisible rather
            # than protected.
            if not self.erp.enabled and _is_erp_document(path):
                raise ApiError(
                    404,
                    "feature_disabled",
                    "the ERP module surface is feature-flag-gated OFF "
                    "(portal/config/feature-flags.yaml surfaces.erp_module)",
                )
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

    def _console(self, cookies: dict[str, str]) -> Response:
        """The operator terminal's one link (issue #774): ``GET /console``.

        Flag-gated **before** AuthN — an unpromoted surface is absent, not
        merely unauthorised — then the same session pipeline as the front door:
        a verified ``os-session-token`` opens the view, anything else reaches
        the OS auth gate. No login of its own is invented here.
        """
        if not self.operator_terminal_enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the operator terminal is feature-flag-gated OFF "
                "(infra/feature-flags/registry.yaml surfaces.operator_terminal)",
            )
        token = cookies.get(SESSION_COOKIE)
        if token:
            try:
                self.sso.verify(token)
                return Response(
                    status=302,
                    is_json=False,
                    payload="",
                    headers=[("Location", "/views/console.html")],
                )
            except Exception:  # noqa: BLE001 - a refused session reaches the gate
                pass
        return Response(
            status=302, is_json=False, payload="",
            headers=[("Location", AUTH_GATE_LOGIN_PATH)],
        )

    def _readiness(self) -> Response:
        """The console surfaces' own readiness signal (issue #802).

        Honest by construction: the states come from
        ``portal.server.surface_health``, which reads each surface's declaration,
        any engaged runtime rollback, and the artifacts that surface needs.

        Two rules, and they are about *who may be named*:

        * a surface is named exactly when it exists for a reader — it is
          promoted, or it was promoted and has since been rolled back. An
          unpromoted surface is absent, not merely unauthorised, so a probe
          cannot enumerate what does not exist yet (the ``/console`` doctrine);
        * the aggregate state still refuses to call the console ready when a
          surface could not be assessed or is promoted-but-broken: 503, never a
          cheerful 200 — and ``cannot-assess`` needs no name to be honest.
        """
        reports = console_readiness(self.repo_root, static_dir=self.static_dir)
        named = [report for report in reports if report.promoted or report.rolled_back]
        if any(report.state == SURFACE_CANNOT_ASSESS for report in reports):
            state, status = SURFACE_CANNOT_ASSESS, 503
        elif any(report.state == SURFACE_NOT_READY for report in named):
            state, status = SURFACE_NOT_READY, 503
        else:
            state, status = SURFACE_READY, 200
        return Response(
            status=status,
            is_json=True,
            payload={
                "ok": status == 200,
                "status": status,
                "requestId": _request_id(),
                "data": {
                    "service": "portal-console",
                    "state": state,
                    "surfaces": {
                        report.surface: report.as_dict()
                        for report in named
                        if report.state != SURFACE_CANNOT_ASSESS
                    },
                },
                "error": None,
            },
        )

    def _is_static(self, path: str) -> bool:
        segments = path.strip("/").split("/")
        return bool(segments) and segments[0] in {
            "views", "css", "js", "design-tokens", "assets", "favicon.svg",
            "erp",
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

        # The console's own READINESS rail (issue #802), on the health route it
        # already exposes — never a second dashboard (ADR-0022). It reports the
        # surfaces this console serves, so an unpromoted surface is not named
        # here either (absent, not merely unauthorised — the `/console` doctrine),
        # and a state that cannot be assessed answers 503 rather than "ready".
        if parts == ["healthz", "ready"] and method == "GET":
            return self._readiness()

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

        # The portal-surfaces feed (issue #350) is gated the same way and for
        # the same reason: an unpromoted surface is invisible, not merely
        # unauthorised.
        if parts[0] == "portal" and not self.surfaces.enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the portal-surfaces feed is feature-flag-gated OFF "
                "(infra/feature-flags/registry.yaml surfaces.portal_surfaces)",
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

        # The live telemetry event feed ships the same way (GR-5), also before
        # authN: an unpromoted surface must be invisible, not merely protected.
        if parts[0] == "telemetry" and not self.live.enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the live telemetry feed is feature-flag-gated OFF "
                "(infra/feature-flags/registry.yaml surfaces.telemetry_live_feed)",
            )

        # The ops/health/SLO surface ships the same way (GR-5), also before
        # authN: an unpromoted surface must be invisible, not merely protected.
        if parts[0] == "ops" and not self.ops.enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the ops/health/SLO surface is feature-flag-gated OFF "
                "(infra/feature-flags/registry.yaml surfaces.ops_health)",
            )

        # The versioned live-data bridge (issue #339) ships the same way (GR-5),
        # also before authN: while the flag is off the whole /api/v1 surface is
        # invisible rather than merely unauthorised.
        if parts[0] == "v1" and not self.bridge.enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the live-data bridge is feature-flag-gated OFF "
                "(infra/feature-flags/registry.yaml surfaces.live_bridge)",
            )

        # The conversational surface (issue #508) ships the same way (GR-5),
        # also before authN: an unpromoted chat surface is invisible rather
        # than merely unauthorised.
        if parts[0] == "chat" and not self.chat.enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the chat surface is feature-flag-gated OFF "
                "(infra/feature-flags/registry.yaml surfaces.chat)",
            )

        # The remote control family (issue #554, RC-3) is the one hook line this
        # lane adds. It hooks here, before authN, so its own flag gate runs first;
        # the module is imported late so the hook needs no import and no new app
        # state (see portal/server/control_api.py for the refusal matrix).
        if parts[0] == "control":
            return __import__("portal.server.control_api", fromlist=["control"]).control(self, parts[1:], method, body, cookies, now_iso)

        # The workbook-11 views (issue #642) ship feature-flag-gated OFF (GR-5),
        # also before authN: an unpromoted view is absent, not merely
        # unauthorised. Their flags live in the portal's own
        # portal/config/feature-flags.yaml (see portal/server/config_flags.py).
        if parts[0] == "orgchart" and not self.org_chart.enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the org-chart view is feature-flag-gated OFF "
                "(portal/config/feature-flags.yaml surfaces.org_chart)",
            )
        if parts[0] == "skillstudio" and not self.skill_studio.enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the skill-studio surface is feature-flag-gated OFF "
                "(portal/config/feature-flags.yaml surfaces.skill_studio)",
            )
        if parts[0] == "taskboard" and not self.task_board.enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the tenant task board is feature-flag-gated OFF "
                "(portal/config/feature-flags.yaml surfaces.task_board)",
            )

        # The ERP module's surface (ERP-07, issue #652) ships the same way and
        # is gated at the same point — before authN — so an unpromoted module is
        # absent rather than distinguishable by an authentication probe.
        if parts[0] == "erp" and not self.erp.enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the ERP module surface is feature-flag-gated OFF "
                "(portal/config/feature-flags.yaml surfaces.erp_module)",
            )

        # The fleet board (issue #880) ships the same way, gated before authN.
        if parts[0] == "board" and not self.board.enabled:
            raise ApiError(
                404,
                "feature_disabled",
                "the fleet board is feature-flag-gated OFF "
                "(portal/config/feature-flags.yaml surfaces.fleet_board)",
            )

        # authenticated surface
        principal, claims = self._require_session(cookies)
        try:
            if parts[0] == "fleet":
                return self._route_fleet(parts, method, query, principal)
            if parts[0] == "portal":
                return self._route_portal(parts, method)
            if parts[0] == "finops":
                return self._route_finops(parts, principal, method, query)
            if parts[0] == "telemetry":
                return self._route_live_feed(parts, principal, method, query)
            if parts[0] == "ops":
                return self._route_ops(parts, principal, method, query)
            if parts[:2] == ["v1", "bridge"]:
                return self._route_bridge(parts[2:], principal, method, query)
            if parts[0] == "chat":
                return self._route_chat(parts, method, query, body, principal)
            if parts[0] == "orgchart":
                return self._route_org_chart(parts, method)
            if parts[0] == "skillstudio":
                return self._route_skill_studio(parts, method, query, body)
            if parts[0] == "taskboard":
                return self._route_task_board(parts, method, query)
            if parts[0] == "erp":
                return self._route_erp(parts[1:], method, body)
            if parts[0] == "board":
                return self._route_board(parts, method)
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
        self,
        parts: list[str],
        method: str,
        query: dict[str, str],
        principal: Principal,
    ) -> Response | StreamResponse:
        """The read-only web single-pane-of-glass (issue #331), access-controlled.

        Every read is delegated to ``fleet/console.py`` through the projection,
        so the browser receives exactly the dashboard's projection. The surface
        is GET-only and, when the feature flag is off, never reaches here. Each
        read is scoped to the caller by ``fleet_authz`` (issue #333): a tenant
        principal receives only its own org's rows, and the cross-org roll-up is
        refused unless the caller passes the platform gates.
        """
        if method != "GET":
            raise ApiError(405, "method_not_allowed", "the fleet surface is GET only")
        surface = parts[1:]
        try:
            if surface == ["rollup"]:
                return self._ok(self.fleet_authz.rollup(principal, self.fleet))
            if surface == ["snapshot"]:
                return self._ok(self.fleet_authz.scoped_snapshot(principal, self.fleet))
            if surface == ["events"]:
                return self._ok(
                    self.fleet_authz.scoped_events(
                        principal, self.fleet, self._fleet_events_limit(query)
                    )
                )
            if surface == ["stream"]:
                return StreamResponse(
                    frames=self.fleet_authz.scoped_stream(principal, self.fleet)
                )
        except FleetDenied as exc:
            raise ApiError(exc.status, exc.code, exc.message) from None
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

    # -- portal-surfaces feed (issue #350) -----------------------------------
    def _route_portal(self, parts: list[str], method: str) -> Response:
        """The portal-surfaces feed (issue #350).

        One read: the pinned CMR fleet-surface document the serving layer ships
        (``registry/portal-surfaces.pinned.json``, provenance in its ``pin``
        block). GET-only; when the feature flag is off the route never reaches
        here.
        """
        if method != "GET":
            raise ApiError(405, "method_not_allowed", "the portal-surfaces feed is GET only")
        surface = parts[1:]
        if surface == ["surfaces"]:
            return self._ok(self.surfaces.document())
        raise ApiError(404, "not_found", f"no such portal surface: {'/'.join(surface)}")

    # -- workbook-11 views (issue #642) --------------------------------------
    def _route_org_chart(self, parts: list[str], method: str) -> Response:
        """The org-chart view (issue #642, workbook-11).

        Two reads, both delegated to ``OrgChartView``: the workbook-1
        declaration (``GET /api/orgchart/chart``) and the workbook-6 role-health
        feed joined to it (``GET /api/orgchart/health``). GET-only; when the
        view's flag is off the route never reaches here.
        """
        if method != "GET":
            raise ApiError(405, "method_not_allowed", "the org-chart view is GET only")
        surface = parts[1:]
        if surface == ["chart"]:
            return self._ok(self.org_chart.chart())
        if surface == ["health"]:
            return self._ok(self.org_chart.health())
        raise ApiError(404, "not_found", f"no such org-chart view: {'/'.join(surface)}")

    def _route_skill_studio(
        self,
        parts: list[str],
        method: str,
        query: dict[str, str],
        body: dict[str, Any],
    ) -> Response:
        """The skill-studio surface (issue #642, workbook-11).

        Reads: ``GET /api/skillstudio/skills`` (optionally ``?category=`` /
        ``?text=``) and ``GET /api/skillstudio/skills/<id>`` (optionally
        ``?version=``). Writes: ``POST /api/skillstudio/<author|test|publish>``,
        each one a single edge of the workbook-9 lifecycle driven through
        ``SkillStudio`` — the surface re-checks no gate, so a refused publish
        carries the studio's own reason. Every read is delegated to the surface,
        which owns the filter vocabulary and the not-found refusal.
        """
        surface = parts[1:]
        if method == "GET":
            try:
                if not surface or surface == ["skills"]:
                    return self._ok(
                        self.skill_studio.skills(
                            category=(query.get("category") or "").strip() or None,
                            text=(query.get("text") or "").strip() or None,
                        )
                    )
                if len(surface) == 2 and surface[0] == "skills":
                    return self._ok(
                        self.skill_studio.skill(
                            surface[1],
                            version=(query.get("version") or "").strip() or None,
                        )
                    )
            except SkillStudioError as exc:
                raise ApiError(exc.status, exc.code, exc.message) from None
            raise ApiError(404, "not_found", f"no such skill-studio read: {'/'.join(surface)}")
        if method == "POST":
            if len(surface) == 1 and surface[0] == ACTION_AUTHOR:
                return self._skill_studio_write(self.skill_studio.author, body)
            if len(surface) == 1 and surface[0] == ACTION_TEST:
                return self._skill_studio_write(self.skill_studio.test, body)
            if len(surface) == 1 and surface[0] == ACTION_PUBLISH:
                return self._skill_studio_write(self.skill_studio.publish, body)
            raise ApiError(404, "not_found", f"no such skill-studio action: {'/'.join(surface)}")
        raise ApiError(405, "method_not_allowed", "the skill studio is GET/POST only")

    def _skill_studio_write(self, action, body: dict[str, Any]) -> Response:
        """Run one studio transition, translating its refusals into the envelope."""
        try:
            return self._ok(action(body))
        except SkillStudioError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from None

    def _route_task_board(
        self, parts: list[str], method: str, query: dict[str, str]
    ) -> Response:
        """The tenant task board (issue #642, workbook-11).

        Reads: ``GET /api/taskboard/tickets`` (the board) and
        ``GET /api/taskboard/tickets/<id>`` (one ticket). Every row is a replay
        of the engine's own event log through ``TicketRuntime`` — the board
        keeps no ticket state of its own. GET-only; when the board's flag is off
        the route never reaches here.
        """
        if method != "GET":
            raise ApiError(405, "method_not_allowed", "the tenant task board is GET only")
        surface = parts[1:]
        tenant = (query.get("tenant") or "").strip()
        try:
            if surface == ["tickets"]:
                return self._ok(self.task_board.board(tenant=tenant))
            if len(surface) == 2 and surface[0] == "tickets":
                return self._ok(self.task_board.ticket(surface[1], tenant=tenant))
            if len(surface) == 2 and surface[0] == "moves":
                return self._ok(
                    self.task_board.legal_moves(surface[1], tenant=tenant)
                )
        except TaskBoardError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from None
        raise ApiError(404, "not_found", f"no such task-board read: {'/'.join(surface)}")

    def _route_board(self, parts: list[str], method: str) -> Response:
        """The fleet board (issue #880).

        Reads: ``GET /api/board/rows`` — every schema-valid row joined from
        ``.board/snapshot.json`` + ``.board/claims.jsonl``, plus any row the
        schema refused (named, not dropped). GET-only; when the board's flag
        is off the route never reaches here.
        """
        if method != "GET":
            raise ApiError(405, "method_not_allowed", "the fleet board is GET only")
        surface = parts[1:]
        if surface == ["rows"]:
            try:
                return self._ok(self.board.rows())
            except TelemetryUnavailableError as exc:
                raise ApiError(503, "board_unavailable", str(exc)) from None
        raise ApiError(404, "not_found", f"no such board read: {'/'.join(surface)}")

    def _route_erp(
        self, surface: list[str], method: str, body: dict[str, Any]
    ) -> Response:
        """The ERP module's portal surface (ERP-07, issue #652).

        Three answers are the module's own — the declaration (``module``), the
        per-family tallies (``dashboard``) and the reports — and each is composed
        from ERP-06's served contract and the API's own answers, never from a
        store of its own. Everything else under ``/api/erp/`` is a **verbatim
        proxy** of ERP-06's route table: the path is handed to the module's own
        surface, which appends ERP-06's prefix and returns that surface's
        envelope unchanged, so an operator reading the console sees ERP-06's
        status, code and message rather than a second dialect of them. The ERP
        authorization decision belongs to ERP-08 and is taken inside the surface
        this proxy mounts; the session in front of it authenticates the operator
        and is never turned into an ERP role.

        When the module's flag is off none of this is reachable — the route is
        refused with 404 ``feature_disabled`` before authN.
        """
        try:
            if surface == ["module"] and method == "GET":
                return self._ok(self.erp.module())
            if surface == ["dashboard"] and method == "GET":
                return self._ok(self.erp.dashboard())
            if surface == ["reports"] and method == "GET":
                return self._ok(self.erp.reports())
            if len(surface) == 2 and surface[0] == "reports" and method == "GET":
                return self._ok(self.erp.report(surface[1]))
        except ErpModuleError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from None
        if surface and surface[0] in {"module", "dashboard", "reports"}:
            raise ApiError(405, "method_not_allowed", f"{method} is not allowed here")
        if not surface:
            raise ApiError(404, "not_found", "no such ERP route: /api/erp")
        return self._erp_proxy(surface, method, body)

    def _erp_proxy(
        self, surface: list[str], method: str, body: dict[str, Any]
    ) -> Response:
        """Hand one request to ERP-06 and return **its** envelope, unchanged.

        The envelope ERP-06 emits is the same house envelope the console emits
        (``identity/cpapi/router``), so it travels as the response body verbatim:
        no code is renamed, no status is re-derived, and a refusal ERP-08 made —
        ``403 permission-denied``, ``409 state_jumped``, a field policy's
        ``field-write-denied`` — arrives with the code and status the module's
        own contract declares for it.
        """
        try:
            envelope = self.erp.call(
                method, "/" + "/".join(surface), body=body if method in {"POST", "PUT"} else None
            )
        except ErpModuleError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from None
        status = envelope.get("status")
        return Response(
            status=int(status) if isinstance(status, int) else 200,
            is_json=True,
            payload=envelope,
        )

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

    # -- live telemetry feed (issue #345) -----------------------------------
    def _route_live_feed(
        self,
        parts: list[str],
        principal: Principal,
        method: str,
        query: dict[str, str],
    ) -> Response | StreamResponse:
        """The live telemetry event feed (issue #345).

        GET-only. Every frame is delegated to ``LiveFeed`` over the live
        telemetry stores — this route owns authorization and transport shape
        only, and never restates a provider, a cost or a verdict. The
        connection is scoped: a principal only ever receives frames for the
        tenants where its role grants ``event:read``, and the header's counts
        are computed over that same subset so no other tenant's volume leaks.
        """
        if method != "GET":
            raise ApiError(405, "method_not_allowed", "the live feed is GET only")
        visible = self._live_feed_tenants(principal)
        limit = self._live_replay_limit(query)
        surface = parts[1:]
        if surface == ["stream"]:
            return StreamResponse(frames=self.live.stream(tenants=visible, replay=limit))
        if surface == ["recent"]:
            return self._ok(self.live.recent(tenants=visible, limit=limit))
        raise ApiError(
            404, "not_found", f"no such telemetry surface: {'/'.join(surface)}"
        )

    def _live_feed_tenants(self, principal: Principal) -> Optional[list[str]]:
        """The tenants a principal may watch (``None`` = every tenant).

        A super-admin sees the whole platform; anyone else sees exactly the
        tenants whose scope they hold and where their role grants
        ``event:read``. An empty set is a refusal — never an empty feed, which
        would read as "the platform saw no traffic".
        """
        if principal.super_admin:
            return None
        tenants = [
            tenant
            for tenant in self.authorizer.scope_tenants(
                principal, self.state.tenant_ids()
            )
            if self.authorizer.allow(principal, tenant, "event:read")
        ]
        if not tenants:
            raise ApiError(
                403,
                "permission_denied",
                f"{principal.email!r} may not read telemetry events in any tenant",
            )
        return tenants

    def _live_replay_limit(self, query: dict[str, str]) -> int:
        """The replay window a client asked for (bounded by the lane's max)."""
        raw = (query.get("replay") or "").strip()
        if not raw:
            return self.live.replay_limit
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ApiError(
                400, "invalid_request", "replay must be an integer"
            ) from None
        return max(0, min(value, MAX_REPLAY_LIMIT))

    # -- ops/health/SLO (issue #342) ----------------------------------------
    def _route_ops(
        self,
        parts: list[str],
        principal: Principal,
        method: str,
        query: dict[str, str],
    ) -> Response:
        """The ops/health/SLO surface (issue #342).

        GET-only. Every verdict, percentile and alert is delegated to the
        observability lane through ``OpsHealthReports`` — this route owns
        authorization and transport shape only, and never restates an SLO
        target or a severity. Like every other tenant surface it is scoped: a
        principal only ever sees the tenants it may read (``agent:read``).
        """
        if method != "GET":
            raise ApiError(405, "method_not_allowed", "the ops surface is GET only")
        surface = parts[1:]
        if surface == ["overview"]:
            return self._ok(self.ops.overview(self._ops_readable(principal)))
        if surface == ["alerts"]:
            tenant_id = (query.get("tenant") or "").strip()
            if tenant_id:
                self._require_ops_tenant(tenant_id)
                self._require(principal, tenant_id, "agent:read")
                return self._ok(self.ops.alerts([tenant_id]))
            return self._ok(self.ops.alerts(self._ops_readable(principal)))
        if surface == ["dashboard"]:
            tenant_id = (query.get("tenant") or "").strip()
            if tenant_id:
                self._require_ops_tenant(tenant_id)
                self._require(principal, tenant_id, "agent:read")
                return self._ok(self.ops.dashboard([tenant_id]))
            return self._ok(self.ops.dashboard(self._ops_readable(principal)))
        if surface in (["agents"], ["slos"]):
            tenant_id = (query.get("tenant") or "").strip()
            if not tenant_id:
                raise ApiError(400, "invalid_request", "tenant is required")
            self._require_ops_tenant(tenant_id)
            self._require(principal, tenant_id, "agent:read")
            reader = self.ops.agents if surface == ["agents"] else self.ops.slos
            return self._ok(reader(tenant_id))
        raise ApiError(404, "not_found", f"no such ops surface: {'/'.join(surface)}")

    def _ops_readable(self, principal: Principal) -> list[str]:
        """The ops tenants a principal may read (scope gate + ``agent:read``)."""
        return [
            tenant
            for tenant in self.authorizer.scope_tenants(
                principal, self.ops.tenant_ids()
            )
            if self.authorizer.allow(principal, tenant, "agent:read")
        ]

    def _require_ops_tenant(self, tenant_id: str) -> None:
        """Fail closed on a tenant the live span feed does not know."""
        if not self.ops.known_tenant(tenant_id):
            raise ApiError(404, "unknown_tenant", f"no such tenant {tenant_id!r}")

    # -- versioned live-data bridge (issue #339) ----------------------------
    #: The default number of telemetry records a bridge read hydrates with.
    BRIDGE_TELEMETRY_DEFAULT_LIMIT = 50

    def _route_bridge(
        self,
        parts: list[str],
        principal: Principal,
        method: str,
        query: dict[str, str],
    ) -> Response | StreamResponse:
        """The versioned bridge over the platform's four state families (#339).

        GET-only. The contract lives in ``portal/server/bridge.py``; this route
        owns transport shape and authorization only. Every read is delegated to
        the family's owning lane, so no figure, verdict or route is restated
        here. Platform-config families (registry/gateway/guardrails) are
        readable by any authenticated principal, exactly like the portal-surfaces
        feed; the telemetry family is scoped through the live feed's own rule,
        and a principal with no visible tenant is refused rather than shown an
        empty feed.
        """
        if method != "GET":
            raise ApiError(405, "method_not_allowed", "the live-data bridge is GET only")
        if not parts:
            return self._ok(self.bridge.manifest())
        if parts == ["stream"]:
            return StreamResponse(frames=self.bridge.stream())
        family = parts[0]
        if family not in self.bridge.families():
            raise ApiError(404, "not_found", f"no such bridge family: {family}")
        if family == "telemetry":
            visible = self._live_feed_tenants(principal)
            return self._ok(
                self.bridge.telemetry(
                    tenants=visible, limit=self._bridge_telemetry_limit(query)
                )
            )
        return self._ok(self.bridge.family(family))

    def _bridge_telemetry_limit(self, query: dict[str, str]) -> int:
        raw = (query.get("limit") or "").strip()
        if not raw:
            return self.BRIDGE_TELEMETRY_DEFAULT_LIMIT
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            raise ApiError(400, "invalid_request", "limit must be an integer") from None
        return max(1, limit)

    # -- conversational surface (issue #508, ADR-0023) ----------------------
    def _route_chat(
        self,
        parts: list[str],
        method: str,
        query: dict[str, str],
        body: dict[str, Any],
        principal: Principal,
    ) -> Response | StreamResponse:
        """The gateway-authoritative conversational surface (issue #508).

        Transport, authorization and *refusal shape* only: every tier, source,
        figure and verdict is delegated to ``ChatSurface``, which consumes the
        serving surface over HTTP and imports no authority. A turn is streamed
        (``StreamResponse``), and the two ways a turn can be refused before it
        starts — a measured hard budget stop and a budget that could not be read
        — are distinct statuses and distinct codes, because they mean different
        things to an operator.

        Permissions come from the platform's existing vocabulary: reading the
        history is ``session:read``, opening one is ``session:manage``, running
        a turn is ``agent:run``, the tier ladder is ``model:read`` and the budget
        state is ``budget:read``. No new vocabulary is minted here.
        """
        surface = parts[1:]
        if surface == ["tiers"]:
            self._require_method(method, "GET", "the tier picker is GET only")
            tenant_id = self._chat_tenant(principal, query, body, "model:read")
            return self._ok(self.chat.tiers(tenant_id))
        if surface == ["budget"]:
            self._require_method(method, "GET", "the budget state is GET only")
            tenant_id = self._chat_tenant(principal, query, body, "budget:read")
            return self._ok(self.chat.budget(tenant_id).as_json())
        if surface == ["conversations"]:
            if method == "GET":
                tenant_id = self._chat_tenant(principal, query, body, "session:read")
                return self._ok(
                    {
                        "tenantId": tenant_id,
                        "conversations": self.chat.conversations(tenant_id),
                    }
                )
            if method == "POST":
                tenant_id = self._chat_tenant(principal, query, body, "session:manage")
                conversation = self._chat_call(
                    self.chat.create_conversation,
                    tenant_id,
                    str(body.get("title") or ""),
                )
                return self._ok({"conversation": conversation})
            raise ApiError(405, "method_not_allowed", "conversations is GET or POST")
        if len(surface) >= 2 and surface[0] == "conversations":
            conversation_id = surface[1]
            tail = surface[2:]
            if not tail:
                self._require_method(method, "GET", "a conversation is GET only")
                tenant_id = self._chat_tenant(principal, query, body, "session:read")
                conversation = self._chat_call(
                    self.chat.conversation, tenant_id, conversation_id
                )
                return self._ok({"conversation": conversation})
            if tail == ["turns"]:
                self._require_method(method, "POST", "a turn is POST only")
                tenant_id = self._chat_tenant(principal, query, body, "agent:run")
                text, tier = self._chat_call(parse_turn_request, body)
                return self._chat_turn(
                    tenant_id, conversation_id, text=text, tier=tier
                )
            if tail == ["cancel"]:
                self._require_method(method, "POST", "cancel is POST only")
                tenant_id = self._chat_tenant(principal, query, body, "agent:run")
                self._chat_call(
                    self.chat.require_conversation, tenant_id, conversation_id
                )
                return self._ok(
                    self._chat_call(self.chat.cancel, tenant_id, conversation_id)
                )
            if tail == ["retry"]:
                self._require_method(method, "POST", "retry is POST only")
                tenant_id = self._chat_tenant(principal, query, body, "agent:run")
                text, tier, retry_of = self._chat_call(
                    self.chat.retry_request,
                    tenant_id,
                    conversation_id,
                    str(body.get("tier") or "").strip().upper(),
                )
                return self._chat_turn(
                    tenant_id,
                    conversation_id,
                    text=text,
                    tier=tier,
                    retry_of=retry_of,
                )
        raise ApiError(404, "not_found", f"no such chat surface: {'/'.join(surface)}")

    def _chat_turn(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        text: str,
        tier: str,
        retry_of: str = "",
    ) -> StreamResponse:
        """Build the turn stream, refusing (not starting) on a closed budget.

        The budget is read **before** anything is written: a hard stop and an
        unreadable budget both refuse here, so a stream never opens with a frame
        that pretends the turn was allowed.
        """
        self._chat_call(self.chat.require_conversation, tenant_id, conversation_id)
        budget = self.chat.budget(tenant_id)
        refusal = self.chat.refusal(tenant_id, budget)
        if refusal is not None:
            raise ApiError(refusal.status, refusal.code, refusal.message)
        frames = self.chat.turn_stream(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            text=text,
            tier=tier,
            budget=budget,
            retry_of=retry_of,
        )
        return StreamResponse(frames=frames)

    def _chat_tenant(
        self,
        principal: Principal,
        query: dict[str, str],
        body: dict[str, Any],
        permission: str,
    ) -> str:
        """The tenant a chat request acts in, with the route's own permission.

        Explicit ``tenant`` wins; otherwise a single-tenant principal is
        unambiguous and a multi-tenant one must say which — never a silent
        default that could act in the wrong tenant.
        """
        tenant_id = str(query.get("tenant") or body.get("tenant") or "").strip()
        if not tenant_id:
            scoped = self.authorizer.scope_tenants(principal, self.state.tenant_ids())
            if len(scoped) != 1:
                raise ApiError(
                    400,
                    "invalid_request",
                    "tenant is required: this principal is scoped to "
                    f"{len(scoped)} tenants",
                )
            tenant_id = scoped[0]
        self._require_tenant(tenant_id)
        self._require(principal, tenant_id, permission)
        return tenant_id

    @staticmethod
    def _require_method(method: str, allowed: str, message: str) -> None:
        if method != allowed:
            raise ApiError(405, "method_not_allowed", message)

    @staticmethod
    def _chat_call(callback, *args):
        """Run a surface call, translating its refusal into the envelope."""
        try:
            return callback(*args)
        except ChatError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc

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
        """The caller's own identity — and, with it, its own control verbs.

        ``controlVerbs`` is the subset of the closed control vocabulary *this*
        caller's capabilities reach (issue #1523). It rides here rather than on a
        new route because this is already the console's one session-scoped
        "what may I do" read: the shell, ``agents``, ``budgets``, ``policies``
        and ``approvals`` all gate on it today, and the operator terminal's
        steer panel is the next consumer of the same question. The list is
        computed by ``control_api`` — the module that owns both the vocabulary
        and the capability decision — so the panel cannot render a verb the
        dispatch path would refuse; ``None`` means the surface is off or the
        store is unreadable, which the client renders as fail-closed.
        """
        control_api = __import__(
            "portal.server.control_api", fromlist=["permitted_verb_ids"]
        )
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
                "controlVerbs": control_api.permitted_verb_ids(self, principal),
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
