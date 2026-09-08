"""Auth tests — short-lived per-tenant session tokens (issue #41 AC3)."""

from __future__ import annotations

import pytest

from aosdk.auth import (
    SessionToken,
    TokenSource,
    bearer_token,
    token_from_env,
    verify_not_expired,
)
from aosdk.errors import ConfigurationError, UnauthorizedError

from _fakes import mint_session_token


def test_env_token_source_reads_and_clears(monkeypatch):
    monkeypatch.setenv("AGENTORCH_SESSION_TOKEN", mint_session_token("acme"))
    source = TokenSource()
    assert source.require().startswith("eyJ")
    assert source.token() is not None
    assert source.bearer().startswith("Bearer ")

    monkeypatch.delenv("AGENTORCH_SESSION_TOKEN")
    assert token_from_env() is None
    with pytest.raises(ConfigurationError):
        source.require()


def test_custom_env_var_name(monkeypatch):
    monkeypatch.setenv("MY_ORG_TOKEN", mint_session_token("globex"))
    assert TokenSource(env_var="MY_ORG_TOKEN").token() is not None


def test_callback_wins_then_falls_back_to_env(monkeypatch):
    monkeypatch.delenv("AGENTORCH_SESSION_TOKEN", raising=False)
    called = []
    source = TokenSource(callback=lambda: (called.append(1), mint_session_token("acme"))[1])
    assert source.token() is not None
    assert len(called) == 1


def test_require_without_any_source_raises(monkeypatch):
    monkeypatch.delenv("AGENTORCH_SESSION_TOKEN", raising=False)
    with pytest.raises(ConfigurationError):
        TokenSource(callback=lambda: None).require()


def test_session_token_parses_scoped_claims():
    token = mint_session_token("acme", subject="alice", role="admin", agent_id="a1")
    session = SessionToken.parse(token)
    assert session.tenant_id == "acme"
    assert session.subject == "alice"
    assert session.role == "admin"
    assert session.agent_id == "a1"
    assert session.expired is False
    assert session.raw_claims["iss"] == "https://auth.example.test"


def test_session_token_rejects_missing_tenant():
    # A token whose payload parses but carries no tenantId claim is rejected
    # (a session token is never tenant-less — issue #35/#10 vocabulary).
    import base64
    import json

    def enc(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    token = f"{enc({'alg': 'HS256'})}.{enc({'sub': 'u-1'})}.sample-signature"
    with pytest.raises(ValueError):
        SessionToken.parse(token)


def test_verify_not_expired_rejects_expired_and_malformed():
    expired = mint_session_token("acme", ttl=-10)
    with pytest.raises(UnauthorizedError):
        verify_not_expired(expired)
    with pytest.raises(UnauthorizedError):
        verify_not_expired("not-a-token")
    with pytest.raises(UnauthorizedError):
        verify_not_expired("h.eyJub3Rqc29uIn0.sig")


def test_bearer_token_builds_header():
    assert bearer_token("abc.def.ghi") == "Bearer abc.def.ghi"
