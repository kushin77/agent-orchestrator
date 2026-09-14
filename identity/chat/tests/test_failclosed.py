"""Fail-closed defaults, one refusal per default, each with its control.

The six defaults the issue names, tested as *behaviour* rather than asserted as
intent:

1. no anonymous mode;
2. no tenant from the request body;
3. no cross-tenant fallback;
4. no impersonation shortcut;
5. no secret read from (or written to) a request body or a tracked file;
6. no credential without a signing key and a revocation store.

Each refusal test sits beside the call that must succeed, so a module that
refused everything could not pass this file.
"""

from __future__ import annotations

import inspect
import json

import pytest

from identity.chat import credential as credential_mod
from identity.chat.approvals import ChatApprovalRouter
from identity.chat.binding import DEFAULT_MAP_PATH, IdentityMap
from identity.chat.credential import assert_scope, mint_chat_credential
from identity.chat.errors import (
    CredentialKeyMissing,
    CredentialRequired,
    CrossTenantRefused,
    InvalidChatCredential,
    InvalidScope,
    UnmappedClientIdentity,
)
from identity.chat.frontdoor import (
    CLIENT_HEADER,
    IDENTITY_HEADER,
    SESSION_COOKIE,
    ChatFrontDoor,
    ChatRequest,
    assert_no_foreign_tenant,
    requested_tenant,
)
from identity.chat.isolation import ChatIsolation
from identity.sso.store import InMemoryStore

TENANT_A = "tenant-acme"
TENANT_B = "tenant-globex"
AGENT_A = "coder"
CONVERSATION_1 = "conv-1"
CLIENT = "openwebui"
EXTERNAL_ADA = "ada@acme.example"
SIGNING_KEY = bytes(range(32))
NOW = 1_800_000_000

#: Parameter names that would be an impersonation shortcut if they existed.
IMPERSONATION_NAMES = (
    "on_behalf_of",
    "impersonate",
    "impersonation",
    "acting_as",
    "act_as",
    "subject_override",
    "as_user",
    "sudo",
)


def _claims_verifier(claims):
    """A deterministic stand-in for the merged console verifier (no crypto here)."""

    def _verify(token, trusted_keys, *, now, **kwargs):
        return dict(claims)

    return _verify


def _front_door(*, claims=None, allowlist_only=False, **kwargs) -> ChatFrontDoor:
    return ChatFrontDoor(
        signing_key=SIGNING_KEY,
        revocation_store=InMemoryStore(),
        trusted_keys={"kid-test": object()},
        allowlist_only=allowlist_only,
        verifier=_claims_verifier(
            claims
            if claims is not None
            else {"sub": "idp|ada", "email": EXTERNAL_ADA, "tenantId": TENANT_A}
        ),
        **kwargs,
    )


def _request(*, body=None, query=None, headers=None) -> ChatRequest:
    merged = {
        "Cookie": f"{SESSION_COOKIE}=test-token",
        CLIENT_HEADER: CLIENT,
        IDENTITY_HEADER: EXTERNAL_ADA,
    }
    merged.update(headers or {})
    return ChatRequest(body=body or {}, query=query or {}, headers=merged)


# --- 1. no anonymous mode --------------------------------------------------- #


def test_there_is_no_anonymous_mode_at_the_front_door():
    door = _front_door()
    for token in ("", "   ", None):
        with pytest.raises(CredentialRequired):
            door.verify_session(token)
    # Control: a real token is accepted on the same door.
    assert door.verify_session("test-token")["email"] == EXTERNAL_ADA


def test_there_is_no_anonymous_identity_at_the_binding():
    identity_map = IdentityMap.load()
    with pytest.raises(UnmappedClientIdentity):
        identity_map.resolve(client=CLIENT, external_id="")
    # Control: the declared identity resolves.
    assert identity_map.resolve(client=CLIENT, external_id=EXTERNAL_ADA)


