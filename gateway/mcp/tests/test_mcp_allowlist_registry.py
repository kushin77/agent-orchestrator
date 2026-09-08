"""Tool allowlist + registry: declared capabilities with schemas; fail closed.

Issue #20 criterion 2: a tool is a declared capability with a JSON schema;
unknown tools are rejected (fail closed) and the session's ``allowedTools``
allowlist bounds what a caller may invoke. tools/list enumerates determinis-
tically and narrows to the session allowlist when a session is presented.
"""

from __future__ import annotations

from mcp.protocol import METHOD_NOT_FOUND, TOOL_NOT_ALLOWED
from mcp.tools import declared_tools


def _error_code(resp: dict) -> int:
    return resp["error"]["code"]


def test_unknown_tool_rejected_fail_closed(make_gateway, mint):
    gateway = make_gateway()
    token = mint("acme", "agent-a", allowed_tools=("kb.summary",))
    resp = gateway.call_tool("kb.delete_all", {}, session_token=token)
    assert _error_code(resp) == METHOD_NOT_FOUND
    assert "unknown tool" in resp["error"]["message"]


def test_declared_tool_schemas_enumerated_sorted(make_gateway):
    gateway = make_gateway()
    resp = gateway.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    tools = resp["result"]["tools"]
    names = [tool["name"] for tool in tools]
    assert names == sorted(names)
    assert set(names) == set(declared_tools())
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert isinstance(tool["description"], str)


def test_session_allowlist_denies_unlisted_tool(make_gateway, mint, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = mint("acme", "agent-a", allowed_tools=("kb.summary",))
    resp = gateway.call_tool(
        "code.search", {"q": "charge"}, session_token=token
    )
    assert _error_code(resp) == TOOL_NOT_ALLOWED
    assert "allowlist" in resp["error"]["message"].lower()


def test_session_allowlist_allows_listed_tool(make_gateway, mint, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = mint("acme", "agent-a", allowed_tools=("kb.summary",))
    resp = gateway.call_tool("kb.summary", {}, session_token=token)
    assert "error" not in resp


def test_empty_allowlist_denies_every_tool(make_gateway, mint):
    gateway = make_gateway()
    token = mint("acme", "agent-a", allowed_tools=())
    resp = gateway.call_tool("platform.whoami", {}, session_token=token)
    assert _error_code(resp) == TOOL_NOT_ALLOWED


def test_tools_list_narrowed_by_session_allowlist(make_gateway, mint):
    gateway = make_gateway()
    public = gateway.list_tools()["result"]["tools"]
    assert len(public) == len(declared_tools())
    token = mint("acme", "agent-a", allowed_tools=("kb.summary",))
    narrowed = gateway.list_tools(session_token=token)["result"]["tools"]
    assert [tool["name"] for tool in narrowed] == ["kb.summary"]


def test_tools_list_rejects_invalid_session(make_gateway):
    gateway = make_gateway()
    invalid_cred = "not-a-real-session-token"
    resp = gateway.list_tools(session_token=invalid_cred)
    assert "error" in resp
    assert resp["error"]["code"] == -32002
