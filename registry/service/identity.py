"""Per-agent identity issuance with tenant-scoped claims (issue #10).

Issues per-agent session credentials whose claims are **scoped** to exactly one
tenant: ``tenantId``, ``agentId``, ``role`` and the closed ``allowedTools`` set
(the issuing agent's profile tool allowlist). The credential is an HMAC-SHA256
signed token (JWT-shaped) whose claims cannot be altered without the signing
key, and whose tenant claim is enforced on every use.

The no-cross-tenant doctrine (capital-underwriting tenant-scoped MCP pattern;
rbac issue #12):

- issuance looks the agent up **only in the requested tenant** - an agent that
  exists in another tenant is simply unknown, so a tenant-A agent can never
  obtain a session claiming tenant B;
- ``require_scope`` refuses to use a session whose ``tenantId`` does not match
  the tenant it is being used in - there is no cross-tenant fallback;
- only ``active`` agents receive new sessions; paused/retired agents are
  refused.

This is the identity *issuance* contract. Authorization (whether a role may
perform a ``resource:action``) remains the rbac engine's job
(``identity/rbac``, issue #12); the session's ``role``/``allowedTools`` claims
are the snapshot a gateway/guardrail consumes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .errors import (
    AgentNotActiveError,
    CrossTenantDenied,
    IdentityError,
    InvalidCredentialError,
    SessionExpiredError,
    ToolNotAllowedError,
)
from .model import STATUS_ACTIVE
from .store import RegistryStore

ISSUER = "urn:agent-orchestrator:registry"
DEFAULT_AUDIENCE = ("control-plane",)
DEFAULT_ROLE = "agent"
DEFAULT_TTL_SECONDS = 3600
TOOL_CALL_CLAIM = "tool"

_JSON_KW = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}
_HEADER = {"alg": "HS256", "typ": "JWT"}


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


@dataclass(frozen=True)
class AgentSession:
    """A per-agent session identity with tenant-scoped claims.

    ``allowed_tools`` is the closed set of tool ids the session may invoke
    (from the issuing agent's profile tool allowlist).
    """

    issuer: str
    subject: str
    audience: Tuple[str, ...]
    issued_at: int
    expires_at: int
    token_id: str
    tenant_id: str
    agent_id: str
    role: str
    allowed_tools: Tuple[str, ...]

    def to_claims(self) -> Dict[str, object]:
        """Wire view of the claims (standard JWT names + scoped registry claims)."""
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

    def is_expired(self, now: Optional[int] = None) -> bool:
        return (now if now is not None else int(time.time())) >= self.expires_at

    def encode(self, signing_key: bytes) -> str:
        """Encode to an HMAC-SHA256 signed token (header.payload.signature)."""
        header_part = _b64url_encode(
            json.dumps(_HEADER, **_JSON_KW).encode("utf-8")
        )
        payload_part = _b64url_encode(
            json.dumps(self.to_claims(), **_JSON_KW).encode("utf-8")
        )
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        signature = _b64url_encode(
            hmac.new(signing_key, signing_input, hashlib.sha256).digest()
        )
        return f"{header_part}.{payload_part}.{signature}"

    @classmethod
    def decode(
        cls, token: str, signing_key: bytes, *, now: Optional[int] = None
    ) -> "AgentSession":
        """Decode and verify a token; returns the session it carries.

        Raises ``InvalidCredentialError`` on a malformed token or a signature
        mismatch, and ``SessionExpiredError`` once the token's ``exp`` has
        passed.
        """
        parts = token.split(".")
        if len(parts) != 3:
            raise InvalidCredentialError("malformed token: expected 3 segments")
        header_part, payload_part, signature_part = parts
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        expected = _b64url_encode(
            hmac.new(signing_key, signing_input, hashlib.sha256).digest()
        )
        if not hmac.compare_digest(expected, signature_part):
            raise InvalidCredentialError("token signature does not verify")
        try:
            payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidCredentialError("token payload is not valid JSON") from exc
        session = cls.from_claims(payload)
        if session.is_expired(now=now):
            raise SessionExpiredError("token has expired")
        return session

    @classmethod
    def from_claims(cls, claims: Dict[str, object]) -> "AgentSession":
        try:
            return cls(
                issuer=str(claims["iss"]),
                subject=str(claims["sub"]),
                audience=tuple(str(item) for item in claims.get("aud", ())),
                issued_at=int(claims["iat"]),
                expires_at=int(claims["exp"]),
                token_id=str(claims["jti"]),
                tenant_id=str(claims["tenantId"]),
                agent_id=str(claims["agentId"]),
                role=str(claims.get("role", DEFAULT_ROLE)),
                allowed_tools=tuple(str(item) for item in claims.get("allowedTools", ())),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidCredentialError(f"token claims are incomplete: {exc}") from exc


class IdentityService:
    """Issues and verifies tenant-scoped agent session credentials."""

    def __init__(
        self,
        store: RegistryStore,
        *,
        events: Optional[Any] = None,
        signing_key: Optional[bytes] = None,
    ) -> None:
        self._store = store
        self._events = events
        self._signing_key = signing_key

    # ------------------------------------------------------------------ #
    # issuance
    # ------------------------------------------------------------------ #
    def issue_session(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        role: Optional[str] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        signing_key: Optional[bytes] = None,
        now: Optional[int] = None,
        issuer: str = ISSUER,
        audience: Tuple[str, ...] = DEFAULT_AUDIENCE,
    ) -> AgentSession:
        """Mint a session credential for an agent of ``tenant_id`` (fail closed).

        - the agent is looked up only in ``tenant_id``; an agent registered in a
          different tenant is unknown here and raises ``UnknownAgentError``;
        - only ``active`` agents are issued new sessions;
        - ``allowedTools`` is taken from the agent's profile tool allowlist.
        """
        key = signing_key if signing_key is not None else self._signing_key
        if not key:
            raise IdentityError("no signing key configured for session issuance")
        agent = self._store.require_agent(tenant_id, agent_id)
        if agent.status != STATUS_ACTIVE:
            raise AgentNotActiveError(
                f"agent {agent_id!r} in tenant {tenant_id!r} is {agent.status!r}; "
                "only active agents receive new sessions (fail closed)"
            )
        now = now if now is not None else int(time.time())
        session = AgentSession(
            issuer=issuer,
            subject=agent_id,
            audience=audience,
            issued_at=now,
            expires_at=now + int(ttl_seconds),
            token_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            agent_id=agent_id,
            role=role or agent.role or DEFAULT_ROLE,
            allowed_tools=agent.tools,
        )
        if self._events is not None:
            self._events.append(
                "session",
                status="issued",
                tenant_id=tenant_id,
                agent_id=agent_id,
                actor=None,
                detail={"role": session.role, "ttlSeconds": int(ttl_seconds)},
            )
        return session

    # ------------------------------------------------------------------ #
    # verification + scoped use (no cross-tenant fallback)
    # ------------------------------------------------------------------ #
    def verify_token(
        self, token: str, signing_key: Optional[bytes] = None, *, now: Optional[int] = None
    ) -> AgentSession:
        """Decode + verify a credential (signature and expiry)."""
        key = signing_key if signing_key is not None else self._signing_key
        if not key:
            raise IdentityError("no signing key configured for token verification")
        return AgentSession.decode(token, key, now=now)

    def require_scope(self, session: AgentSession, tenant_id: str) -> AgentSession:
        """Refuse to use a session outside the tenant its claims name.

        This is the no-cross-tenant enforcement point: a session minted for
        tenant A is refused for tenant B, even though both may define identical
        roles or tools.
        """
        if session.tenant_id != tenant_id:
            raise CrossTenantDenied(session.tenant_id, tenant_id)
        return session

    def session_allows_tool(self, session: AgentSession, tool_id: str) -> bool:
        """Whether the session's scoped claim permits this tool id."""
        return tool_id in session.allowed_tools

    def authorize_tool_use(
        self, session: AgentSession, tenant_id: str, tool_id: str
    ) -> bool:
        """Compose scope then tool checks (the every-tool-call gate).

        Order matters and mirrors the tenant-scoped MCP pattern: the tenant
        scope is checked first (``CrossTenantDenied``), then the tool must be in
        the session's allowed set (``ToolNotAllowedError``). A denial is an
        unconditional stop - there is no fallback to a wider scope.
        """
        self.require_scope(session, tenant_id)
        if not self.session_allows_tool(session, tool_id):
            raise ToolNotAllowedError(
                f"tool {tool_id!r} is not in session {session.token_id!r}'s "
                f"allowed set for tenant {tenant_id!r}"
            )
        return True
