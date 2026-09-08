"""JSON-RPC 2.0 wire shapes for the tenant-scoped MCP tool gateway.

The gateway speaks a minimal, in-process JSON-RPC surface adapted from the CMR
indexer MCP server pattern (``.research/CMR/catalog/indexer/mcp_server.py``):
one JSON object per line, ``initialize`` / ``ping`` / ``tools/list`` /
``tools/call`` methods, an unknown tool answered with the JSON-RPC
``-32601`` method-not-found error, and a tool result carried as a text
content item. Notifications (``id`` is absent) produce no reply.

Transport error codes follow JSON-RPC 2.0; enforcement failures (tenant
context, authentication, authorization, tool allowlist, rate limit) use the
reserved server-error range ``-32000..-32099`` and mirror the HTTP posture of
the cannibalized JWT + rate-limited MCP hub (``shared-services``
``mcp-hub/server/mcp-server.ts``): unauthenticated -> 401, denied -> 403,
rate-limited -> 429.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# Wire / protocol identifiers.
JSONRPC_VERSION = "2.0"
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agent-orchestrator-mcp"
SERVER_VERSION = "0.1.0"

# MCP lifecycle + surface methods the gateway implements.
METHOD_INITIALIZE = "initialize"
METHOD_PING = "ping"
METHOD_TOOLS_LIST = "tools/list"
METHOD_TOOLS_CALL = "tools/call"
NOTIFICATION_INITIALIZED = "notifications/initialized"

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Gateway application error codes (JSON-RPC server-error range). Each maps to
# the HTTP posture of the cannibalized hub so a later transport can translate
# without reinterpretation:
#   tenant context required -> 401  (the tool refuses to run unscoped)
#   authn failed            -> 401  (missing/invalid/expired credential)
#   authz denied            -> 403  (rbac scope or permission gate)
#   tool not allowed        -> 403  (profile/session tool allowlist)
#   rate limited            -> 429
TENANT_CONTEXT_REQUIRED = -32001
AUTHN_FAILED = -32002
AUTHZ_DENIED = -32003
TOOL_NOT_ALLOWED = -32004
RATE_LIMITED = -32029


class JsonRpcError(Exception):
    """A JSON-RPC error that maps 1:1 onto an error response body.

    ``code`` is a JSON-RPC code (or a gateway application code above),
    ``message`` a short machine-readable reason, ``data`` optional additive
    context (e.g. an rbac ``Decision`` view for a denial).
    """

    def __init__(
        self,
        message: str,
        *,
        code: int = INTERNAL_ERROR,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_body(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            body["data"] = self.data
        return body


def result_body(message_id: Any, result: Any) -> Dict[str, Any]:
    """Build a JSON-RPC success response."""
    return {"jsonrpc": JSONRPC_VERSION, "id": message_id, "result": result}


def error_body(
    message_id: Any, error: JsonRpcError, *, code: Optional[int] = None
) -> Dict[str, Any]:
    """Build a JSON-RPC error response from a :class:`JsonRpcError`."""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": message_id,
        "error": JsonRpcError(
            error.message, code=code if code is not None else error.code,
            data=error.data,
        ).to_body(),
    }


def parse_message(message: Any) -> Tuple[str, Optional[Dict[str, Any]], Any]:
    """Validate one decoded message and return ``(method, params, id)``.

    Raises :class:`JsonRpcError` with ``INVALID_REQUEST`` on a malformed
    envelope (non-dict, missing ``jsonrpc``/``method``, non-2.0 version).
    A missing ``id`` marks a notification; callers that must reply check the
    returned id against ``None``.
    """
    if not isinstance(message, dict):
        raise JsonRpcError("message must be a JSON object", code=INVALID_REQUEST)
    if message.get("jsonrpc") != JSONRPC_VERSION:
        raise JsonRpcError(
            f"unsupported jsonrpc version {message.get('jsonrpc')!r}",
            code=INVALID_REQUEST,
        )
    method = message.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcError("method must be a non-empty string", code=INVALID_REQUEST)
    params = message.get("params")
    if params is not None and not isinstance(params, dict):
        raise JsonRpcError("params must be an object", code=INVALID_REQUEST)
    return method, params, message.get("id")


def tool_content(text: str, *, is_error: bool = False) -> Dict[str, Any]:
    """One MCP text content item (the only content type the gateway emits)."""
    item: Dict[str, Any] = {"type": "text", "text": text}
    if is_error:
        item["isError"] = True
    return item


def tool_result(text: str, *, is_error: bool = False) -> Dict[str, Any]:
    """The ``tools/call`` result envelope carrying one text content item."""
    return {"content": [tool_content(text, is_error=is_error)], "isError": is_error}
