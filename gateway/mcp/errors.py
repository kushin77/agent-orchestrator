"""Gateway exception hierarchy (one class per enforcement layer).

Each failure type carries the JSON-RPC error code it maps to (protocol.py).
The gateway raises these inside ``tools/call`` handling and converts them to
error responses; a denial is an unconditional stop - there is no code path
that turns a block into a fallback that retries in another scope (the rbac
two-gate doctrine and the no-cross-tenant-fallback doctrine from the
capital-underwriting tenant-scoped MCP pattern).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .protocol import (
    AUTHN_FAILED,
    AUTHZ_DENIED,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    RATE_LIMITED,
    TENANT_CONTEXT_REQUIRED,
    TOOL_NOT_ALLOWED,
    JsonRpcError,
)


class GatewayError(Exception):
    """Base for gateway-raised failures; carries a protocol code + data."""

    code: int = INVALID_PARAMS

    def __init__(
        self,
        message: str,
        *,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.data = data

    def as_jsonrpc(self) -> JsonRpcError:
        return JsonRpcError(self.message, code=self.code, data=self.data)


class TenantContextRequired(GatewayError):
    """A tool call arrived without a tenant context (fail closed).

    Mirrors the capital-underwriting doctrine: a tenant-scoped tool refuses to
    run unscoped - an absent scope never means "any tenant".
    """

    code = TENANT_CONTEXT_REQUIRED


class AuthnError(GatewayError):
    """Authentication failed (invalid or expired session credential)."""

    code = AUTHN_FAILED


class InvalidCredentialError(AuthnError):
    """The session token is malformed or its signature does not verify."""


class SessionExpiredError(AuthnError):
    """The session token's ``exp`` has passed."""


class AuthzDenied(GatewayError):
    """The rbac scope or permission gate denied the call (403)."""

    code = AUTHZ_DENIED


class CrossTenantSessionDenied(AuthzDenied):
    """A verified session was used against a mismatched explicit tenant id.

    Mirrors the registry service's ``require_scope`` refusal and the rbac scope
    gate: a session minted for tenant A is refused for tenant B - there is no
    cross-tenant fallback (scope denial posture, 403).
    """


class ToolNotAllowedError(AuthzDenied):
    """The session's ``allowedTools`` allowlist does not name this tool."""

    code = TOOL_NOT_ALLOWED


class UnknownToolError(GatewayError):
    """The tool name is not a declared capability of this gateway.

    Answered with the JSON-RPC ``-32601`` method-not-found code (the CMR
    indexer MCP server pattern): unknown tools are rejected, never routed.
    """

    code = METHOD_NOT_FOUND


class RateLimitedError(GatewayError):
    """The per-(tenant, agent, tool) rate gate refused this call (429)."""

    code = RATE_LIMITED


class InvalidArgumentsError(GatewayError):
    """Tool arguments failed schema validation before dispatch."""

    code = INVALID_PARAMS
