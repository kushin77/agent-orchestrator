"""Single-source HS256 codec for the MCP gateway (issue #1204).

`gateway/mcp/authn.py` used to hand-roll base64url + HMAC-SHA256. Its docstring
admitted the format was "byte-identical" to the registry's; it now delegates to
the identity lane's canonical codec (`identity/sso/jose.py`) instead, so a
signature-verification defect has exactly one place to patch.

These tests pin the collapse against a **frozen** token minted by the *old* code
before the refactor:

- the frozen legacy token still verifies through the new path;
- re-encoding the same session through the new path reproduces the frozen token
  byte for byte (nothing moved on the wire, in either direction);
- the codec really is delegated (the canonical functions are the ones bound);
- the duplicate base64url implementation is gone from the module.
"""

from __future__ import annotations

import base64
import json

import pytest

import identity.sso.jose as jose
from mcp import authn
from mcp.authn import mint_session, session_to_token, verify_token
from mcp.errors import InvalidCredentialError, SessionExpiredError
from mcp.model import SessionIdentity

TEST_SIGNING_KEY = bytes(range(32))

# --------------------------------------------------------------------------- #
# FROZEN fixtures - minted by the PRE-refactor `session_to_token`.
# Evidence capture for issue #1204, 2026-09-17: do not regenerate them. They are
# the compatibility anchors for tokens already outstanding in the field.
# --------------------------------------------------------------------------- #
FROZEN_NOW = 1_700_000_000
FROZEN_EXP = 1_700_003_600
FROZEN_TOKEN = ".".join(
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

# The registry lane mints the same shape; its frozen token proves the two
# consumers now speak one codec.
FROZEN_REGISTRY_TOKEN = ".".join(
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

# The offline ``mint_session`` convenience path, frozen with a deterministic
# token id (``mint_session`` generates a random one, so the codec is fed the
# session it produced).
FROZEN_MINT_TOKEN = ".".join(
    (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        (
            "eyJhZ2VudElkIjoiYWdlbnQtYSIsImFsbG93ZWRUb29scyI6WyJrYi5zdW1tYXJ5"
            "Il0sImF1ZCI6WyJjb250cm9sLXBsYW5lIl0sImV4cCI6MTcwMDAwMzYwMCwiaWF0Ijox"
            "NzAwMDAwMDAwLCJpc3MiOiJ1cm46YWdlbnQtb3JjaGVzdHJhdG9yOm1jcCIsImp0aSI6"
            "ImFhYWFiYmJiY2NjY2RkZGRlZWVlZmZmZjAwMDAxMTExIiwicm9sZSI6ImFnZW50Iiwi"
            "c3ViIjoiYWdlbnQtYSIsInRlbmFudElkIjoiYWNtZSJ9"
        ),
        "W0CT6BPvTpqitojTWmXKFIBBPnPM2JDZmsu1ELHeakw",
    )
)

MINT_TOKEN_ID = "aaaabbbbccccddddeeeeffff00001111"


def _frozen_session() -> SessionIdentity:
    """The exact session the frozen gateway token was minted from."""
    return SessionIdentity(
        tenant_id="acme",
        agent_id="worker-1",
        role="worker",
        allowed_tools=("kb.summary", "platform.whoami"),
        subject="worker-1",
        issuer="urn:agent-orchestrator:mcp",
        audience=("control-plane",),
        issued_at=FROZEN_NOW,
        expires_at=FROZEN_EXP,
        token_id="fedcba9876543210fedcba9876543210",
    )


# --------------------------------------------------------------------------- #
# wire-format compatibility (the point of the collapse)
# --------------------------------------------------------------------------- #
def test_frozen_legacy_token_still_verifies():
    """A token minted by the old code verifies through the new path."""
    session = verify_token(FROZEN_TOKEN, TEST_SIGNING_KEY, now=FROZEN_NOW)
    assert session.tenant_id == "acme"
    assert session.agent_id == "worker-1"
    assert session.role == "worker"
    assert session.allowed_tools == ("kb.summary", "platform.whoami")
    assert session.issued_at == FROZEN_NOW
    assert session.expires_at == FROZEN_EXP
    assert session.token_id == "fedcba9876543210fedcba9876543210"


def test_new_path_reproduces_the_frozen_token_byte_for_byte():
    """Vice versa: the new path emits exactly the legacy bytes."""
    assert session_to_token(_frozen_session(), TEST_SIGNING_KEY) == FROZEN_TOKEN


def test_frozen_legacy_token_is_still_refused_when_expired():
    """Expiry is preserved: the frozen token is refused past its exp."""
    with pytest.raises(SessionExpiredError):
        verify_token(FROZEN_TOKEN, TEST_SIGNING_KEY, now=FROZEN_EXP)


def test_frozen_legacy_token_is_still_refused_with_the_wrong_key():
    with pytest.raises(InvalidCredentialError):
        verify_token(FROZEN_TOKEN, b"a-different-key-0000000000")


def test_frozen_legacy_token_is_still_refused_when_tampered():
    tampered = FROZEN_TOKEN[:-2] + ("xx" if FROZEN_TOKEN[-2:] != "xx" else "yy")
    with pytest.raises(InvalidCredentialError):
        verify_token(tampered, TEST_SIGNING_KEY, now=FROZEN_NOW)


def test_registry_minted_token_verifies_here():
    """Cross-module: a registry-minted token verifies through this consumer."""
    session = verify_token(FROZEN_REGISTRY_TOKEN, TEST_SIGNING_KEY, now=FROZEN_NOW)
    assert session.tenant_id == "acme"
    assert session.agent_id == "worker-1"
    assert session.allowed_tools == ("file_write", "shell_exec")
    assert session.issuer == "urn:agent-orchestrator:registry"


def test_offline_mint_path_is_unchanged():
    """``mint_session`` still builds the same claims (only the jti is random)."""
    minted = mint_session(
        "acme",
        "agent-a",
        TEST_SIGNING_KEY,
        role="agent",
        allowed_tools=("kb.summary",),
        subject="agent-a",
        ttl_seconds=3600,
        now=FROZEN_NOW,
        issuer="urn:agent-orchestrator:mcp",
        audience=("control-plane",),
    )
    frozen = SessionIdentity(
        tenant_id=minted.tenant_id,
        agent_id=minted.agent_id,
        role=minted.role,
        allowed_tools=minted.allowed_tools,
        subject=minted.subject,
        issuer=minted.issuer,
        audience=minted.audience,
        issued_at=minted.issued_at,
        expires_at=minted.expires_at,
        token_id=MINT_TOKEN_ID,
    )
    assert session_to_token(frozen, TEST_SIGNING_KEY) == FROZEN_MINT_TOKEN
    verified = verify_token(FROZEN_MINT_TOKEN, TEST_SIGNING_KEY, now=FROZEN_NOW)
    assert verified.tenant_id == "acme"
    assert verified.agent_id == "agent-a"


# --------------------------------------------------------------------------- #
# the delegation is real (not a coincidence of two identical implementations)
# --------------------------------------------------------------------------- #
def test_the_canonical_codec_is_the_one_bound_here():
    assert authn.jwt_encode is jose.jwt_encode
    assert authn.jwt_unsign is jose.jwt_unsign
    assert authn.ALG_HS256 == jose.ALG_HS256 == "HS256"


def test_encode_delegates_to_the_canonical_codec(monkeypatch):
    seen = []
    real_encode = authn.jwt_encode

    def spy(claims, **kwargs):
        seen.append((dict(claims), kwargs))
        return real_encode(claims, **kwargs)

    monkeypatch.setattr(authn, "jwt_encode", spy)
    token = session_to_token(_frozen_session(), TEST_SIGNING_KEY)

    assert token == FROZEN_TOKEN
    assert len(seen) == 1
    claims, kwargs = seen[0]
    assert claims["tenantId"] == "acme"
    assert kwargs["alg"] == "HS256"
    assert kwargs["key"] == TEST_SIGNING_KEY


def test_decode_delegates_to_the_canonical_codec(monkeypatch):
    seen = []
    real_unsign = authn.jwt_unsign

    def spy(token, **kwargs):
        seen.append((token, kwargs))
        return real_unsign(token, **kwargs)

    monkeypatch.setattr(authn, "jwt_unsign", spy)
    session = verify_token(FROZEN_TOKEN, TEST_SIGNING_KEY, now=FROZEN_NOW)

    assert session.tenant_id == "acme"
    assert seen == [(FROZEN_TOKEN, {"alg": "HS256", "key": TEST_SIGNING_KEY})]


def test_alg_is_pinned_so_a_none_header_is_refused():
    """Algorithm confusion: a `none`-header token is refused, never verified."""
    _, payload, _ = FROZEN_TOKEN.split(".")
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
        verify_token(f"{forged_header}.{payload}.", TEST_SIGNING_KEY, now=FROZEN_NOW)


# --------------------------------------------------------------------------- #
# the duplicate is gone (mechanical, so it cannot silently return)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name",
    ["_b64url_encode", "_b64url_decode", "_sign", "_HEADER", "_JSON_KW"],
)
def test_no_hand_rolled_codec_pieces_remain(name):
    assert not hasattr(authn, name), (
        f"{name} is back in gateway/mcp/authn.py - the HS256 codec is "
        "identity/sso/jose.py and must not be re-implemented (issue #1204)"
    )
