"""Offline platform doubles for the consumer SDK tests (issue #41).

These are the SDK's *platform doubles*: in-memory transports that implement the
#37/#38 route + envelope shapes and the #16 gateway dispatch envelope exactly
as documented by the merged contracts, so the Python SDK is proven end-to-end
offline (no network, no real API calls, no server socket).

- :class:`FakeGatewayBackend` — ``POST /v1/agents/{agentId}/tasks`` (+
  streaming), the issue #16 gateway envelope ``{status, result, record}`` with
  the closed outcome vocabulary, plus edge-style authN (bearer session token
  present, not expired, tenant matches the request body) and 401/403 handling.
- :class:`FakeControlPlane` — the issue #38 envelope ``{ok,status,requestId,
  data,error}`` over the issue #37 public + #38 backend routes the SDK calls:
  usage, audit export/query and policies, with tenant-scope + permission gates.
- :class:`FakeMcpTransport` — the issue #20 JSON-RPC 2.0 MCP surface
  (initialize / ping / tools/list / tools/call with ``context.session``).

Nothing here verifies signatures cryptographically — verification is the real
platform's job; the doubles model the *wire behaviour* the SDK consumes
(expiry, tenant scoping, allowlists, error codes).  Test tokens are minted by
:func:`mint_session_token` and are clearly sample data.
"""

from __future__ import annotations

import base64
import json
import re
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional

from aosdk.model import decode_claims

# --------------------------------------------------------------------------- #
# Sample session-token minting (offline test data only)
# --------------------------------------------------------------------------- #
_DEFAULT_SIG = "offline-sample-signature"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint_session_token(
    tenant_id: str,
    *,
    subject: str = "user-1",
    subject_type: str = "user",
    role: Optional[str] = None,
    agent_id: Optional[str] = None,
    allowed_tools: Optional[List[str]] = None,
    ttl: int = 3600,
    now: Optional[float] = None,
) -> str:
    """Mint a JWT-shaped sample session token (sample data, never a real key)."""
    now = now if now is not None else time.time()
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    payload: Dict[str, Any] = {
        "iss": "https://auth.example.test",
        "sub": subject,
        "aud": "agent-orchestrator",
        "iat": int(now),
        "exp": int(now) + ttl,
        "jti": uuid.uuid4().hex,
        "tenantId": tenant_id,
        "subjectType": subject_type,
    }
    if role:
        payload["role"] = role
    if agent_id:
        payload["agentId"] = agent_id
    if allowed_tools is not None:
        payload["allowedTools"] = allowed_tools
    body = _b64url_encode(json.dumps(payload).encode("utf-8"))
    return f"{header}.{body}.{_DEFAULT_SIG}"


# --------------------------------------------------------------------------- #
# Envelope helpers
# --------------------------------------------------------------------------- #
def _request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


def ok_envelope(status: int, data: Any) -> Dict[str, Any]:
    return {"ok": True, "status": status, "requestId": _request_id(), "data": data, "error": None}


def err_envelope(status: int, code: str, message: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "requestId": _request_id(),
        "data": None,
        "error": {"code": code, "message": message, "details": None},
    }