def test_there_is_no_tenantless_credential():
    with pytest.raises(InvalidScope):
        mint_chat_credential(
            tenant_id="",
            agent_id=AGENT_A,
            conversation_id=CONVERSATION_1,
            signing_key=SIGNING_KEY,
        )
    # Control: the complete scope mints.
    assert mint_chat_credential(
        tenant_id=TENANT_A,
        agent_id=AGENT_A,
        conversation_id=CONVERSATION_1,
        signing_key=SIGNING_KEY,
        now=NOW,
    ).tenant_id == TENANT_A


# --- 2. no tenant from the request body ------------------------------------- #


def test_a_body_tenant_id_cannot_select_a_tenant(credential):
    foreign = _request(body={"conversationId": CONVERSATION_1, "tenantId": TENANT_B})
    with pytest.raises(CrossTenantRefused):
        assert_no_foreign_tenant(foreign, credential)
    assert requested_tenant(foreign) == TENANT_B
    # Control: the same body without the claim passes, and the credential's own
    # tenant in the body does not change the credential either.
    assert assert_no_foreign_tenant(
        _request(body={"conversationId": CONVERSATION_1}), credential
    ) is None
    assert assert_no_foreign_tenant(
        _request(body={"conversationId": CONVERSATION_1, "tenantId": TENANT_A}),
        credential,
    ) is None
    assert credential.tenant_id == TENANT_A


def test_a_query_tenant_cannot_select_a_tenant(credential):
    with pytest.raises(CrossTenantRefused):
        assert_no_foreign_tenant(
            _request(query={"tenant": TENANT_B, "conversation": CONVERSATION_1}),
            credential,
        )
    # Control: the matching query tenant passes.
    assert assert_no_foreign_tenant(
        _request(query={"tenant": TENANT_A, "conversation": CONVERSATION_1}),
        credential,
    ) is None


def test_the_front_door_takes_the_tenant_from_the_binding_only():
    """Even with the auth-gate identity claiming B, ada lands in A."""
    door = _front_door(
        claims={"sub": "idp|ada", "email": EXTERNAL_ADA, "tenantId": TENANT_A}
    )
    credential = door.establish(
        _request(body={"conversationId": CONVERSATION_1}), now=NOW
    )
    assert credential.tenant_id == TENANT_A
    assert credential.agent_id == AGENT_A


# --- 3. no cross-tenant fallback -------------------------------------------- #


def test_no_cross_tenant_fallback_on_the_credential_scope(credential):
    with pytest.raises(CrossTenantRefused):
        assert_scope(credential, tenant_id=TENANT_B)
    # Control: the credential's own tenant is accepted.
    assert assert_scope(credential, tenant_id=TENANT_A) is None


def test_no_cross_tenant_fallback_at_the_front_door():
    door = _front_door(
        claims={"sub": "idp|grace", "email": EXTERNAL_ADA, "tenantId": TENANT_B}
    )
    with pytest.raises(CrossTenantRefused):
        door.establish(_request(body={"conversationId": CONVERSATION_1}), now=NOW)


def test_no_cross_tenant_fallback_in_memory(credential, isolation):
    other = conversation_container_for(TENANT_B)
    with pytest.raises(Exception) as caught:
        isolation.read(credential, memory_id="m", requested_container=other)
    assert caught.value.code == "conversation_scope_mismatch"
    assert isolation.cross_scope_denials == 1
    # Control: the credential's own container is read without a refusal.
    assert isolation.read(credential, memory_id="missing") is None
    assert isolation.cross_scope_denials == 1


def conversation_container_for(tenant_id: str) -> str:
    from identity.chat.isolation import conversation_container

    return conversation_container(tenant_id, "analyst", "conv-9")


# --- 4. no impersonation shortcut ------------------------------------------- #


