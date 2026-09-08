"""gateway/mcp - the tenant-scoped MCP tool gateway (issue #20).

The MCP surface of the model-gateway pillar: an in-process, offline JSON-RPC
server that exposes the platform's declared tools (code/KB indexing +
platform informational tools) to external AI agents. Every ``tools/call`` is
tenant-scoped and enforced - tenant context, authN (session/JWT-shaped token),
authZ (rbac scope gate, consumed via an injected guard), session tool
allowlist, rate limit (injected gate) and a full append-only audit record per
call.

Public surface
--------------

- ``MCPToolGateway`` (gateway) - enforcement core + ``handle_message`` JSON-RPC
  surface; ``build_registry`` (tools) declares the tool catalog.
- ``RbacScopeGuard`` (authz) - consumes identity/rbac ``guard_session`` through
  the injected ``PermissionGuard`` seam.
- ``verify_token`` / ``mint_session`` (authn) - HS256 JWT-shaped tenant-scoped
  session credentials (claim vocabulary consumed from registry/service).
- ``HashChainAuditLog`` (audit) - append-only hash-chained tool-call ledger
  (record shape consumed from registry/events); injected as the ``AuditSink``.
- ``LimitsRateGate`` (rategate) - adapts the gateway/limits ``RateLimiter`` to
  the injected ``RateGate`` seam.
- ``KbRegistry`` / ``TenantKb`` (kb) - per-tenant fake code/KB index so the
  declared indexing tools are fully exercisable offline.

The package is importable as ``mcp`` when ``gateway/`` is on ``sys.path``
(the tests arrange this in ``tests/conftest.py``, prepending ``gateway/`` so
this package - not any third-party ``mcp`` - is what resolves).
"""

from .audit import (
    AuditLogError,
    AuditLogIntegrityError,
    AuditSink,
    HashChainAuditLog,
    now_utc,
)
from .authn import (
    DEFAULT_ROLE,
    DEFAULT_TTL_SECONDS,
    ISSUER,
    mint_session,
    session_to_token,
    verify_token,
)
from .authz import AuthzDecision, PermissionGuard, RbacScopeGuard
from .errors import (
    AuthnError,
    AuthzDenied,
    CrossTenantSessionDenied,
    GatewayError,
    InvalidArgumentsError,
    InvalidCredentialError,
    RateLimitedError,
    SessionExpiredError,
    TenantContextRequired,
    ToolNotAllowedError,
    UnknownToolError,
)
from .gateway import MCPToolGateway
from .kb import (
    IndexBackend,
    KbRegistry,
    MemoryKbBackend,
    RepoIndex,
    Symbol,
    TenantKb,
    canonical,
)
from .model import (
    MCP_ALLOWLIST_KEYS,
    TOOL_CALL_PERMISSION,
    SessionIdentity,
    ToolDefinition,
)
from .protocol import (
    AUTHN_FAILED,
    AUTHZ_DENIED,
    PROTOCOL_VERSION,
    RATE_LIMITED,
    SERVER_NAME,
    SERVER_VERSION,
    TENANT_CONTEXT_REQUIRED,
    TOOL_NOT_ALLOWED,
    tool_result,
)
from .rategate import LimitsRateGate, RateDecision, RateGate, UnlimitedRateGate, scope_for
from .registry import ToolRegistry
from .tools import build_registry, declared_tools

__all__ = [
    "AUTHN_FAILED",
    "AUTHZ_DENIED",
    "AuditLogError",
    "AuditLogIntegrityError",
    "AuditSink",
    "AuthnError",
    "AuthzDecision",
    "AuthzDenied",
    "CrossTenantSessionDenied",
    "DEFAULT_ROLE",
    "DEFAULT_TTL_SECONDS",
    "GatewayError",
    "HashChainAuditLog",
    "ISSUER",
    "IndexBackend",
    "InvalidArgumentsError",
    "InvalidCredentialError",
    "KbRegistry",
    "LimitsRateGate",
    "MCPToolGateway",
    "MCP_ALLOWLIST_KEYS",
    "MemoryKbBackend",
    "PermissionGuard",
    "PROTOCOL_VERSION",
    "RATE_LIMITED",
    "RateDecision",
    "RateGate",
    "RateLimitedError",
    "RbacScopeGuard",
    "RepoIndex",
    "SERVER_NAME",
    "SERVER_VERSION",
    "SessionExpiredError",
    "SessionIdentity",
    "Symbol",
    "TENANT_CONTEXT_REQUIRED",
    "TOOL_CALL_PERMISSION",
    "TOOL_NOT_ALLOWED",
    "TenantContextRequired",
    "TenantKb",
    "ToolDefinition",
    "ToolNotAllowedError",
    "ToolRegistry",
    "UnknownToolError",
    "UnlimitedRateGate",
    "build_registry",
    "canonical",
    "declared_tools",
    "mint_session",
    "now_utc",
    "scope_for",
    "session_to_token",
    "tool_result",
    "verify_token",
]
