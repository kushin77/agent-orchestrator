"""Single-source HS256 codec for the registry service (issue #1204).

`registry/service/identity.py` used to hand-roll base64url + HMAC-SHA256 for its
session tokens. It now delegates to the identity lane's canonical codec
(`identity/sso/jose.py`), so a signature-verification defect has exactly one
place to patch.

The collapse is only safe if the wire format did not move, so these tests pin it
against a **frozen** token minted by the *old* code before the refactor:

- the frozen legacy token still verifies through the new path;
- re-minting the same session through the new path reproduces the frozen token
  byte for byte (nothing moved on the wire, in either direction);
- the codec really is delegated (the canonical functions are the ones called);
- the duplicate base64url implementation is gone from the module.
"""

from __future__ import annotations

import pytest

import service.identity as identity_mod
from service import AgentRegistry, InvalidCredentialError, SessionExpiredError
from service.identity import AgentSession

# A fixed, clearly-labelled unit-test key. Not a secret: it exists only so token
# signing/verification is deterministic across test runs.
TEST_SIGNING_KEY = bytes(range(32))

# --------------------------------------------------------------------------- #
# FROZEN fixture - minted by the PRE-refactor ``AgentSession.encode``.
# Evidence capture for issue #1204, 2026-09-17: do not regenerate it. It is the
# compatibility anchor for tokens already outstanding in the field.
# --------------------------------------------------------------------------- #
FROZEN_NOW = 1_700_000_000
FROZEN_EXP = 1_700_003_600
FROZEN_TOKEN = ".".join(
    (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        (
            "eyJhZ2VudElkIjoid29ya2VyLTEiLCJhbGxvd2VkVG9vbHMiOlsiZmlsZV93cml0ZSIs"
            "InNoZWxsX2V4ZWMiXSwiYXVkIjpbImNvbnRyb2wtcGxhbmUiXSwiZXhwIjoxNzAwMDAz"
            "NjAwLCJpYXQiOjE3MDAwMDAwMDAsImlzcyI6InVybjphZ2VudC1vcmNoZXN0cmF0b3I6"
            "cmVnaXN0cnkiLCJqdGkiOiIwMTIzNDU2Nzg5YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZiIs"
            "InJvbGUiOiJ3b3JrZXIiLCJzdWIiOiJ3b3JrZXItMSIsInRlbmFudElkIjoiYWNtZSJ9"
        ),
        "23HslgwCcoC2L-6kDUaRCD_4YvyemEtmrIIxqMZMg60",
    )
)

# The gateway lane mints the same shape; its frozen token proves the two
# consumers now speak one codec (see test_gateway_minted_token_verifies_here).
FROZEN_GATEWAY_TOKEN = ".".join(
    (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        (
            "eyJhZ2VudElkIjoid29ya2VyLTEiLCJhbGxvd2VkVG9vbHMiOlsia2Iuc3VtbWFy"
            "eSIsInBsYXRmb3JtLndob2FtaSJdLCJhdWQiOlsiY29udHJvbC1wbGFuZSJdLCJleHAi"
            "OjE3MDAwMDM2MDAsImlhdCI6MTcwMDAwMDAwMCwiaXNzIjoidXJuOmFnZW50LW9yY2hl"
            "c3RyYXRvcjptY3AiLCJqdGkiOiJmZWRjYmE5ODc2NTQzMjEwZmVkY2JhOTg3NjU0MzIx"
            "MCIsInJvbGUiOiJ3b3JrZXIiLCJzdWIiOiJ3b3JrZXItMSIsInRlbmFudElkIjoiYWNt"
            "ZSJ9"
        ),
        "ROHMc3JnJYqmxL-wewIitJhTtk_Egyq2NcgtLog1-d0",
    )
)


def _frozen_session() -> AgentSession:
    """The exact session the frozen registry token was minted from."""
    return AgentSession(
        issuer="urn:agent-orchestrator:registry",
        subject="worker-1",
        audience=("control-plane",),
        issued_at=FROZEN_NOW,
        expires_at=FROZEN_EXP,
        token_id="0123456789abcdef0123456789abcdef",
        tenant_id="acme",
        agent_id="worker-1",
        role="worker",
        allowed_tools=("file_write", "shell_exec"),
    )


# --------------------------------------------------------------------------- #
# wire-format compatibility (the point of the collapse)
# --------------------------------------------------------------------------- #
def test_frozen_legacy_token_still_verifies():
    """A token minted by the old code verifies through the new path."""
    session = AgentSession.decode(FROZEN_TOKEN, TEST_SIGNING_KEY, now=FROZEN_NOW)
    assert session.tenant_id == "acme"
    assert session.agent_id == "worker-1"
    assert session.role == "worker"
    assert session.allowed_tools == ("file_write", "shell_exec")
    assert session.issued_at == FROZEN_NOW
    assert session.expires_at == FROZEN_EXP
    assert session.token_id == "0123456789abcdef0123456789abcdef"


