"""identity.chat failure taxonomy - every refusal the chat surface can raise.

One class per crossing, each carrying a stable machine ``code`` and the HTTP
status it maps to. The taxonomy mirrors ``identity.cpapi.errors`` on purpose
(``401`` unauthorized / ``403`` forbidden / ``422`` refused / ``503``
approval_required) and :meth:`ChatError.as_api_error` converts onto that wire
vocabulary through the control plane's own helpers instead of duplicating it,
so the chat surface speaks the control-plane error language rather than a
parallel one.

Fail-closed is a property of this taxonomy, not a convention: there is no
``allow`` member and no ``fallback`` member, and every subclass is raised on a
boundary that would otherwise *widen* access (an unmapped identity, a foreign
tenant id, a mismatched conversation container, an unapproved action).
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ChatError(Exception):
    """Base refusal. ``code`` is a stable machine string; ``status`` an HTTP code."""

    code = "chat_error"
    status = 403

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details: Dict[str, Any] = dict(details)

    def as_api_error(self) -> Any:
        """The same refusal on the control plane's wire vocabulary."""
        from identity.cpapi import errors as cpapi_errors

        return cpapi_errors.ApiError(
            self.status, self.code, self.message, dict(self.details) or None
        )


# --- binding faults (400/500: the request or the deployment is malformed) ----


class InvalidScope(ChatError):
    """A credential scope that cannot name a conversation container."""

    code = "invalid_scope"
    status = 400


class InvalidIdentityMap(ChatError):
    """The declared external-identity map is malformed and cannot be trusted."""

    code = "invalid_identity_map"
    status = 500


class CredentialKeyMissing(ChatError):
    """No credential signing key was supplied.

    There is no default and no ephemeral fallback: a key is a deployment
    secret, and inventing one would mint credentials nobody can revoke.
    """

    code = "signing_key_required"
    status = 500


# --- front door (401) --------------------------------------------------------


class CredentialRequired(ChatError):
    """No credential / auth-gate session was presented at all."""

    code = "credential_required"
    status = 401


class FrontDoorRefused(ChatError):
    """The auth-gate identity itself was refused (signature, expiry, allowlist)."""

    code = "front_door_refused"
    status = 401


class InvalidChatCredential(ChatError):
    """The scoped chat credential is malformed or its signature does not verify."""

    code = "invalid_credential"
    status = 401


class ChatCredentialExpired(ChatError):
    """The credential's ``exp`` has passed."""

    code = "credential_expired"
    status = 401


class ChatSessionRevoked(ChatError):
    """The credential's ``jti`` is on the revocation list, while still unexpired."""

    code = "session_revoked"
    status = 401

    def as_api_error(self) -> Any:
        from identity.cpapi.errors import session_revoked

        return session_revoked()


# --- isolation (403) ---------------------------------------------------------


class CrossTenantRefused(ChatError):
    """A credential minted for tenant A was used against tenant B.

    Raised for a foreign ``tenantId`` in the request body, in a query
    parameter, on the auth-gate identity, or on the resource itself. There is
    no cross-tenant fallback: the refusal is the only outcome.
    """

    code = "cross_tenant"
    status = 403


class ConversationScopeMismatch(ChatError):
    """The credential's conversation scope disagrees with the memory container.

    The one container a conversation may touch is
    ``session:<tenant>:<agent>:<conversation>``; naming any other container,
    or presenting a credential whose scope does not cover the container, is a
    refusal that is counted (``ChatIsolation.cross_scope_denials``).
    """

    code = "conversation_scope_mismatch"
    status = 403


class UnmappedClientIdentity(ChatError):
    """The external client identity is not in the declared map.

    Refused, never defaulted: an unknown identity resolves to *no* tenant, and
    "no tenant" is not a tenant.
    """

    code = "unmapped_client_identity"
    status = 403


class AmbiguousClientIdentity(ChatError):
    """The external client identity resolves to more than one tenant.

    The binding contract is *exactly one* tenant; an ambiguous declaration is
    a refusal, not a first-match-wins lookup.
    """

    code = "ambiguous_client_identity"
    status = 403


class DirectWriteRefused(ChatError):
    """The chat surface tried to take a side effect without an approval.

    The chat path has no direct write: an action runs only through
    ``ChatApprovalRouter.execute`` with a matching, already-approved request.
    """

    code = "direct_write_refused"
    status = 403


# --- approvals (503) ---------------------------------------------------------


class ApprovalRequired(ChatError):
    """An action was proposed and is now waiting on an approver.

    The proposal has already been recorded by ``identity.cpapi``'s approval
    gate; nothing has executed. ``as_api_error`` returns the control plane's
    own ``approval_required`` object for this id, so the wire status and code
    are the merged lane's, not a second opinion.
    """

    code = "approval_required"
    status = 503

    def __init__(self, approval_id: str, message: str = "") -> None:
        super().__init__(
            message or "this action needs an approved authorization request",
            approvalId=approval_id,
        )
        self.approval_id = approval_id

    def as_api_error(self) -> Any:
        from identity.cpapi.errors import approval_required

        return approval_required(self.approval_id)


def detail(exc: BaseException) -> Optional[str]:
    """A refusal's machine code, for counters and gate output."""
    return getattr(exc, "code", None)
