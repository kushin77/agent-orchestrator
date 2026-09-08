"""The tenant-scoped MCP tool gateway core.

A single in-process JSON-RPC surface (``handle_message``) exposing the
platform's declared tools to external AI agents. Every ``tools/call`` is
enforced in a fixed order before any tool acts - the no-cross-tenant doctrine
composed from the cannibalized sources:

1. **tenant context** - every tool requires a tenant context (a session token
   in ``params.context.session``); an unscoped call is refused (fail closed).
   An explicit ``tenantId`` that disagrees with the verified session is
   refused - no cross-tenant fallback (capital-underwriting; registry
   ``require_scope``).
2. **authN** - the session/JWT-shaped token is verified (signature + expiry)
   via an injected verifier (``authn``, or a registry-provided verifier).
3. **declared capability** - the tool name must be in the registry; an unknown
   tool is rejected ``-32601`` (allowlist, not denylist - saas-rbac).
4. **authZ** - the injected rbac scope gate (``PermissionGuard``) runs scope
   first, then permission, preserving ``scope`` vs ``permission`` causes.
5. **allowlist** - the session's ``allowedTools`` must name the tool.
6. **rate limit** - the injected rate gate consumes one token per
   (tenant, agent, tool) scope on every call.
7. **tenant-scoped dispatch** - the handler receives an index backend bound to
   the session's tenant; no argument can select another tenant's data.
8. **audit** - exactly one append-only hash-chained record per call (allowed or
   denied) answering who/what/tenant/result.
"""

from __future__ import annotations

import json
import uuid
from functools import partial
from typing import Any, Dict, Optional

from . import authn
from .audit import AuditSink
from .authz import AuthzDecision, PermissionGuard
from .errors import (
    AuthnError,
    AuthzDenied,
    CrossTenantSessionDenied,
    GatewayError,
    InvalidArgumentsError,
    RateLimitedError,
    TenantContextRequired,
    ToolNotAllowedError,
    UnknownToolError,
)
from .kb import KbRegistry
from .model import SessionIdentity, TOOL_CALL_PERMISSION
from .protocol import (
    METHOD_INITIALIZE,
    METHOD_PING,
    METHOD_TOOLS_CALL,
    METHOD_TOOLS_LIST,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    JsonRpcError,
    error_body,
    parse_message,
    result_body,
    tool_result,
)
from .rategate import RateGate, scope_for
from .registry import ToolRegistry