def test_new_path_reproduces_the_frozen_token_byte_for_byte():
    """Vice versa: the new path emits exactly the legacy bytes."""
    assert _frozen_session().encode(TEST_SIGNING_KEY) == FROZEN_TOKEN


def test_frozen_legacy_token_verifies_through_the_service_facade():
    """The public facade (AgentRegistry) accepts the frozen token too."""
    reg = AgentRegistry(signing_key=TEST_SIGNING_KEY)
    session = reg.verify_session(
        FROZEN_TOKEN, signing_key=TEST_SIGNING_KEY, now=FROZEN_NOW
    )
    assert session.tenant_id == "acme"
    assert reg.require_scope(session, "acme").agent_id == "worker-1"


def test_frozen_legacy_token_is_still_refused_when_expired():
    """Expiry is preserved: the frozen token is refused past its exp."""
    with pytest.raises(SessionExpiredError):
        AgentSession.decode(FROZEN_TOKEN, TEST_SIGNING_KEY, now=FROZEN_EXP)


def test_frozen_legacy_token_is_still_refused_with_the_wrong_key():
    with pytest.raises(InvalidCredentialError):
        AgentSession.decode(FROZEN_TOKEN, b"a-different-key-0000000000")


def test_gateway_minted_token_verifies_here():
    """Cross-module: a gateway-minted token verifies through this consumer."""
    session = AgentSession.decode(
        FROZEN_GATEWAY_TOKEN, TEST_SIGNING_KEY, now=FROZEN_NOW
    )
    assert session.tenant_id == "acme"
    assert session.allowed_tools == ("kb.summary", "platform.whoami")


# --------------------------------------------------------------------------- #
# the delegation is real (not a coincidence of two identical implementations)
# --------------------------------------------------------------------------- #
def test_encode_delegates_to_the_canonical_codec(monkeypatch):
    seen = []
    real_encode = identity_mod.jwt_encode

    def spy(claims, **kwargs):
        seen.append((dict(claims), kwargs))
        return real_encode(claims, **kwargs)

    monkeypatch.setattr(identity_mod, "jwt_encode", spy)
    token = _frozen_session().encode(TEST_SIGNING_KEY)

    assert token == FROZEN_TOKEN
    assert len(seen) == 1
    claims, kwargs = seen[0]
    assert claims["tenantId"] == "acme"
    assert kwargs["alg"] == identity_mod.ALG_HS256
    assert kwargs["key"] == TEST_SIGNING_KEY


def test_decode_delegates_to_the_canonical_codec(monkeypatch):
    seen = []
    real_unsign = identity_mod.jwt_unsign

    def spy(token, **kwargs):
        seen.append((token, kwargs))
        return real_unsign(token, **kwargs)

    monkeypatch.setattr(identity_mod, "jwt_unsign", spy)
    session = AgentSession.decode(FROZEN_TOKEN, TEST_SIGNING_KEY, now=FROZEN_NOW)

    assert session.tenant_id == "acme"
    assert seen == [
        (FROZEN_TOKEN, {"alg": identity_mod.ALG_HS256, "key": TEST_SIGNING_KEY})
    ]


def test_alg_is_pinned_so_a_none_header_is_refused():
    """Algorithm confusion: a `none`-header token is refused, never verified.

    The codec pins ``alg`` to HS256 before any signature work, so a token that
    claims ``alg: none`` (with any signature, including an empty one) cannot be
    treated as unsigned.
    """
    header, payload, _ = FROZEN_TOKEN.split(".")
    none_token = f"{header}.{payload}."  # empty signature
    with pytest.raises(InvalidCredentialError):
        AgentSession.decode(none_token, TEST_SIGNING_KEY, now=FROZEN_NOW)

    import base64
    import json

    forged_header = (
        base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    with pytest.raises(InvalidCredentialError):
        AgentSession.decode(
            f"{forged_header}.{payload}.", TEST_SIGNING_KEY, now=FROZEN_NOW
        )


# --------------------------------------------------------------------------- #
# the duplicate is gone (mechanical, so it cannot silently return)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name", ["_b64url_encode", "_b64url_decode", "_HEADER", "_JSON_KW", "_sign"]
)
def test_no_hand_rolled_codec_pieces_remain(name):
    assert not hasattr(identity_mod, name), (
        f"{name} is back in registry/service/identity.py - the HS256 codec is "
        "identity/sso/jose.py and must not be re-implemented (issue #1204)"
    )
