"""authN + tenant-context enforcement of the MCP tool gateway.

Issue #20 criteria: tenant context resolved per request (no cross-tenant
leakage); every tool requires tenant context (fail closed); a session/JWT-
shaped token is verified before any tool acts. Denials carry the gateway
authN/context error codes, never a fallback.
"""

from __future__ import annotations

import json

import pytest

from mcp.authn import mint_session, session_to_token, verify_token
from mcp.errors import InvalidCredentialError, SessionExpiredError
from mcp.protocol import AUTHN_FAILED, TENANT_CONTEXT_REQUIRED


def _text(resp: dict) -> dict:
    return json.loads(resp["result"]["content"][0]["text"])


def _error_code(resp: dict) -> int:
    return resp["error"]["code"]


def _tampered(token: str) -> str:
    """Return ``token`` with its signature provably altered.

    The signature's FIRST base64url character is changed, never its last. A
    32-byte HMAC encodes to 43 base64url characters, so the final character
    carries only 4 significant bits (2 are padding) and replacing it therefore
    often decodes to the **same** signature bytes: the credential is not
    tampered at all and the denial under test is never exercised. MEASURED on
    this box: 263 of 4000 minted tokens (6.6 %) were still ACCEPTED after
    ``token[:-1] + ("A"|"B")`` -- i.e. ~1 run in 15 reddened the wired gate with
    a false failure. The first character carries 6 significant bits, so changing
    it always changes the decoded signature (measured: 0 of 4000 no-ops).
    """
    head, sep, sig = token.rpartition(".")
    assert sep and sig, f"not a header.payload.signature token: {token!r}"
    return f"{head}{sep}{'B' if sig[0] != 'B' else 'C'}{sig[1:]}"


def test_initialize_and_ping_need_no_session(make_gateway):
    gateway = make_gateway()
    init = gateway.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert init["result"]["protocolVersion"] == "2024-11-05"
    ping = gateway.handle_message({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert ping["result"] == {}


def test_platform_tool_requires_tenant_context(make_gateway):
    """A tenant-scoped tool refuses to run unscoped (fail closed)."""
    gateway = make_gateway()
    resp = gateway.call_tool("platform.whoami", {})
    assert _error_code(resp) == TENANT_CONTEXT_REQUIRED
    assert "tenant context" in resp["error"]["message"]


def test_unscoped_arguments_are_not_a_tenant(make_gateway, mint, two_tenant_kb):
    """Passing a repo arg is not tenant context - still denied without a session."""
    gateway = make_gateway(kb_registry=two_tenant_kb)
    resp = gateway.call_tool(
        "kb.query", {"repo": "acme/payments"}, session_token="", tenant_id=None
    )
    assert _error_code(resp) == TENANT_CONTEXT_REQUIRED


def test_valid_session_allows_tenant_scoped_call(
    make_gateway, mint, two_tenant_kb
):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = mint("acme", "agent-a", allowed_tools=("kb.summary",))
    resp = gateway.call_tool("kb.summary", {}, session_token=token)
    assert "error" not in resp
    body = _text(resp)
    assert body["tenantId"] == "acme"


def test_whoami_resolves_tenant_per_request(make_gateway, mint):
    gateway = make_gateway()
    acme = mint("acme", "agent-a", allowed_tools=("platform.whoami",))
    globex = mint("globex", "agent-x", allowed_tools=("platform.whoami",))
    assert _text(gateway.call_tool("platform.whoami", {}, session_token=acme))[
        "tenantId"
    ] == "acme"
    assert _text(gateway.call_tool("platform.whoami", {}, session_token=globex))[
        "tenantId"
    ] == "globex"


def test_invalid_signature_denied(make_gateway, mint, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = mint("acme", "agent-a", allowed_tools=("kb.summary",))
    resp = gateway.call_tool("kb.summary", {}, session_token=_tampered(token))
    assert _error_code(resp) == AUTHN_FAILED


def test_malformed_token_denied(make_gateway, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    invalid_cred = "not-a-real-session-token"
    resp = gateway.call_tool("kb.summary", {}, session_token=invalid_cred)
    assert _error_code(resp) == AUTHN_FAILED


def test_expired_session_denied(make_gateway, mint, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = mint(
        "acme", "agent-a", allowed_tools=("kb.summary",), ttl_seconds=-10
    )
    resp = gateway.call_tool("kb.summary", {}, session_token=token)
    assert _error_code(resp) == AUTHN_FAILED
    assert "expired" in resp["error"]["message"].lower()


def test_authn_module_roundtrip_and_tamper(signing_key):
    session = mint_session("acme", "agent-a", signing_key)
    encoded = session_to_token(session, signing_key)
    decoded = verify_token(encoded, signing_key)
    assert decoded.tenant_id == "acme"
    assert decoded.agent_id == "agent-a"
    with pytest.raises(InvalidCredentialError):
        verify_token(encoded[:-2] + "xx", signing_key)
    with pytest.raises(SessionExpiredError):
        expired = mint_session(
            "acme", "agent-a", signing_key, ttl_seconds=1,
            now=1_000_000_000,
        )
        verify_token(session_to_token(expired, signing_key), signing_key,
                     now=1_000_000_002)