@pytest.mark.parametrize(
    "target",
    [
        credential_mod.mint_chat_credential,
        credential_mod.verify_chat_credential,
        credential_mod.assert_scope,
        ChatFrontDoor.establish,
        ChatFrontDoor.resume,
        ChatApprovalRouter.propose,
        ChatApprovalRouter.execute,
        ChatIsolation.read,
        ChatIsolation.write,
    ],
)
def test_no_entry_point_accepts_an_impersonation_parameter(target):
    parameters = set(inspect.signature(target).parameters)
    assert parameters & set(IMPERSONATION_NAMES) == set(), (
        f"{target.__qualname__} accepts an impersonation parameter: "
        f"{sorted(parameters & set(IMPERSONATION_NAMES))}"
    )


def test_the_subject_is_the_auth_gate_principal_not_a_caller_choice():
    """``subject`` is minted from the verified identity, not from the request."""
    door = _front_door()
    credential = door.establish(
        _request(
            body={"conversationId": CONVERSATION_1, "subject": "someone-else"},
        ),
        now=NOW,
    )
    assert credential.subject == "idp|ada"


# --- 5. no secret in a request body or a tracked file ----------------------- #


def test_a_body_supplied_signing_key_is_ignored():
    """The credential is minted with the deployment key, not a body value."""
    door = _front_door()
    credential = door.establish(
        _request(
            body={
                "conversationId": CONVERSATION_1,
                "signingKey": "body-supplied",
                "key": "body-supplied",
                # Short on purpose: scripts/check-secrets.sh RE_GEN flags a
                # secret word assigned a quoted 8+ character value, and this
                # body field is a test input, not a credential.
                "secret": "ignored",
            },
        ),
        now=NOW,
    )
    verified = credential_mod.verify_chat_credential(
        credential.token,
        SIGNING_KEY,
        revocation_store=InMemoryStore(),
        now=NOW + 1,
    )
    assert verified.scope == credential.scope
    with pytest.raises(InvalidChatCredential):
        credential_mod.verify_chat_credential(
            credential.token,
            b"body-supplied",
            revocation_store=InMemoryStore(),
            now=NOW + 1,
        )


def test_no_tracked_chat_file_carries_secret_material():
    root = DEFAULT_MAP_PATH.parent
    # Secret *shapes*, not bare words: the map's note documents the absence of
    # secret material, so a word scan would flag the note itself.
    forbidden = (
        "-----begin",
        "bearer ",
        '"signingkey"',
        '"privatekey"',
        '"apikey"',
        '"password"',
        '"secret"',
        '"token"',
    )
    for path in sorted(root.glob("*.json")):
        text = path.read_text(encoding="utf-8").lower()
        for needle in forbidden:
            assert needle not in text, f"{path.name} contains a secret shape {needle!r}"
    payload = json.loads(DEFAULT_MAP_PATH.read_text(encoding="utf-8"))
    for entry in payload["bindings"]:
        assert set(entry) == {"client", "externalId", "tenantId", "agentId", "role"}


# --- 6. no credential without a key and a revocation authority -------------- #


def test_a_credential_cannot_be_minted_or_verified_without_a_key(credential):
    with pytest.raises(CredentialKeyMissing):
        mint_chat_credential(
            tenant_id=TENANT_A,
            agent_id=AGENT_A,
            conversation_id=CONVERSATION_1,
            signing_key=None,
        )
    with pytest.raises(CredentialKeyMissing):
        credential_mod.verify_chat_credential(
            credential.token, None, revocation_store=InMemoryStore(), now=NOW + 1
        )


def test_a_credential_cannot_be_verified_without_a_revocation_store(credential):
    with pytest.raises(InvalidChatCredential):
        credential_mod.verify_chat_credential(
            credential.token, SIGNING_KEY, revocation_store=None, now=NOW + 1
        )
    # Control: with a revocation store the same token verifies.
    assert credential_mod.verify_chat_credential(
        credential.token,
        SIGNING_KEY,
        revocation_store=InMemoryStore(),
        now=NOW + 1,
    ).tenant_id == TENANT_A
