"""The front door: the auth-gate identity, consumed exactly as the portal does.

The first test is the important one: it pins the *contract* the chat surface
shares with ``portal/server/sso.py`` - same cookie, same token purpose, same
super-admin role name, same merged verifier function object - so "the chat
surface re-implements no login" is a mechanically checked claim rather than a
comment. The rest exercise the real RS256 path end to end.
"""

from __future__ import annotations

import pytest

from identity.chat import credential as credential_mod
from identity.chat.errors import (
    ChatSessionRevoked,
    ConversationScopeMismatch,
    CredentialRequired,
    CrossTenantRefused,
    FrontDoorRefused,
    InvalidScope,
    UnmappedClientIdentity,
)
from identity.chat.frontdoor import (
    CLIENT_HEADER,
    CONSOLE_TOKEN_PURPOSE,
    IDENTITY_HEADER,
    ROOT_ADMIN_ROLE,
    SESSION_COOKIE,
    ChatFrontDoor,
    ChatRequest,
    default_console_verifier,
    requested_tenant,
)
from identity.chat.isolation import conversation_container
from identity.sso.jose import generate_rsa_keypair, public_key_from_jwk
from identity.sso.store import InMemoryStore
from identity.sso.tokens import (
    console_jwks,
    console_kid_for,
    issue_console_session_token,
    verify_console_session_token,
)

TENANT_A = "tenant-acme"
TENANT_B = "tenant-globex"
AGENT_A = "coder"
CONVERSATION_1 = "conv-1"
CLIENT = "openwebui"
EXTERNAL_ADA = "ada@acme.example"
EXTERNAL_GRACE = "grace@globex.example"
EXTERNAL_UNKNOWN = "mallory@evil.example"
SIGNING_KEY = bytes(range(32))
NOW = 1_800_000_000


@pytest.fixture(scope="module")
def console_keys():
    private, public = generate_rsa_keypair()
    return private, public, console_kid_for(public)


@pytest.fixture()
def trusted_keys(console_keys):
    _, public, _ = console_keys
    payload = console_jwks([(console_kid_for(public), public)])
    return {
        entry["kid"]: public_key_from_jwk(dict(entry)) for entry in payload["keys"]
    }


@pytest.fixture()
def revocation_store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture()
def front_door(trusted_keys, revocation_store) -> ChatFrontDoor:
    return ChatFrontDoor(
        signing_key=SIGNING_KEY,
        revocation_store=revocation_store,
        trusted_keys=trusted_keys,
        allowlist_only=False,
    )


def _console_token(
    console_keys,
    *,
    tenant_id: str = TENANT_A,
    email: str = EXTERNAL_ADA,
    role: str = "user",
    ttl: int = 3600,
    at: int = NOW,
) -> str:
    private, _, kid = console_keys
    token, _ = issue_console_session_token(
        private,
        kid=kid,
        tenant_id=tenant_id,
        subject_id=f"idp|{email}",
        email=email,
        name=email,
        role=role,
        now=at,
        ttl=ttl,
    )
    return token


def _request(token: str, *, body=None, query=None, headers=None) -> ChatRequest:
    merged = {
        "Cookie": f"other=1; {SESSION_COOKIE}={token}",
        CLIENT_HEADER: CLIENT,
        IDENTITY_HEADER: EXTERNAL_ADA,
    }
    merged.update(headers or {})
    return ChatRequest(body=body or {}, query=query or {}, headers=merged)


# --- contract parity -------------------------------------------------------- #


def test_the_front_door_speaks_the_portals_contract():
    """Same cookie, same purpose, same super-admin role, same verifier object."""
    from portal.server import sso as portal_sso

    assert SESSION_COOKIE == portal_sso.SESSION_COOKIE
    assert CONSOLE_TOKEN_PURPOSE == portal_sso.CONSOLE_TOKEN_PURPOSE
    assert ROOT_ADMIN_ROLE == portal_sso.ROOT_ADMIN_ROLE
    assert default_console_verifier() is verify_console_session_token


# --- the happy path --------------------------------------------------------- #


def test_establish_mints_a_credential_scoped_to_the_declared_binding(
    front_door, console_keys
):
    token = _console_token(console_keys)
    credential = front_door.establish(
        _request(token, body={"conversationId": CONVERSATION_1}), now=NOW + 1
    )
    assert credential.tenant_id == TENANT_A
    assert credential.agent_id == AGENT_A
    assert credential.conversation_id == CONVERSATION_1
    assert credential.role == "chat-user"
    assert credential.container_id == conversation_container(
        TENANT_A, AGENT_A, CONVERSATION_1
    )
    assert credential.subject == f"idp|{EXTERNAL_ADA}"
    # The credential verifies through the merged verifier + revocation check.
    resumed = front_door.resume(
        credential.token, conversation_id=CONVERSATION_1, now=NOW + 2
    )
    assert resumed.scope == credential.scope


