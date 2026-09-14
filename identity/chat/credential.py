"""Scoped ``(tenant, agent, conversation)`` chat credentials.

Design decision (load-bearing): **no new token format and no new verifier.**
A chat credential *is* the gateway's session credential - minted with
``gateway.mcp.authn.mint_session`` / ``session_to_token`` and verified with
``gateway.mcp.authn.verify_token``, unchanged - whose claim vocabulary is the
frozen one (``iss`` / ``sub`` / ``aud`` / ``iat`` / ``exp`` / ``jti`` plus the
scoped ``tenantId`` / ``agentId`` / ``role`` / ``allowedTools``).

The conversation scope therefore has to ride a claim the existing verifier
already returns. It rides ``aud``: a credential minted for conversation ``C``
carries ``aud = ["control-plane", "chat:conversation:C"]``. Every other
standard claim is already spoken for - ``sub`` is the external principal,
``jti`` is the revocation handle, ``tenantId``/``agentId`` are the tenant and
agent - and a bespoke ``conversationId`` claim would be *silently dropped* by
``SessionIdentity.from_claims``, so the token would still verify while the
conversation scope vanished. Overloading ``aud`` keeps the scope verifiable by
the merged verifier and makes the credential non-replayable across
conversations.

What this module adds on top of the merged verifier is only the scope
assertion: the tenant, the agent, and the conversation the token was minted
for. That assertion is what ``isolation`` and ``approvals`` consume.

Revocation is not optional here. ``verify_chat_credential`` requires a
revocation store (the merged ``identity.sso.store`` jti deny list, probed
through the merged ``identity.sso.tokens.check_not_revoked``), so there is no
code path that verifies a credential without consulting the revocation
authority - a revoked-but-unexpired jti is refused at the front of the call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple

from gateway.mcp import authn
from gateway.mcp.errors import InvalidCredentialError, SessionExpiredError
from gateway.mcp.model import SessionIdentity

from .errors import (
    ChatCredentialExpired,
    ChatSessionRevoked,
    ConversationScopeMismatch,
    CredentialKeyMissing,
    InvalidChatCredential,
    InvalidScope,
)

#: The audience every chat credential carries alongside its conversation.
CHAT_AUDIENCE = "control-plane"

#: Prefix marking the conversation-scope audience member.
CONVERSATION_AUDIENCE_PREFIX = "chat:conversation:"

#: Default chat role for a credential minted without an explicit one.
DEFAULT_CHAT_ROLE = "chat-user"


def conversation_audience(conversation_id: str) -> str:
    """The ``aud`` member that names conversation ``conversation_id``."""
    return f"{CONVERSATION_AUDIENCE_PREFIX}{conversation_id}"


def audience_for(conversation_id: str) -> Tuple[str, ...]:
    """The full audience of a credential scoped to one conversation."""
    return (CHAT_AUDIENCE, conversation_audience(conversation_id))


def _require_scope_part(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidScope(f"{field_name} is required for a chat credential")
    return value.strip()


@dataclass(frozen=True)
class ChatScope:
    """The one ``(tenant, agent, conversation)`` triple a credential covers."""

    tenant_id: str
    agent_id: str
    conversation_id: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.tenant_id, "tenant_id"),
            (self.agent_id, "agent_id"),
            (self.conversation_id, "conversation_id"),
        ):
            _require_scope_part(value, field_name)

    @property
    def container_id(self) -> str:
        """The single ``engine/memory`` container this conversation owns."""
        from .isolation import conversation_container

        return conversation_container(
            self.tenant_id, self.agent_id, self.conversation_id
        )

    def covers(self, other: "ChatScope") -> bool:
        """Whether this scope is exactly ``other`` (no widening, no nesting)."""
        return self == other


@dataclass(frozen=True)
class ChatCredential:
    """A verified credential: its scope, the session it came from, its token."""

    scope: ChatScope
    session: SessionIdentity
    token: str

    @property
    def tenant_id(self) -> str:
        return self.scope.tenant_id

    @property
    def agent_id(self) -> str:
        return self.scope.agent_id

    @property
    def conversation_id(self) -> str:
        return self.scope.conversation_id

    @property
    def subject(self) -> str:
        return self.session.subject

    @property
    def role(self) -> str:
        return self.session.role

    @property
    def container_id(self) -> str:
        return self.scope.container_id

    @property
    def jti(self) -> str:
        return self.session.token_id


def _require_signing_key(signing_key: Optional[bytes]) -> bytes:
    if not signing_key or not isinstance(signing_key, (bytes, bytearray)):
        raise CredentialKeyMissing(
            "a chat credential signing key is required; there is no default "
            "and no ephemeral fallback"
        )
    return bytes(signing_key)


def mint_chat_credential(
    *,
    tenant_id: str,
    agent_id: str,
    conversation_id: str,
    signing_key: Optional[bytes],
    role: str = DEFAULT_CHAT_ROLE,
    allowed_tools: Sequence[str] = (),
    subject: Optional[str] = None,
    ttl_seconds: int = authn.DEFAULT_TTL_SECONDS,
    now: Optional[int] = None,
) -> ChatCredential:
    """Mint a credential scoped to exactly one ``(tenant, agent, conversation)``.

    The tenant, the agent and the conversation are **arguments from the
    trusted caller** - the front door resolves them from a declared binding -
    and never from a request body. ``signing_key`` is mandatory.
    """
    scope = ChatScope(
        tenant_id=_require_scope_part(tenant_id, "tenant_id"),
        agent_id=_require_scope_part(agent_id, "agent_id"),
        conversation_id=_require_scope_part(conversation_id, "conversation_id"),
    )
    key = _require_signing_key(signing_key)
    if not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise InvalidScope("ttl_seconds must be a positive integer")

    session = authn.mint_session(
        scope.tenant_id,
        scope.agent_id,
        key,
        role=role,
        allowed_tools=tuple(allowed_tools),
        subject=subject,
        ttl_seconds=ttl_seconds,
        now=now,
        audience=audience_for(scope.conversation_id),
    )
    return ChatCredential(
        scope=scope,
        session=session,
        token=authn.session_to_token(session, key),
    )


def conversation_of(session: SessionIdentity) -> str:
    """The conversation a verified session is scoped to, or ``""`` if none."""
    for member in session.audience:
        if member.startswith(CONVERSATION_AUDIENCE_PREFIX):
            return member[len(CONVERSATION_AUDIENCE_PREFIX):]
    return ""


def verify_chat_credential(
    token: str,
    signing_key: Optional[bytes],
    *,
    revocation_store: Any,
    conversation_id: Optional[str] = None,
    now: Optional[int] = None,
) -> ChatCredential:
    """Verify a chat credential with the merged verifier plus the scope assertion.

    ``revocation_store`` is required: verification always consults the merged
    jti deny list (``identity.sso.store`` via ``identity.sso.tokens``), so an
    unexpired-but-revoked credential is refused. Passing ``conversation_id``
    additionally requires the credential to be scoped to exactly that
    conversation; a credential minted for another conversation is a
    :class:`ConversationScopeMismatch`, never a widened read.
    """
    key = _require_signing_key(signing_key)
    if not token or not isinstance(token, str):
        raise InvalidChatCredential("a chat credential token is required")
    if revocation_store is None:
        raise InvalidChatCredential(
            "a revocation store is required; the chat path never verifies "
            "without consulting the revocation authority"
        )
    now_i = int(now if now is not None else time.time())
    try:
        session = authn.verify_token(token, key, now=now_i)
    except SessionExpiredError as exc:
        raise ChatCredentialExpired("chat credential has expired") from exc
    except InvalidCredentialError as exc:
        raise InvalidChatCredential(f"chat credential refused: {exc}") from exc

    _assert_not_revoked(revocation_store, session)

    token_conversation = conversation_of(session)
    if not token_conversation:
        raise InvalidChatCredential(
            "chat credential carries no conversation scope"
        )
    if conversation_id is not None and conversation_id != token_conversation:
        raise ConversationScopeMismatch(
            f"credential is scoped to conversation {token_conversation!r}, "
            f"not {conversation_id!r}"
        )
    try:
        scope = ChatScope(
            tenant_id=session.tenant_id,
            agent_id=session.agent_id,
            conversation_id=token_conversation,
        )
    except InvalidScope as exc:
        raise InvalidChatCredential(str(exc)) from exc
    return ChatCredential(scope=scope, session=session, token=token)


def _assert_not_revoked(revocation_store: Any, session: SessionIdentity) -> None:
    """Refuse a session whose ``jti`` is revoked, through the merged helper."""
    from identity.sso.tokens import check_not_revoked

    try:
        check_not_revoked(revocation_store, session.to_claims())
    except Exception as exc:  # noqa: BLE001 - a revoked session is a refusal
        raise ChatSessionRevoked(
            f"chat credential {session.token_id or '<no jti>'} has been revoked"
        ) from exc


def assert_scope(
    credential: ChatCredential,
    *,
    tenant_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> None:
    """Assert the credential covers the named scope, refusing any crossing.

    Called with the ids a request *claims* to act in. A tenant mismatch is a
    :class:`CrossTenantRefused`; an agent or conversation mismatch is a
    :class:`ConversationScopeMismatch`.
    """
    from .errors import CrossTenantRefused

    if tenant_id is not None and tenant_id != credential.tenant_id:
        raise CrossTenantRefused(
            f"credential is scoped to tenant {credential.tenant_id!r}, "
            f"not {tenant_id!r}"
        )
    if agent_id is not None and agent_id != credential.agent_id:
        raise ConversationScopeMismatch(
            f"credential is scoped to agent {credential.agent_id!r}, "
            f"not {agent_id!r}"
        )
    if conversation_id is not None and conversation_id != credential.conversation_id:
        raise ConversationScopeMismatch(
            f"credential is scoped to conversation {credential.conversation_id!r}, "
            f"not {conversation_id!r}"
        )
