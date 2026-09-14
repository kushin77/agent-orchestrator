"""Scoped ``(tenant, agent, conversation)`` credentials: minting, verification.

Every acceptance criterion here is paired with the control that proves the
assertion is not vacuous: a refusal test sits beside the same call succeeding
for the correct scope.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.mcp import authn
from identity.chat import credential as credential_mod
from identity.chat.credential import (
    ChatCredential,
    ChatScope,
    assert_scope,
    conversation_of,
    mint_chat_credential,
    verify_chat_credential,
)
from identity.chat.errors import (
    ChatCredentialExpired,
    ChatSessionRevoked,
    ConversationScopeMismatch,
    CredentialKeyMissing,
    CrossTenantRefused,
    InvalidChatCredential,
    InvalidScope,
)

from identity.sso.store import InMemoryStore

# Local constants (deliberately not imported from a sibling module: two suites
# with a same-named module would collide when the repo runs in one invocation).
SIGNING_KEY = bytes(range(32))
NOW = 1_800_000_000
TENANT_A = "tenant-acme"
TENANT_B = "tenant-globex"
AGENT_A = "coder"
AGENT_B = "analyst"
CONVERSATION_1 = "conv-1"
CONVERSATION_2 = "conv-2"


def _fresh_revocation_store() -> InMemoryStore:
    """A fresh merged jti deny list (nothing revoked)."""
    return InMemoryStore()


def test_minted_credential_round_trips_through_the_gateway_verifier(
    credential, signing_key, now
):
    """The conversation scope survives the *unmodified* gateway verifier."""
    session = authn.verify_token(credential.token, signing_key, now=now + 1)
    assert session.tenant_id == TENANT_A
    assert session.agent_id == AGENT_A
    assert credential_mod.conversation_audience(CONVERSATION_1) in session.audience
    assert conversation_of(session) == CONVERSATION_1


def test_credential_module_does_not_fork_the_gateway_crypto():
    """No parallel JOSE: the token format and verifier are the gateway's."""
    assert credential_mod.authn is authn
    assert credential_mod.authn.session_to_token is authn.session_to_token
    assert credential_mod.authn.verify_token is authn.verify_token
    source = Path(credential_mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("import hmac", "import base64", "import jwt", "def _sign"):
        assert forbidden not in source, f"credential.py re-implements {forbidden!r}"


def test_verification_recovers_the_exact_scope(credential):
    """Verification returns the same triple that was minted."""
    verified = verify_chat_credential(
        credential.token,
        SIGNING_KEY,
        revocation_store=_fresh_revocation_store(),
        now=NOW + 1,
    )
    assert isinstance(verified, ChatCredential)
    assert verified.scope == ChatScope(TENANT_A, AGENT_A, CONVERSATION_1)
    assert verified.container_id == f"session:{TENANT_A}:{AGENT_A}:{CONVERSATION_1}"


def test_credential_for_another_conversation_is_refused(credential):
    """A credential for conv-1 cannot be presented as a conv-2 credential."""
    with pytest.raises(ConversationScopeMismatch) as caught:
        verify_chat_credential(
            credential.token,
            SIGNING_KEY,
            revocation_store=_fresh_revocation_store(),
            conversation_id=CONVERSATION_2,
            now=NOW + 1,
        )
    assert caught.value.code == "conversation_scope_mismatch"
    # Control: the same call naming the credential's own conversation verifies.
    assert verify_chat_credential(
        credential.token,
        SIGNING_KEY,
        revocation_store=_fresh_revocation_store(),
        conversation_id=CONVERSATION_1,
        now=NOW + 1,
    ).conversation_id == CONVERSATION_1


def test_expired_credential_is_refused(mint):
    expired = mint(ttl_seconds=60, at=NOW)
    with pytest.raises(ChatCredentialExpired):
        verify_chat_credential(
            expired.token,
            SIGNING_KEY,
            revocation_store=_fresh_revocation_store(),
            now=NOW + 61,
        )
    # Control: the same token verifies while it is still inside its window.
    assert verify_chat_credential(
        expired.token,
        SIGNING_KEY,
        revocation_store=_fresh_revocation_store(),
        now=NOW + 59,
    ).conversation_id == CONVERSATION_1


def test_revoked_session_is_refused_while_still_unexpired(credential, revocation_store):
    """Revocation bites before expiry: a logged-out session is refused at once."""
    assert verify_chat_credential(
        credential.token,
        SIGNING_KEY,
        revocation_store=revocation_store,
        now=NOW + 1,
    ).jti == credential.jti
    revocation_store.revoke_jti(credential.jti, NOW + 2)
    with pytest.raises(ChatSessionRevoked) as caught:
        verify_chat_credential(
            credential.token,
            SIGNING_KEY,
            revocation_store=revocation_store,
            now=NOW + 3,
        )
    assert caught.value.code == "session_revoked"
    assert caught.value.as_api_error().status == 401
    # Control: the token is demonstrably still unexpired (exp is well ahead).
    assert credential.session.expires_at > NOW + 3


def test_a_tampered_credential_is_refused(credential):
    head, payload, signature = credential.token.split(".")
    flipped = "A" if payload[0] != "A" else "B"
    tampered = f"{head}.{flipped}{payload[1:]}.{signature}"
    with pytest.raises(InvalidChatCredential):
        verify_chat_credential(
            tampered,
            SIGNING_KEY,
            revocation_store=_fresh_revocation_store(),
            now=NOW + 1,
        )


def test_a_plain_gateway_session_is_not_a_chat_credential(signing_key):
    """A session with no conversation audience carries no conversation scope."""
    session = authn.mint_session(TENANT_A, AGENT_A, signing_key, now=NOW)
    token = authn.session_to_token(session, signing_key)
    with pytest.raises(InvalidChatCredential):
        verify_chat_credential(
            token,
            SIGNING_KEY,
            revocation_store=_fresh_revocation_store(),
            now=NOW + 1,
        )


def test_minting_without_a_signing_key_is_refused():
    """There is no default key and no ephemeral fallback."""
    with pytest.raises(CredentialKeyMissing):
        mint_chat_credential(
            tenant_id=TENANT_A,
            agent_id=AGENT_A,
            conversation_id=CONVERSATION_1,
            signing_key=b"",
        )
    # Control: with a key the same call succeeds.
    assert mint_chat_credential(
        tenant_id=TENANT_A,
        agent_id=AGENT_A,
        conversation_id=CONVERSATION_1,
        signing_key=SIGNING_KEY,
        now=NOW,
    ).tenant_id == TENANT_A


@pytest.mark.parametrize(
    "field",
    ["tenant_id", "agent_id", "conversation_id"],
)
def test_an_incomplete_scope_cannot_be_minted(field):
    """An empty scope part is refused: no anonymous and no tenant-less credential."""
    kwargs = {
        "tenant_id": TENANT_A,
        "agent_id": AGENT_A,
        "conversation_id": CONVERSATION_1,
        "signing_key": SIGNING_KEY,
    }
    kwargs[field] = "   "
    with pytest.raises(InvalidScope):
        mint_chat_credential(**kwargs)


def test_verification_requires_a_revocation_store(credential):
    """There is no path that verifies without consulting revocation."""
    with pytest.raises(InvalidChatCredential):
        verify_chat_credential(credential.token, SIGNING_KEY, revocation_store=None)


def test_a_tenant_b_scope_crossing_is_refused(credential, now):
    """A tenant-A credential is refused for any tenant-B read or write."""
    for kwargs in (
        {"tenant_id": TENANT_B},
        {"tenant_id": TENANT_B, "agent_id": AGENT_A},
        {"agent_id": AGENT_B},
        {"conversation_id": CONVERSATION_2},
    ):
        with pytest.raises((CrossTenantRefused, ConversationScopeMismatch)) as caught:
            assert_scope(credential, **kwargs)
        assert caught.value.code in {"cross_tenant", "conversation_scope_mismatch"}
    # Control: the credential's own scope is accepted unchanged.
    assert_scope(
        credential,
        tenant_id=TENANT_A,
        agent_id=AGENT_A,
        conversation_id=CONVERSATION_1,
    ) is None


def test_audience_carries_exactly_one_conversation(credential):
    members = credential_mod.audience_for(CONVERSATION_1)
    assert members == ("control-plane", f"chat:conversation:{CONVERSATION_1}")
    assert credential_mod.conversation_of(credential.session) == CONVERSATION_1


def test_scope_equality_is_exact():
    """Scopes do not nest: covering is equality, so no scope widens another."""
    outer = ChatScope(TENANT_A, AGENT_A, CONVERSATION_1)
    assert outer.covers(ChatScope(TENANT_A, AGENT_A, CONVERSATION_1))
    assert not outer.covers(ChatScope(TENANT_A, AGENT_A, CONVERSATION_2))
    assert not outer.covers(ChatScope(TENANT_A, AGENT_B, CONVERSATION_1))
    assert not outer.covers(ChatScope(TENANT_B, AGENT_A, CONVERSATION_1))


def test_the_expiry_boundary_is_enforced_at_the_pinned_instant(credential):
    """``exp`` is exclusive: at exactly ``exp`` the credential is already refused."""
    assert credential.session.expires_at == NOW + 3600
    with pytest.raises(ChatCredentialExpired):
        verify_chat_credential(
            credential.token,
            SIGNING_KEY,
            revocation_store=_fresh_revocation_store(),
            now=credential.session.expires_at,
        )