class MCPToolGateway:
    """Enforcement core + in-process JSON-RPC surface for one gateway."""

    def __init__(
        self,
        registry: ToolRegistry,
        kb_registry: Optional[KbRegistry] = None,
        *,
        authz: PermissionGuard,
        audit: Optional[AuditSink] = None,
        rate_gate: Optional[RateGate] = None,
        session_verifier: Optional[authn.SessionVerifier] = None,
        signing_key: Optional[bytes] = None,
    ) -> None:
        if authz is None:
            raise ValueError("MCPToolGateway requires an injected authz guard")
        if session_verifier is None:
            if signing_key is None:
                raise ValueError(
                    "MCPToolGateway requires a session_verifier or a signing_key"
                )
            session_verifier = partial(authn.verify_token, signing_key=signing_key)
        self.registry = registry
        self.kb_registry = kb_registry if kb_registry is not None else KbRegistry()
        self.authz = authz
        self.audit = audit
        self.rate_gate = rate_gate
        self._verify = session_verifier

    # ------------------------------------------------------------------ #
    # JSON-RPC surface
    # ------------------------------------------------------------------ #
    def handle_message(self, message: Any) -> Optional[Dict[str, Any]]:
        """One message in, one response out (None for notifications).

        ``initialize`` / ``ping`` / ``tools/list`` / ``tools/call`` are
        answered; ``notifications/initialized`` and other notifications get no
        reply; unknown methods, malformed envelopes and any enforcement
        failure (context / authN / authZ / allowlist / rate / invalid args)
        get an error response carrying the failure's code.
        """
        try:
            method, params, message_id = parse_message(message)
        except JsonRpcError as exc:
            raw_id = message.get("id") if isinstance(message, dict) else None
            return error_body(raw_id, exc)

        try:
            return self._route(method, params or {}, message_id)
        except GatewayError as exc:
            # A denial is an unconditional stop - never a fallback that retries
            # in another scope. Notifications (id absent) get no reply.
            if message_id is None:
                return None
            return error_body(message_id, exc.as_jsonrpc())

    def _route(
        self, method: str, params: Dict[str, Any], message_id: Any
    ) -> Optional[Dict[str, Any]]:
        if method == METHOD_INITIALIZE:
            return result_body(
                message_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )
        if method == METHOD_PING:
            return result_body(message_id, {})
        if method == METHOD_TOOLS_LIST:
            return self._handle_tools_list(params, message_id)
        if method == METHOD_TOOLS_CALL:
            return self._handle_tools_call(params, message_id)
        if message_id is None:  # notification (incl. notifications/initialized)
            return None
        return error_body(
            message_id,
            JsonRpcError(f"method not found: {method}"),
        )

    # ------------------------------------------------------------------ #
    # tools/list - deterministic declared schemas (narrowed by session)
    # ------------------------------------------------------------------ #
    def _handle_tools_list(
        self, params: Dict[str, Any], message_id: Any
    ) -> Dict[str, Any]:
        context = params.get("context") or {}
        token = context.get("session") if isinstance(context, dict) else None
        if token is None:
            schemas = self.registry.schemas()
        else:
            session = self._require_session(context)
            schemas = [
                schema
                for schema in self.registry.schemas()
                if session.allows(schema["allowlistId"])
            ]
        return result_body(message_id, {"tools": schemas})

    # ------------------------------------------------------------------ #
    # tools/call - the enforced path
    # ------------------------------------------------------------------ #
    def _handle_tools_call(
        self, params: Dict[str, Any], message_id: Any
    ) -> Dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        request_id = uuid.uuid4().hex

        if not isinstance(name, str) or not name:
            self._audit_denied(
                "unknown", session=None, tool_name=name, request_id=request_id,
                reason="no tool name",
            )
            raise UnknownToolError("tools/call requires a tool name")

        # 1+2. tenant context + authN.
        context = params.get("context") or {}
        try:
            session = self._require_session(context)
        except TenantContextRequired as exc:
            self._audit_denied(
                "context", session=None, tool_name=name, request_id=request_id,
                reason=str(exc),
            )
            raise
        except AuthnError as exc:
            self._audit_denied(
                "authn", session=None, tool_name=name, request_id=request_id,
                reason=str(exc),
            )
            raise

        explicit_tenant = (
            context.get("tenantId") if isinstance(context, dict) else None
        )
        if explicit_tenant is not None and explicit_tenant != session.tenant_id:
            self._audit_denied(
                "authz", session=session, tool_name=name, request_id=request_id,
                permission=TOOL_CALL_PERMISSION,
                reason="cross-tenant session use denied (no cross-tenant fallback)",
            )
            raise CrossTenantSessionDenied(
                "cross-tenant session use denied: session for tenant "
                f"{session.tenant_id!r} cannot be used as tenant "
                f"{explicit_tenant!r}"
            )

        # 3. declared capability (fail closed on unknown tools).
        if name not in self.registry:
            self._audit_denied(
                "unknown", session=session, tool_name=name, request_id=request_id,
                permission=TOOL_CALL_PERMISSION,
                reason=f"unknown tool {name!r} (not a declared capability)",
            )
            raise UnknownToolError(f"unknown tool {name!r} (not a declared capability)")

        tool = self.registry.require(name)

        # 4. authZ - rbac scope gate, then permission gate.
        decision: AuthzDecision = self.authz.authorize(session, tool.permission)
        if decision.denied:
            self._audit_denied(
                "authz", session=session, tool_name=name, request_id=request_id,
                permission=tool.permission,
                reason=decision.reason or "denied",
                detail={"reason": decision.reason, "code": decision.code},
            )
            raise AuthzDenied(
                f"authorization denied: {decision.reason} "
                f"(code={decision.code}) for {name!r}",
                data={
                    "reason": decision.reason,
                    "code": decision.code,
                    "permission": tool.permission,
                },
            )

        # 5. session tool allowlist (fail closed).
        if not session.allows(tool.allowlist_id):
            self._audit_denied(
                "allowlist", session=session, tool_name=name, request_id=request_id,
                permission=tool.permission,
                reason=f"tool {name!r} is not in the session allowlist",
            )
            raise ToolNotAllowedError(
                f"tool {name!r} is not in the session's allowedTools "
                f"(allowlist: {sorted(session.allowed_tools)})"
            )

        # 6. rate limit on every call (injected gate).
        if self.rate_gate is not None:
            scope = scope_for(session.tenant_id, session.agent_id, name)
            rate = self.rate_gate.consume(scope)
            if rate.denied:
                self._audit_denied(
                    "rate", session=session, tool_name=name, request_id=request_id,
                    permission=tool.permission,
                    reason=f"rate limit exceeded for scope {scope!r}",
                    detail={"scope": scope, "remaining": rate.remaining},
                )
                raise RateLimitedError(
                    f"rate limit exceeded for {scope!r}", data={"scope": scope}
                )

        # 7+8. tenant-scoped dispatch + audit.
        return self._dispatch_tool(
            tool, arguments, session, request_id=request_id, message_id=message_id
        )

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _require_session(self, context: Any) -> SessionIdentity:
        """Resolve tenant context from ``params.context`` (fail closed)."""
        if not isinstance(context, dict):
            raise TenantContextRequired(
                "every tool is tenant-scoped: params.context must be an object"
            )
        token = context.get("session")
        if not isinstance(token, str) or not token:
            raise TenantContextRequired(
                "every tool requires tenant context: params.context.session "
                "(a session token) is required; no scope never means another tenant"
            )
        return self._verify(token)

    def _dispatch_tool(
        self,
        tool,
        arguments: Dict[str, Any],
        session: SessionIdentity,
        *,
        request_id: str,
        message_id: Any,
    ) -> Dict[str, Any]:
        required = list(tool.input_schema.get("required") or ())
        missing = [key for key in required if key not in arguments]
        if missing:
            self._audit(
                "tool_call",
                status="error",
                session=session,
                tool_name=tool.name,
                permission=tool.permission,
                request_id=request_id,
                arguments=arguments,
                result={"outcome": "invalid_arguments", "missing": missing},
            )
            raise InvalidArgumentsError(
                f"tool {tool.name!r} is missing required arguments: {missing}"
            )

        backend = self.kb_registry.backend_for(session.tenant_id)
        try:
            payload = tool.handler(arguments, session, backend)
        except InvalidArgumentsError as exc:
            self._audit(
                "tool_call",
                status="error",
                session=session,
                tool_name=tool.name,
                permission=tool.permission,
                request_id=request_id,
                arguments=arguments,
                result={"outcome": "invalid_arguments", "message": str(exc)},
            )
            raise
        except Exception as exc:  # a tool failure is a text answer, not a crash
            self._audit(
                "tool_call",
                status="error",
                session=session,
                tool_name=tool.name,
                permission=tool.permission,
                request_id=request_id,
                arguments=arguments,
                result={"outcome": "error", "message": str(exc)},
            )
            return result_body(
                message_id,
                tool_result(
                    json.dumps(
                        {"error": f"tool error: {exc}"}, sort_keys=True, indent=2
                    ),
                    is_error=True,
                ),
            )

        self._audit(
            "tool_call",
            status="ok",
            session=session,
            tool_name=tool.name,
            permission=tool.permission,
            request_id=request_id,
            arguments=arguments,
            result={"outcome": "ok", "summary": self._summarize(payload)},
        )
        return result_body(
            message_id,
            tool_result(json.dumps(payload, sort_keys=True, indent=2)),
        )

    @staticmethod
    def _summarize(payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            count = payload.get("count")
            if count is not None:
                return {"count": int(count)}
            return {key: payload[key] for key in ("tenantId", "repo") if key in payload}
        return {}

    # ------------------------------------------------------------------ #
    # audit helpers
    # ------------------------------------------------------------------ #
    def _audit_denied(
        self,
        status: str,
        *,
        session: Optional[SessionIdentity],
        tool_name: Optional[str],
        request_id: str,
        permission: Optional[str] = None,
        reason: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._audit(
            "tool_call_denied",
            status=status,
            session=session,
            tool_name=tool_name,
            permission=permission,
            request_id=request_id,
            result={"outcome": "denied", "reason": reason},
            extra_detail=detail,
        )

    def _audit(
        self,
        event: str,
        *,
        status: str,
        session: Optional[SessionIdentity],
        tool_name: Optional[str],
        request_id: str,
        permission: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None,
        result: Optional[Dict[str, Any]] = None,
        extra_detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self.audit is None:
            return
        detail: Dict[str, Any] = {"requestId": request_id}
        if tool_name is not None:
            detail["tool"] = tool_name
        if permission is not None:
            detail["permission"] = permission
        if arguments is not None:
            detail["arguments"] = arguments
        if result is not None:
            detail["result"] = result
        if extra_detail is not None:
            detail.update(extra_detail)
        self.audit.append(
            event,
            status=status,
            tenant_id=session.tenant_id if session is not None else None,
            agent_id=session.agent_id if session is not None else None,
            actor=session.subject if session is not None else None,
            detail=detail,
        )

    # ------------------------------------------------------------------ #
    # convenience (tests / CLI)
    # ------------------------------------------------------------------ #
    def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        *,
        session_token: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """One-shot ``tools/call`` returning the full JSON-RPC response dict.

        With ``session_token`` absent the request carries no tenant context and
        is refused (fail closed); ``tenant_id`` optionally cross-checks the
        verified session's tenant.
        """
        context: Dict[str, Any] = {}
        if session_token is not None:
            context["session"] = session_token
        if tenant_id is not None:
            context["tenantId"] = tenant_id
        return self.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": METHOD_TOOLS_CALL,
                "params": {"name": name, "arguments": arguments, "context": context},
            }
        )

    def list_tools(self, session_token: Optional[str] = None) -> Dict[str, Any]:
        """One-shot ``tools/list`` returning the full JSON-RPC response dict."""
        params: Dict[str, Any] = {}
        if session_token is not None:
            params["context"] = {"session": session_token}
        return self.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": METHOD_TOOLS_LIST,
                "params": params,
            }
        )