class _AuthDenied(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message


def _authenticate(token: Optional[str]) -> Dict[str, Any]:
    """Return the token's claims or raise ``_AuthDenied`` (401)."""
    if not token:
        raise _AuthDenied(401, "unauthenticated", "missing bearer session token")
    try:
        claims = decode_claims(token)
    except ValueError as exc:
        raise _AuthDenied(401, "unauthenticated", "malformed session token") from exc
    exp = claims.get("exp")
    if exp is not None and time.time() >= int(exp):
        raise _AuthDenied(401, "unauthenticated", "session token expired")
    if not claims.get("tenantId"):
        raise _AuthDenied(401, "unauthenticated", "session token lacks tenantId")
    return claims


# --------------------------------------------------------------------------- #
# Gateway backend double (issue #16 dispatch envelope + streaming)
# --------------------------------------------------------------------------- #
# (agent_id, task_type) -> (outcome, content, provider, model)
_GATEWAY_ROWS: Dict[tuple, tuple] = {
    ("coder-agent", "code-review-verdict"): (
        "success",
        {"verdict": "approve", "summary": "change is sound", "confidence": 0.94},
        "deepseek",
        "deepseek-chat",
    ),
    ("reviewer-agent", "summarize"): (
        "success",
        {"summary": "billing outage resolved", "highlights": ["root cause found"]},
        "anthropic",
        "claude-sonnet",
    ),
    ("cache-agent", "classify-route"): (
        "cache_hit",
        {"label": "billing", "confidence": 0.99},
        None,
        None,
    ),
    ("coder-agent", "classify-route"): ("denied", None, None, None),
    ("blocked-agent", "classify-route"): ("blocked", None, None, None),
}

_STREAM_STAGES = ["received", "agent_resolved", "task_resolved", "route_selected", "guard", "completed"]


class FakeGatewayBackend:
    """Offline double of ``POST /v1/agents/{agentId}/tasks`` (+ streaming)."""

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        match = re.fullmatch(r"/v1/agents/([^/]+)/tasks", path or "")
        if method != "POST" or not match:
            return err_envelope(404, "unknown_route", "no such public route")
        try:
            claims = _authenticate(token)
        except _AuthDenied as exc:
            return err_envelope(exc.status, exc.code, exc.message)
        body = body or {}
        if claims.get("tenantId") != body.get("tenantId"):
            return err_envelope(403, "scope_denied", "cross-tenant dispatch refused")
        agent_id = match.group(1)
        task_type = str(body.get("taskType") or "")
        outcome, content, provider, model = _GATEWAY_ROWS.get(
            (agent_id, task_type), ("failed", None, None, None)
        )
        return self._envelope(agent_id, task_type, claims, outcome, content, provider, model)

    def request_stream(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Iterator[Dict[str, Any]]:
        terminal = self.request(method, path, body=body, query=query, token=token, headers=headers)
        if "result" not in terminal:
            yield terminal
            return
        request_id = terminal["result"].get("requestId", "req_unknown")
        for stage in _STREAM_STAGES:
            yield {"event": {"requestId": request_id, "stage": stage, "data": {"stage": stage}}}
        yield terminal

    @staticmethod
    def _envelope(
        agent_id: str,
        task_type: str,
        claims: Dict[str, Any],
        outcome: str,
        content: Any,
        provider: Optional[str],
        model: Optional[str],
    ) -> Dict[str, Any]:
        from aosdk.model import OUTCOME_STATUS

        request_id = f"req_{uuid.uuid4().hex[:12]}"
        tenant_id = claims.get("tenantId")
        result = {
            "requestId": request_id,
            "tenantId": tenant_id,
            "agentId": agent_id,
            "taskType": task_type,
            "outcome": outcome,
            "content": content,
            "provider": provider,
            "model": model,
            "tier": "LOW" if provider else None,
            "inputTokens": 120,
            "outputTokens": 40,
            "latencyMs": 210.0,
            "error": None if content is not None else f"outcome {outcome}",
        }
        record = {
            "requestId": request_id,
            "ts": "2026-09-08T00:00:00Z",
            "tenantId": tenant_id,
            "agentId": agent_id,
            "taskType": task_type,
            "capability": "code-review",
            "taskClass": task_type,
            "tier": "LOW" if provider else None,
            "provider": provider,
            "model": model,
            "outcome": outcome,
            "inputTokens": 120,
            "outputTokens": 40,
            "tokens": 160,
            "latencyMs": 210.0,
            "estimatedCostUsd": 0.0012,
            "budgetAction": "allow",
            "attempts": 1,
            "error": None if content is not None else f"outcome {outcome}",
        }
        return {"status": OUTCOME_STATUS[outcome], "result": result, "record": record}


# --------------------------------------------------------------------------- #
# Control-plane double (issue #38 envelope over #37/#38 routes)
# --------------------------------------------------------------------------- #
_USAGE: Dict[str, Dict[str, Any]] = {
    "acme": {
        "tenantId": "acme",
        "period": "2026-09",
        "calls": 42,
        "inputTokens": 4800,
        "outputTokens": 2100,
        "estimatedCostUsd": 0.31,
        "budget": {"limitUsd": 50.0, "spentUsd": 0.31, "action": "observe", "warnAtPct": 80.0, "status": "ok"},
    },
    "globex": {
        "tenantId": "globex",
        "period": "2026-09",
        "calls": 7,
        "inputTokens": 900,
        "outputTokens": 300,
        "estimatedCostUsd": 0.05,
        "budget": {"limitUsd": 20.0, "spentUsd": 0.05, "action": "observe", "warnAtPct": 80.0, "status": "ok"},
    },
}

_AUDIT_TEMPLATE = [
    {"seq": 1, "ts": "2026-09-08T00:00:01Z", "actor": "user:alice", "action": "agent.register", "resource": "agent:coder-agent", "tenantId": "acme", "detail": {"profile": "coder"}},
    {"seq": 2, "ts": "2026-09-08T00:00:02Z", "actor": "user:alice", "action": "task.dispatch", "resource": "agent:coder-agent", "tenantId": "acme", "detail": {"taskType": "code-review-verdict"}},
    {"seq": 3, "ts": "2026-09-08T00:00:03Z", "actor": "user:bob", "action": "agent.register", "resource": "agent:reviewer-agent", "tenantId": "globex", "detail": {"profile": "reviewer"}},
]

_POLICIES: Dict[str, Dict[str, Any]] = {
    "worker-bundle": {
        "policyId": "worker-bundle",
        "name": "worker-bundle",
        "bundle": "worker-bundle",
        "version": "1.0.0",
        "tenantId": "acme",
        "enabled": True,
        "controls": ["no-secrets", "verify-before-done", "no-direct-push"],
    },
    "reviewer-bundle": {
        "policyId": "reviewer-bundle",
        "name": "reviewer-bundle",
        "bundle": "reviewer-bundle",
        "version": "1.0.0",
        "tenantId": "acme",
        "enabled": True,
        "controls": ["evidence-gates", "independent-verification"],
    },
}

_ROUTE_PERMISSION = {
    ("GET", "usage"): "budget:read",
    ("GET", "audit"): "audit:read",
    ("GET", "policy"): "policy:read",
}


def _permissions(role: Optional[str]) -> set:
    if role in ("owner", "admin"):
        return {"budget:read", "audit:read", "policy:read", "org:read"}
    return {"budget:read", "audit:read"}


class FakeControlPlane:
    """Offline double of the usage / audit / policy control-plane routes."""

    def __init__(self) -> None:
        self.usage: Dict[str, Dict[str, Any]] = {k: dict(v) for k, v in _USAGE.items()}
        self.audit = [dict(rec) for rec in _AUDIT_TEMPLATE]
        self.policies: Dict[str, Dict[str, Any]] = {k: dict(v) for k, v in _POLICIES.items()}

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        try:
            claims = _authenticate(token)
        except _AuthDenied as exc:
            return err_envelope(exc.status, exc.code, exc.message)
        tenant = claims.get("tenantId")
        role = claims.get("role")
        perms = _permissions(role)
        query = query or {}

        # usage (tenant-scoped + public me route)
        usage_match = re.fullmatch(r"/v1/tenants/([^/]+)/usage", path or "")
        if method == "GET" and usage_match:
            path_tenant = usage_match.group(1)
            if path_tenant == "me":
                return ok_envelope(200, self.usage.get(tenant, _empty_usage(tenant)))
            if path_tenant != tenant:
                return err_envelope(403, "scope_denied", "cross-tenant usage refused")
            if "budget:read" not in perms:
                return err_envelope(403, "permission_denied", "role lacks budget:read")
            return ok_envelope(200, self.usage.get(tenant, _empty_usage(tenant)))

        # audit export (public me route) + audit query (backend)
        if method == "GET" and path.rstrip("/").endswith("/audit/export"):
            if "audit:read" not in perms:
                return err_envelope(403, "permission_denied", "role lacks audit:read")
            records = [r for r in self.audit if r.get("tenantId") == tenant]
            action = query.get("action")
            if action:
                records = [r for r in records if r.get("action") == action]
            return ok_envelope(200, {"items": records, "count": len(records)})
        if method == "GET" and path.rstrip("/").endswith("/audit"):
            if "audit:read" not in perms:
                return err_envelope(403, "permission_denied", "role lacks audit:read")
            records = [r for r in self.audit if r.get("tenantId") == tenant]
            action = query.get("action")
            if action:
                records = [r for r in records if r.get("action") == action]
            return ok_envelope(200, {"items": records, "count": len(records)})

        # policies (backend routes)
        policy_list = re.fullmatch(r"/v1/policies", path or "")
        if method == "GET" and policy_list:
            if "policy:read" not in perms:
                return err_envelope(403, "permission_denied", "role lacks policy:read")
            listed = [p for p in self.policies.values() if p.get("tenantId") == tenant]
            return ok_envelope(200, {"items": listed, "count": len(listed)})
        policy_get = re.fullmatch(r"/v1/policies/([^/]+)", path or "")
        if method == "GET" and policy_get:
            if "policy:read" not in perms:
                return err_envelope(403, "permission_denied", "role lacks policy:read")
            policy = self.policies.get(policy_get.group(1))
            if not policy or policy.get("tenantId") != tenant:
                return err_envelope(404, "not_found", "no such policy")
            return ok_envelope(200, policy)

        return err_envelope(404, "unknown_route", "no such public route")


def _empty_usage(tenant_id: str) -> Dict[str, Any]:
    return {
        "tenantId": tenant_id,
        "period": "2026-09",
        "calls": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "estimatedCostUsd": 0.0,
        "budget": None,
    }


# --------------------------------------------------------------------------- #
# MCP double (issue #20 JSON-RPC surface)
# --------------------------------------------------------------------------- #
_MCP_TOOLS = [
    {"name": "code.definitions", "description": "Where a symbol is defined in the tenant code index.", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}},
    {"name": "code.references", "description": "Resolved call sites of a symbol.", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}},
    {"name": "code.search", "description": "Substring search over symbol names.", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
    {"name": "kb.query", "description": "Query the tenant code/KB graph.", "inputSchema": {"type": "object", "properties": {"module_id": {"type": "string"}}}},
    {"name": "kb.freshness", "description": "Read-only freshness of one tenant repo.", "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}, "required": ["repo"]}},
    {"name": "kb.summary", "description": "Counts + shape of the tenant KB snapshot.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "platform.whoami", "description": "Resolved tenant context of the session.", "inputSchema": {"type": "object", "properties": {}}},
]


class FakeMcpTransport:
    """Offline double of the issue #20 MCP tool gateway (JSON-RPC 2.0)."""

    def __init__(self) -> None:
        self.allowed_tools: Optional[List[str]] = None

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        payload = body or {}
        request_id = payload.get("id")
        rpc_method = payload.get("method")
        params = payload.get("params") or {}

        if rpc_method == "initialize":
            return self._result(request_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agent-orchestrator-mcp", "version": "0.1.0"},
            })
        if rpc_method == "ping":
            return self._result(request_id, {})

        context = params.get("context") or {}
        try:
            claims = _authenticate(context.get("session") or token)
        except _AuthDenied as exc:
            return self._error(request_id, -32000, exc.message)
        # No cross-tenant: an explicit context.tenantId must equal the session
        # tenant (issue #20).  tools/list may omit it (session only).
        declared_tenant = context.get("tenantId")
        if declared_tenant is not None and claims.get("tenantId") != declared_tenant:
            return self._error(request_id, -32002, "authz scope: tenant mismatch")

        if rpc_method == "tools/list":
            listed = self._narrow(list(_MCP_TOOLS), claims)
            return self._result(request_id, {"tools": listed})
        if rpc_method == "tools/call":
            name = params.get("name")
            declared = next((t for t in _MCP_TOOLS if t["name"] == name), None)
            if declared is None:
                return self._error(request_id, -32601, f"unknown tool: {name}")
            if name not in {t["name"] for t in self._narrow(_MCP_TOOLS, claims)}:
                return self._error(request_id, -32602, "tool not in session allowedTools")
            if name == "platform.whoami":
                text = json.dumps({"tenantId": claims.get("tenantId"), "subject": claims.get("sub")})
            else:
                text = json.dumps({"tool": name, "ok": True})
            return self._result(request_id, {"content": [{"type": "text", "text": text}], "isError": False})

        return self._error(request_id, -32601, f"method not found: {rpc_method}")

    @staticmethod
    def _narrow(tools: List[Dict[str, Any]], claims: Dict[str, Any]) -> List[Dict[str, Any]]:
        allowed = claims.get("allowedTools")
        if allowed is None:
            return tools
        return [t for t in tools if t["name"] in allowed]

    @staticmethod
    def _result(request_id: Any, result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