def test_the_conversation_may_come_from_the_query_string(front_door, console_keys):
    token = _console_token(console_keys)
    credential = front_door.establish(
        _request(token, query={"conversation": CONVERSATION_1}), now=NOW + 1
    )
    assert credential.conversation_id == CONVERSATION_1


def test_a_request_without_a_conversation_is_refused(front_door, console_keys):
    token = _console_token(console_keys)
    with pytest.raises(InvalidScope):
        front_door.establish(_request(token), now=NOW + 1)


# --- the tenant is never taken from the request ----------------------------- #


def test_a_foreign_tenant_in_the_body_is_refused(front_door, console_keys):
    token = _console_token(console_keys)
    request = _request(
        token, body={"conversationId": CONVERSATION_1, "tenantId": TENANT_B}
    )
    with pytest.raises(CrossTenantRefused) as caught:
        front_door.establish(request, now=NOW + 1)
    assert caught.value.code == "cross_tenant"
    assert requested_tenant(request) == TENANT_B
    # Control: with no tenant claim the same request establishes normally.
    assert front_door.establish(
        _request(token, body={"conversationId": CONVERSATION_1}), now=NOW + 1
    ).tenant_id == TENANT_A


def test_a_foreign_tenant_in_the_query_is_refused(front_door, console_keys):
    token = _console_token(console_keys)
    with pytest.raises(CrossTenantRefused):
        front_door.establish(
            _request(
                token,
                body={"conversationId": CONVERSATION_1},
                query={"tenant": TENANT_B},
            ),
            now=NOW + 1,
        )


def test_a_matching_tenant_claim_changes_nothing(front_door, console_keys):
    """Naming the *correct* tenant is not a source of authority either."""
    token = _console_token(console_keys)
    credential = front_door.establish(
        _request(
            token,
            body={"conversationId": CONVERSATION_1, "tenantId": TENANT_A},
        ),
        now=NOW + 1,
    )
    assert credential.tenant_id == TENANT_A


def test_the_body_cannot_nominate_an_agent_or_a_role(front_door, console_keys):
    token = _console_token(console_keys)
    credential = front_door.establish(
        _request(
            token,
            body={
                "conversationId": CONVERSATION_1,
                "agentId": "analyst",
                "role": ROOT_ADMIN_ROLE,
            },
        ),
        now=NOW + 1,
    )
    assert credential.agent_id == AGENT_A
    assert credential.role == "chat-user"


# --- fail-closed front door ------------------------------------------------- #


def test_no_jwks_mirror_refuses_every_session(revocation_store):
    door = ChatFrontDoor(
        signing_key=SIGNING_KEY,
        revocation_store=revocation_store,
        trusted_keys=None,
        allowlist_only=False,
    )
    with pytest.raises(FrontDoorRefused):
        door.verify_session("any.token.here")


def test_a_missing_token_is_refused(front_door):
    with pytest.raises(CredentialRequired) as caught:
        front_door.verify_session("")
    assert caught.value.code == "credential_required"


def test_a_tampered_console_token_is_refused(front_door, console_keys):
    token = _console_token(console_keys)
    head, payload, signature = token.split(".")
    flipped = "A" if signature[0] != "A" else "B"
    with pytest.raises(FrontDoorRefused):
        front_door.verify_session(f"{head}.{payload}.{flipped}{signature[1:]}")


def test_an_expired_console_token_is_refused(front_door, console_keys):
    token = _console_token(console_keys, ttl=60)
    with pytest.raises(FrontDoorRefused):
        front_door.verify_session(token, now=NOW + 61)
    # Control: it verifies inside its window.
    assert front_door.verify_session(token, now=NOW + 59)["email"] == EXTERNAL_ADA


def test_a_token_signed_by_another_key_is_refused(front_door):
    other_private, other_public = generate_rsa_keypair()
    token, _ = issue_console_session_token(
        other_private,
        kid=console_kid_for(other_public),
        tenant_id=TENANT_A,
        subject_id="idp|mallory",
        email=EXTERNAL_UNKNOWN,
        name="mallory",
        role=ROOT_ADMIN_ROLE,
        now=NOW,
        ttl=3600,
    )
    with pytest.raises(FrontDoorRefused):
        front_door.verify_session(token)


