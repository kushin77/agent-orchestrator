"""identity.chat - scoped, tenant-isolated chat identity (issue #505).

The chat surface's identity layer for EPIC #500 (enterprise chat in the SPoG).
Five modules, each consuming a merged pillar contract rather than forking one:

``credential``
    Scoped ``(tenant, agent, conversation)`` credentials, minted and verified
    with the gateway's existing session-credential format and verifier
    (``gateway.mcp.authn``). No new token format, no new JOSE.

``binding``
    The declared external-client-identity -> tenant map. A client's own user
    table is advisory (ADR-0023); an unmapped identity is refused, never
    defaulted, and the role vocabulary is ours.

``frontdoor``
    The auth-gate identity, consumed exactly as ``portal/server/sso.py``
    consumes it: the RS256 ``os-session-token`` verified offline against the
    published JWKS, the ``ROOT_ADMIN_EMAILS`` allowlist deciding role, and a
    body/query ``tenantId`` treated as a *refusal*, never an authority.

``isolation``
    The chat path onto ``engine/memory``. A conversation's memory lives in
    exactly one ``session:<tenant>:<agent>:<conversation>`` container, the
    credential scope must match it, and a mismatch is refused and counted - the
    engine's own ``MemoryIsolationError`` is re-raised chained, never bypassed.

``approvals``
    Proposals route to ``identity.cpapi``'s approval gate. The chat surface has
    no direct write path and no approver of its own.

Fail-closed defaults, stated once: no anonymous mode, no tenant from the
request body, no cross-tenant fallback, no impersonation shortcut, and no
credential without a signing key and a revocation store.


---knowledge---
module_id: identity.chat
system: identity
app: chat
solution_class: class
patterns: [package-contract, public-surface, delegate-never-re-derive]
derives_from: null
owner_sme: security-sme
tier: L0
interfaces: [credential, binding, frontdoor, isolation, approvals, errors]
invariants: ""
gotchas: ""
related: ["#505", "#500"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from .approvals import ROUTER_PUBLIC_SURFACE, ChatApprovalRouter, Proposal
from .binding import (
    CHAT_ROLES,
    ClientBinding,
    IdentityMap,
    Resolution,
    load_default_map,
)
from .credential import (
    ChatCredential,
    ChatScope,
    assert_scope,
    conversation_of,
    mint_chat_credential,
    verify_chat_credential,
)
from .errors import (
    AmbiguousClientIdentity,
    ApprovalRequired,
    ChatCredentialExpired,
    ChatError,
    ChatSessionRevoked,
    ConversationScopeMismatch,
    CredentialKeyMissing,
    CredentialRequired,
    CrossTenantRefused,
    DirectWriteRefused,
    FrontDoorRefused,
    InvalidChatCredential,
    InvalidIdentityMap,
    InvalidScope,
    UnmappedClientIdentity,
)
from .frontdoor import (
    AuthGateIdentity,
    ChatFrontDoor,
    ChatRequest,
    assert_no_foreign_tenant,
)
from .isolation import ChatIsolation, conversation_container

__all__ = [
    "AmbiguousClientIdentity",
    "ApprovalRequired",
    "AuthGateIdentity",
    "CHAT_ROLES",
    "ChatApprovalRouter",
    "ChatCredential",
    "ChatCredentialExpired",
    "ChatError",
    "ChatFrontDoor",
    "ChatIsolation",
    "ChatRequest",
    "ChatScope",
    "ChatSessionRevoked",
    "ClientBinding",
    "ConversationScopeMismatch",
    "CredentialKeyMissing",
    "CredentialRequired",
    "CrossTenantRefused",
    "DirectWriteRefused",
    "FrontDoorRefused",
    "IdentityMap",
    "InvalidChatCredential",
    "InvalidIdentityMap",
    "InvalidScope",
    "Proposal",
    "ROUTER_PUBLIC_SURFACE",
    "Resolution",
    "UnmappedClientIdentity",
    "assert_no_foreign_tenant",
    "assert_scope",
    "conversation_container",
    "conversation_of",
    "load_default_map",
    "mint_chat_credential",
    "verify_chat_credential",
]
