"""agent-orchestrator consumer SDK (Python) — issue #41.

Typed, transport-injected SDK for the model-gateway dispatch surface
(issue #16), the control-plane usage / audit / policy surface (issues #37/#38)
and the tenant-scoped MCP tool gateway (issue #20).

Design rules (see ``control-plane/sdk/README.md`` for the full contract):

- **Offline by construction.**  Every client takes an injected transport; the
  tests exercise them against offline platform doubles implementing the
  #37/#38 route + envelope shapes.  No SDK test touches the network.
- **Auth = short-lived per-tenant session tokens** from ``AGENTORCH_SESSION_TOKEN``
  or an injected callback.  Never a hardcoded key.
- **Typed, fail closed.**  Gateway outcomes are a closed set; a non-served
  outcome never carries fabricated typed content.

Public surface:


---knowledge---
module_id: control-plane.sdk.python.aosdk
system: control-plane
app: sdk
solution_class: class
patterns: [package-contract, public-surface, offline-by-construction, fail-closed]
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: [aosdk.auth, aosdk.gateway, aosdk.controlplane, aosdk.mcp, aosdk.model, aosdk.errors, aosdk.transport]
invariants: "every client takes an injected transport so no SDK test touches the network, and a non-served gateway outcome never carries fabricated typed content"
gotchas: "auth is always a short-lived per-tenant session token from AGENTORCH_SESSION_TOKEN or an injected callback, never a hardcoded key"
related: ["#41"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from .auth import (
    DEFAULT_TOKEN_ENV,
    SessionToken,
    TokenSource,
    TokenProvider,
    token_from_env,
)
from .errors import (
    ApiError,
    ConfigurationError,
    McpError,
    PermissionDeniedError,
    ScopeDeniedError,
    SdkError,
    TaskNotServedError,
    TransportError,
    UnauthorizedError,
)
from .gateway import GatewayClient
from .controlplane import ControlPlaneClient
from .mcp import McpClient
from .model import (
    OUTCOMES,
    AuditRecord,
    DispatchEvent,
    GatewayCallRecord,
    PolicyBinding,
    TaskEnvelope,
    TaskRequest,
    TaskResult,
    ToolDefinition,
    ToolResult,
    UsageBudget,
    UsageReport,
)
from .transport import HttpTransport, StreamTransport, Transport

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # auth
    "DEFAULT_TOKEN_ENV",
    "SessionToken",
    "TokenProvider",
    "TokenSource",
    "token_from_env",
    # errors
    "SdkError",
    "ConfigurationError",
    "TransportError",
    "ApiError",
    "UnauthorizedError",
    "ScopeDeniedError",
    "PermissionDeniedError",
    "TaskNotServedError",
    "McpError",
    # transports
    "Transport",
    "StreamTransport",
    "HttpTransport",
    # clients
    "GatewayClient",
    "ControlPlaneClient",
    "McpClient",
    # model
    "OUTCOMES",
    "TaskRequest",
    "TaskResult",
    "TaskEnvelope",
    "DispatchEvent",
    "GatewayCallRecord",
    "UsageReport",
    "UsageBudget",
    "AuditRecord",
    "PolicyBinding",
    "ToolDefinition",
    "ToolResult",
]
