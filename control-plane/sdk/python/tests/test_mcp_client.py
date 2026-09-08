"""MCP client bootstrap tests (issue #41 AC1)."""

from __future__ import annotations

import pytest

from aosdk.auth import TokenSource
from aosdk.errors import ConfigurationError, McpError, UnauthorizedError
from aosdk.mcp import McpClient
from aosdk.model import ToolDefinition, ToolResult

from _fakes import FakeMcpTransport, mint_session_token


def _client(token: str, transport: FakeMcpTransport = None, **kwargs):
    return McpClient(
        transport or FakeMcpTransport(),
        token_source=TokenSource(callback=lambda: token),
        **kwargs,
    )


def _token(tenant="acme", role="admin", **kwargs):
    return mint_session_token(tenant, subject="alice", role=role, **kwargs)


def test_initialize_negotiates_protocol():
    client = _client(_token())
    result = client.initialize()
    assert result["protocolVersion"] == "2024-11-05"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "agent-orchestrator-mcp"


def test_ping():
    client = _client(_token())
    assert client.ping() is True


def test_list_tools_returns_declared_catalog():
    client = _client(_token())
    tools = client.list_tools()
    assert isinstance(tools, list)
    assert all(isinstance(t, ToolDefinition) for t in tools)
    names = {t.name for t in tools}
    assert "kb.summary" in names
    assert "platform.whoami" in names
    assert "code.definitions" in names
    summary = next(t for t in tools if t.name == "code.definitions")
    assert summary.input_schema.get("required") == ["symbol"]


def test_list_tools_narrowed_by_session_allowed_tools():
    token = _token(allowed_tools=["kb.summary", "platform.whoami"])
    client = _client(token)
    names = {t.name for t in client.list_tools()}
    assert names == {"kb.summary", "platform.whoami"}


def test_call_tool_typed_result():
    client = _client(_token())
    result = client.call_tool("kb.summary", {"module_id": "core"})
    assert isinstance(result, ToolResult)
    assert result.is_error is False
    assert '"ok": true' in result.text


def test_call_whoami_echoes_tenant_context():
    client = _client(_token(tenant="acme"))
    result = client.call_tool("platform.whoami")
    assert '"tenantId": "acme"' in result.text


def test_unknown_tool_raises_mcp_error():
    client = _client(_token())
    with pytest.raises(McpError) as excinfo:
        client.call_tool("code.nonexistent")
    assert excinfo.value.code == -32601


def test_tool_not_in_session_allowlist_denied():
    client = _client(_token(allowed_tools=["kb.summary"]))
    with pytest.raises(McpError) as excinfo:
        client.call_tool("code.definitions", {"symbol": "main"})
    assert excinfo.value.code == -32602


def test_missing_token_raises_configuration_error():
    client = McpClient(FakeMcpTransport(), token_source=TokenSource(callback=lambda: None))
    with pytest.raises(ConfigurationError):
        client.list_tools()


def test_expired_session_rejected():
    client = _client(_token(ttl=-60))
    with pytest.raises(UnauthorizedError):
        client.call_tool("kb.summary")
