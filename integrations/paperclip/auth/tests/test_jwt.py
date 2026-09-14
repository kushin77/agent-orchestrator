"""HS256 JWT mint/verify behaviour (issue #412)."""

from __future__ import annotations

import base64
import json

import pytest

from integrations.paperclip.auth import jwt as jwt_mod
from integrations.paperclip.auth.model import AuthError

AUD = "paperclip"
ISS = "agent-orchestrator"


def _claims(now: int) -> dict:
    return {
        "iss": ISS,
        "sub": "paperclip",
        "aud": AUD,
        "iat": now,
        "exp": now + 900,
        "jti": "j1",
    }


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_round_trip(key: str, now: int) -> None:
    token = jwt_mod.sign(_claims(now), key)
    claims = jwt_mod.verify(token, secret=key, now=now, audience=AUD, issuer=ISS)
    assert claims["sub"] == "paperclip"
    assert claims["aud"] == AUD


def test_peek_claims_is_unverified(key: str, now: int) -> None:
    token = jwt_mod.sign(_claims(now), key)
    # peek reads the payload without checking the signature — routing only
    assert jwt_mod.peek_claims(token)["sub"] == "paperclip"


def test_signature_tamper_refused(key: str, now: int) -> None:
    token = jwt_mod.sign(_claims(now), key)
    header, payload, _ = token.split(".")
    forged = jwt_mod.sign(_claims(now), key + "-wrong")
    bad = ".".join([header, payload, forged.split(".")[2]])
    with pytest.raises(AuthError) as exc:
        jwt_mod.verify(bad, secret=key, now=now, audience=AUD, issuer=ISS)
    assert exc.value.status == 401
    assert exc.value.code == "invalid_token"


def test_alg_none_refused(key: str, now: int) -> None:
    header = _b64u(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload = _b64u(json.dumps(_claims(now)).encode())
    token = f"{header}.{payload}."
    with pytest.raises(AuthError) as exc:
        jwt_mod.verify(token, secret=key, now=now, audience=AUD, issuer=ISS)
    assert exc.value.code == "invalid_token"
    assert "algorithm" in exc.value.message


def test_expired_refused(key: str, now: int) -> None:
    token = jwt_mod.sign(_claims(now - 10_000), key)
    with pytest.raises(AuthError) as exc:
        jwt_mod.verify(token, secret=key, now=now, audience=AUD, issuer=ISS)
    assert exc.value.status == 401
    assert exc.value.code == "token_expired"


def test_wrong_audience_and_issuer_refused(key: str, now: int) -> None:
    token = jwt_mod.sign(_claims(now), key)
    with pytest.raises(AuthError):
        jwt_mod.verify(token, secret=key, now=now, audience="other", issuer=ISS)
    with pytest.raises(AuthError):
        jwt_mod.verify(token, secret=key, now=now, audience=AUD, issuer="other")


def test_malformed_refused(key: str, now: int) -> None:
    for bad in ("", "a.b", "a.b.c.d", "not.a.token"):
        with pytest.raises(AuthError):
            jwt_mod.verify(bad, secret=key, now=now, audience=AUD, issuer=ISS)


def test_missing_subject_refused(key: str, now: int) -> None:
    claims = _claims(now)
    del claims["sub"]
    token = jwt_mod.sign(claims, key)
    with pytest.raises(AuthError):
        jwt_mod.verify(token, secret=key, now=now, audience=AUD, issuer=ISS)
