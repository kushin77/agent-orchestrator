"""Shared data contracts for the tenant-scoped MCP tool gateway.

``SessionIdentity`` is the verified, tenant-scoped principal the gateway acts
for on every call. Its claim vocabulary - ``tenantId`` / ``agentId`` / ``role``
/ ``allowedTools`` camelCase wire names plus the standard ``iss`` / ``sub`` /
``aud`` / ``iat`` / ``exp`` / ``jti`` - is consumed from the registry service
identity contract (issue #10, ``registry/service/identity.py``) and from the
rbac session role snapshot (issue #12); it is not redefined here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

# The rbac permission every tool call must hold at the session's node (the
# identity/rbac middleware contract: "every tool/API call ... passes through
# guard_session(store, session, 'tool:call')").
TOOL_CALL_PERMISSION = "tool:call"

# Closed tool-allowlist vocabulary of the MCP surface. Each id names a tool the
# gateway declares (issue #20 owns this contract); a session whose
# ``allowedTools`` does not contain the requested id is denied (fail closed).
# This is the external-MCP allowlist, distinct from (and complementary to) the
# agent-internal tool vocabulary in registry/profiles/catalog.yaml.
MCP_ALLOWLIST_KEYS = (
    "code.definitions",
    "code.references",
    "code.search",
    "kb.query",
    "kb.freshness",
    "kb.summary",
    "platform.whoami",
)


@dataclass(frozen=True)
class SessionIdentity:
    """A verified tenant-scoped session identity.

    ``tenant_id`` is the one tenant the session may act in; ``agent_id`` the
    managed agent it impersonates; ``role`` its role snapshot; ``allowed_tools``
    the closed set of tool ids it may invoke. ``subject`` is the external
    principal that owns the session (the rbac subject that holds bindings).
    """

    tenant_id: str
    agent_id: str
    role: str = "agent"
    allowed_tools: Tuple[str, ...] = ()
    subject: str = ""
    issuer: str = "urn:agent-orchestrator:mcp"
    audience: Tuple[str, ...] = ("control-plane",)
    issued_at: int = 0
    expires_at: int = 0
    token_id: str = ""

    def __post_init__(self) -> None:
        if not self.subject:
            object.__setattr__(self, "subject", self.agent_id)
        object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))
        object.__setattr__(self, "audience", tuple(self.audience))

    def allows(self, allowlist_id: str) -> bool:
        """Whether the session may invoke the tool named ``allowlist_id``."""
        return allowlist_id in self.allowed_tools

    @property
    def scope_key(self) -> str:
        """Rate-limit scope for this principal: ``tenant:agent``."""
        return f"{self.tenant_id}:{self.agent_id}"

    def to_claims(self) -> Dict[str, object]:
        """Wire view of the claims (JWT-standard names + scoped claims)."""
        return {
            "iss": self.issuer,
            "sub": self.subject,
            "aud": list(self.audience),
            "iat": self.issued_at,
            "exp": self.expires_at,
            "jti": self.token_id,
            "tenantId": self.tenant_id,
            "agentId": self.agent_id,
            "role": self.role,
            "allowedTools": list(self.allowed_tools),
        }

    @classmethod
    def from_claims(cls, claims: Dict[str, object]) -> "SessionIdentity":
        missing = [k for k in ("tenantId", "agentId") if k not in claims]
        if missing:
            raise ValueError(f"token claims are incomplete: missing {missing}")
        return cls(
            tenant_id=str(claims["tenantId"]),
            agent_id=str(claims["agentId"]),
            role=str(claims.get("role", "agent")),
            allowed_tools=tuple(str(i) for i in claims.get("allowedTools", ())),
            subject=str(claims.get("sub", "")),
            issuer=str(claims.get("iss", "urn:agent-orchestrator:mcp")),
            audience=tuple(str(i) for i in claims.get("aud", ())),
            issued_at=int(claims.get("iat", 0)),
            expires_at=int(claims.get("exp", 0)),
            token_id=str(claims.get("jti", "")),
        )

    def _jwt_payload(self) -> Dict[str, object]:
        payload = dict(self.to_claims())
        payload.pop("sub", None)
        payload["sub"] = self.subject or self.agent_id
        return payload


@dataclass(frozen=True)
class ToolDefinition:
    """A declared capability: name, description, JSON schema, handler.

    ``handler(args, session, backend) -> dict`` runs tenant-scoped: the
    gateway passes the per-tenant index ``backend`` bound to
    ``session.tenant_id`` so no argument can select another tenant's data.
    """

    name: str
    description: str
    input_schema: Dict[str, object]
    handler: object = field(compare=False, repr=False)
    allowlist_id: str = ""
    permission: str = TOOL_CALL_PERMISSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowlist_id", self.allowlist_id or self.name)

    def schema(self) -> Dict[str, object]:
        """The ``tools/list`` schema entry (MCP tool shape)."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "allowlistId": self.allowlist_id,
        }
