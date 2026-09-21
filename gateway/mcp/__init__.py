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
- ``SourceCatalog`` (sources) - the read-only authority readers behind the
  enterprise family; every read is stamped with the authority's own revision,
  and a ``fixture_only`` source is refused on a production path (issue #504).
- ``GroundingAssembler`` / ``CitationsEnvelope`` (grounding) - the static-first
  cited prefix a grounded chat turn is built from, consuming the
  ``engine/memory/prompt_cache`` discipline (issue #504).
- ``enterprise_handlers`` / ``enterprise_schemas`` (enterprise) - the ten
  read-only enterprise tools (eight new + ``kb.query`` / ``kb.freshness``)
  composed beside the seven base declarations (issue #504).

The package is importable as ``mcp`` when ``gateway/`` is on ``sys.path``
(the tests arrange this in ``tests/conftest.py``, prepending ``gateway/`` so
this package - not any third-party ``mcp`` - is what resolves).

---knowledge---
module_id: gateway.mcp
system: gateway
app: mcp
solution_class: class
patterns: [package-contract, public-surface, delegate-never-re-derive]
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: [MCPToolGateway, build_gateway, ToolRegistry, HashChainAuditLog, SessionIdentity]
invariants: "every tools/call is tenant-scoped and enforced before any tool acts, and the package re-implements no enforcement layer of its own"
gotchas: "the surface is in-process and offline: one JSON object per line, no transport"
related: ["#20"]
do_not_duplicate: null
---knowledge---
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
from .enterprise import (
    CHAT_FAMILY_NAMES,
    ENTERPRISE_TOOL_NAMES,
    REUSED_READ_TOOLS,
    WRITE_TOOLS,
    enterprise_descriptions,
    enterprise_handlers,
    enterprise_schemas,
)
from .gateway import MCPToolGateway
from .grounding import (
    ApprovalProposal,
    Citation,
    CitationsEnvelope,
    GroundedTurn,
    GroundingAssembler,
    GroundingError,
    GroundingRequest,
    Need,
    TokenBudget,
    propose_approval,
)
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
from .outbound import (
    DEFAULT_OUTBOUND_ENABLED,
    DRAWIO_SERVER_ID,
    OUTBOUND_CALL_PERMISSION,
    OUTBOUND_EVENT_KINDS,
    OUTBOUND_FLAG_ENV,
    OUTBOUND_STATUSES,
    DeclaredOfflineProbe,
    DuplicateServerError,
    EndpointOverrideError,
    HealthProbe,
    OutboundAuditLog,
    OutboundError,
    OutboundOutcome,
    OutboundRegistry,
    OutboundServer,
    OutboundUnreachableError,
    PinViolationError,
    UnknownServerError,
    build_outbound_registry,
    env_outbound_enabled,
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
from .sources import (
    MODE_FIXTURE,
    MODE_PRODUCTION,
    STATUS_NO_DATA,
    STATUS_OK,
    TENANT_ARGUMENT_KEYS,
    TENANT_SCOPED_FAMILIES,
    AuthoritySource,
    Fragment,
    KbFixtureSource,
    ReadResult,
    SourceCatalog,
    default_repo_root,
)
from .tools import build_registry, declared_tools

__all__ = [
    "AUTHN_FAILED",
    "AUTHZ_DENIED",
    "ApprovalProposal",
    "AuditLogError",
    "AuditLogIntegrityError",
    "AuditSink",
    "AuthnError",
    "AuthoritySource",
    "AuthzDecision",
    "AuthzDenied",
    "CHAT_FAMILY_NAMES",
    "Citation",
    "CitationsEnvelope",
    "CrossTenantSessionDenied",
    "DEFAULT_OUTBOUND_ENABLED",
    "DEFAULT_ROLE",
    "DEFAULT_TTL_SECONDS",
    "DRAWIO_SERVER_ID",
    "DeclaredOfflineProbe",
    "DuplicateServerError",
    "ENTERPRISE_TOOL_NAMES",
    "EndpointOverrideError",
    "Fragment",
    "GatewayError",
    "HealthProbe",
    "OUTBOUND_CALL_PERMISSION",
    "OUTBOUND_EVENT_KINDS",
    "OUTBOUND_FLAG_ENV",
    "OUTBOUND_STATUSES",
    "OutboundAuditLog",
    "OutboundError",
    "OutboundOutcome",
    "OutboundRegistry",
    "OutboundServer",
    "OutboundUnreachableError",
    "PinViolationError",
    "UnknownServerError",
    "build_outbound_registry",
    "env_outbound_enabled",
    "GroundedTurn",
    "GroundingAssembler",
    "GroundingError",
    "GroundingRequest",
    "HashChainAuditLog",
    "ISSUER",
    "IndexBackend",
    "InvalidArgumentsError",
    "InvalidCredentialError",
    "KbFixtureSource",
    "KbRegistry",
    "LimitsRateGate",
    "MCPToolGateway",
    "MCP_ALLOWLIST_KEYS",
    "MODE_FIXTURE",
    "MODE_PRODUCTION",
    "MemoryKbBackend",
    "Need",
    "PermissionGuard",
    "PROTOCOL_VERSION",
    "RATE_LIMITED",
    "REUSED_READ_TOOLS",
    "RateDecision",
    "RateGate",
    "RateLimitedError",
    "RbacScopeGuard",
    "ReadResult",
    "RepoIndex",
    "SERVER_NAME",
    "SERVER_VERSION",
    "STATUS_NO_DATA",
    "STATUS_OK",
    "TENANT_ARGUMENT_KEYS",
    "TENANT_SCOPED_FAMILIES",
    "SessionExpiredError",
    "SessionIdentity",
    "SourceCatalog",
    "Symbol",
    "TENANT_CONTEXT_REQUIRED",
    "TOOL_CALL_PERMISSION",
    "TOOL_NOT_ALLOWED",
    "TenantContextRequired",
    "TenantKb",
    "TokenBudget",
    "ToolDefinition",
    "ToolNotAllowedError",
    "ToolRegistry",
    "UnknownToolError",
    "UnlimitedRateGate",
    "WRITE_TOOLS",
    "build_registry",
    "build_outbound_registry",
    "canonical",
    "declared_tools",
    "default_repo_root",
    "enterprise_descriptions",
    "enterprise_handlers",
    "enterprise_schemas",
    "mint_session",
    "now_utc",
    "propose_approval",
    "scope_for",
    "session_to_token",
    "tool_result",
    "verify_token",
]
