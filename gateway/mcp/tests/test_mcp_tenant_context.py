"""Cross-tenant negatives + tenant-context resolution of the MCP tool gateway.

Issue #20 criterion 1: tenant context is resolved per request and there is NO
cross-tenant leakage - a call with tenant A context cannot reach tenant B data
or tools, and tools require tenant context. Data access is bound to the
session's tenant structurally (the index backend is tenant-scoped), and an
explicit ``tenantId`` that disagrees with the verified session is refused.
"""

from __future__ import annotations

import json

from mcp.protocol import AUTHZ_DENIED, TENANT_CONTEXT_REQUIRED


def _text(resp: dict) -> dict:
    return json.loads(resp["result"]["content"][0]["text"])


def _error_code(resp: dict) -> int:
    return resp["error"]["code"]


def _error_body(resp: dict) -> str:
    return json.dumps(resp["error"], sort_keys=True)


def test_explicit_tenant_mismatch_denied_no_fallback(make_gateway, mint):
    """A session for tenant A is refused when presented as tenant B."""
    gateway = make_gateway()
    token = mint("acme", "agent-a", allowed_tools=("platform.whoami",))
    resp = gateway.call_tool(
        "platform.whoami", {}, session_token=token, tenant_id="globex"
    )
    assert _error_code(resp) == AUTHZ_DENIED
    assert "cross-tenant" in _error_body(resp).lower()


def test_acme_session_cannot_read_globex_repo(make_gateway, mint, two_tenant_kb):
    """A repo that exists only in tenant B is absent from tenant A's KB."""
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = mint("acme", "agent-a", allowed_tools=("kb.query",))
    resp = gateway.call_tool(
        "kb.query", {"repo": "globex/secret-project"}, session_token=token
    )
    assert "error" not in resp
    body = _text(resp)
    text = json.dumps(body, sort_keys=True)
    # globex data must be structurally unreachable from an acme call.
    assert "globex/secret-project" not in text
    assert "launch_codes" not in text
    assert body["query"]["repos"] == {}


def test_acme_session_cannot_resolve_globex_symbol(make_gateway, mint, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = mint("acme", "agent-a", allowed_tools=("code.definitions",))
    resp = gateway.call_tool(
        "code.definitions", {"symbol": "launch_codes"}, session_token=token
    )
    assert "error" not in resp
    assert _text(resp)["count"] == 0


def test_globex_can_read_its_own_data(make_gateway, mint, two_tenant_kb):
    """Positive control: the same query with globex context returns globex data."""
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = mint("globex", "agent-x", allowed_tools=("code.definitions",))
    resp = gateway.call_tool(
        "code.definitions", {"symbol": "launch_codes"}, session_token=token
    )
    assert "error" not in resp
    body = _text(resp)
    assert body["count"] == 1
    assert body["hits"][0]["repo"] == "globex/secret-project"


def test_no_session_never_means_another_tenant(make_gateway, mint):
    """Scoped call without tenant context is denied, never widened to a tenant."""
    gateway = make_gateway()
    # Even with a tool that would otherwise succeed, no context => fail closed.
    resp = gateway.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "platform.whoami", "arguments": {}},
        }
    )
    assert _error_code(resp) == TENANT_CONTEXT_REQUIRED


def test_context_tenant_matches_session_is_allowed(make_gateway, mint):
    """Explicit tenantId equal to the session's own tenant is fine."""
    gateway = make_gateway()
    token = mint("acme", "agent-a", allowed_tools=("platform.whoami",))
    resp = gateway.call_tool(
        "platform.whoami", {}, session_token=token, tenant_id="acme"
    )
    assert "error" not in resp
    assert _text(resp)["tenantId"] == "acme"