def test_the_allowlist_decides_the_role_not_the_token(front_door, console_keys):
    """A token claiming super-admin is refused when the email is not allowlisted."""
    token = _console_token(console_keys, email=EXTERNAL_UNKNOWN, role=ROOT_ADMIN_ROLE)
    claims = front_door.verify_session(token)
    assert claims["role"] == ROOT_ADMIN_ROLE
    gated = ChatFrontDoor(
        signing_key=SIGNING_KEY,
        revocation_store=front_door.revocation_store,
        trusted_keys=front_door.trusted_keys,
        root_admin_emails=(EXTERNAL_ADA,),
        allowlist_only=True,
    )
    with pytest.raises(FrontDoorRefused):
        gated.identity_from_claims(claims)
    # Control: an allowlisted email really does get the super-admin role.
    allowed = gated.identity_from_claims(
        {"sub": "idp|ada", "email": EXTERNAL_ADA, "tenantId": TENANT_A}
    )
    assert allowed.super_admin is True


def test_an_undeclared_client_is_refused(front_door, console_keys):
    token = _console_token(console_keys)
    request = ChatRequest(
        body={"conversationId": CONVERSATION_1},
        headers={"Cookie": f"{SESSION_COOKIE}={token}"},
    )
    with pytest.raises(FrontDoorRefused):
        front_door.establish(request, now=NOW + 1)


def test_an_unmapped_external_identity_is_refused(front_door, console_keys):
    token = _console_token(console_keys, email=EXTERNAL_UNKNOWN)
    with pytest.raises(UnmappedClientIdentity):
        front_door.establish(
            _request(
                token,
                body={"conversationId": CONVERSATION_1},
                headers={IDENTITY_HEADER: EXTERNAL_UNKNOWN},
            ),
            now=NOW + 1,
        )
    # Control: a declared identity on the same code path establishes.
    assert front_door.establish(
        _request(
            token,
            body={"conversationId": CONVERSATION_1},
            headers={IDENTITY_HEADER: EXTERNAL_ADA},
        ),
        now=NOW + 1,
    ).tenant_id == TENANT_A


def test_an_auth_gate_tenant_that_disagrees_with_the_binding_is_refused(
    front_door, console_keys
):
    token = _console_token(console_keys, tenant_id=TENANT_B)
    with pytest.raises(CrossTenantRefused):
        front_door.establish(
            _request(token, body={"conversationId": CONVERSATION_1}), now=NOW + 1
        )
    # Control: the binding's own tenant in the token establishes.
    token = _console_token(console_keys, tenant_id=TENANT_A)
    assert front_door.establish(
        _request(token, body={"conversationId": CONVERSATION_1}), now=NOW + 1
    ).tenant_id == TENANT_A


# --- revocation and status -------------------------------------------------- #


def test_resume_consults_revocation_and_refuses_a_logged_out_credential(
    front_door, console_keys
):
    token = _console_token(console_keys)
    credential = front_door.establish(
        _request(token, body={"conversationId": CONVERSATION_1}), now=NOW + 1
    )
    front_door.revoke(credential.jti, now=NOW + 2)
    with pytest.raises(ChatSessionRevoked):
        front_door.resume(
            credential.token, conversation_id=CONVERSATION_1, now=NOW + 3
        )
    assert credential.session.expires_at > NOW + 3


def test_resume_refuses_a_credential_presented_for_another_conversation(
    front_door, console_keys
):
    token = _console_token(console_keys)
    credential = front_door.establish(
        _request(token, body={"conversationId": CONVERSATION_1}), now=NOW + 1
    )
    with pytest.raises(ConversationScopeMismatch):
        front_door.resume(
            credential.token, conversation_id="conv-2", now=NOW + 2
        )
    # Control: the credential's own conversation resumes.
    assert front_door.resume(
        credential.token, conversation_id=CONVERSATION_1, now=NOW + 2
    ).conversation_id == CONVERSATION_1


def test_revoking_without_a_jti_is_refused(front_door):
    with pytest.raises(InvalidScope):
        front_door.revoke("")


def test_a_globex_identity_lands_in_globex(front_door, console_keys):
    """The other declared tenant is reachable - the map is not tenant-A-only."""
    token = _console_token(
        console_keys, tenant_id=TENANT_B, email=EXTERNAL_GRACE
    )
    credential = front_door.establish(
        _request(
            token,
            body={"conversationId": CONVERSATION_1},
            headers={IDENTITY_HEADER: EXTERNAL_GRACE},
        ),
        now=NOW + 1,
    )
    assert credential.tenant_id == TENANT_B
    assert credential_mod.conversation_of(credential.session) == CONVERSATION_1
