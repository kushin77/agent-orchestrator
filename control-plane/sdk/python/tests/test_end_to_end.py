"""End-to-end offline quickstart walkthrough (issue #41 AC4).

Mirrors the "5-minute governed agent in your repo" path from
``control-plane/sdk/README.md`` against the offline platform doubles: an
env-supplied short-lived session token, a typed gateway dispatch (+ stream),
the control-plane usage + audit export, and the MCP bootstrap.  Ends with the
per-tenant isolation negative (no cross-tenant fallback).  Nothing touches the
network; the printed summary doubles as runnable evidence.
"""

from __future__ import annotations

from aosdk.auth import TokenSource
from aosdk.controlplane import ControlPlaneClient
from aosdk.errors import ApiError
from aosdk.gateway import GatewayClient
from aosdk.mcp import McpClient
from aosdk.model import DispatchEvent, TaskResult

from _fakes import (
    FakeControlPlane,
    FakeGatewayBackend,
    FakeMcpTransport,
    mint_session_token,
)


def test_quickstart_governed_agent_offline(monkeypatch, capsys):
    # --- step 0: a short-lived per-tenant session token from the environment ---
    monkeypatch.setenv("AGENTORCH_SESSION_TOKEN", mint_session_token("acme", role="admin"))
    token_source = TokenSource()

    # --- step 1: typed gateway dispatch ---------------------------------------
    gateway = GatewayClient(FakeGatewayBackend(), token_source=token_source)
    review = gateway.run("coder-agent", "code-review-verdict", input_={"diff": "..."})
    assert review.served()
    assert review.content["verdict"] == "approve"
    print("quickstart: typed gateway dispatch served:", review.outcome, review.content["verdict"])

    # --- step 2: streaming dispatch -------------------------------------------
    streamed = list(gateway.stream("coder-agent", "code-review-verdict", input_={"diff": "..."}))
    events = [c for c in streamed if isinstance(c, DispatchEvent)]
    terminal = [c for c in streamed if isinstance(c, TaskResult)][0]
    assert terminal.served() and len(events) == 6
    print("quickstart: streaming dispatch events:", len(events), "-> terminal", terminal.outcome)

    # --- step 3: control-plane usage + audit export -----------------------------
    control = ControlPlaneClient(FakeControlPlane(), token_source=token_source)
    usage = control.my_usage()
    assert usage.tenant_id == "acme" and usage.calls == 42
    audit = control.export_audit()
    assert len(audit) == 2
    print(
        "quickstart: usage calls=", usage.calls,
        "budget=", usage.budget.action if usage.budget else None,
        "audit records=", len(audit),
    )

    # --- step 4: MCP bootstrap --------------------------------------------------
    mcp = McpClient(FakeMcpTransport(), token_source=token_source)
    assert mcp.initialize()["protocolVersion"] == "2024-11-05"
    tools = {t.name for t in mcp.list_tools()}
    assert "platform.whoami" in tools and "kb.summary" in tools
    who = mcp.call_tool("platform.whoami")
    assert '"tenantId": "acme"' in who.text
    print("quickstart: mcp tools=", len(tools), "whoami tenant=acme ok")

    # --- step 5: per-tenant isolation (no cross-tenant fallback) -----------------
    other = ControlPlaneClient(
        FakeControlPlane(),
        token_source=TokenSource(callback=lambda: mint_session_token("globex", role="admin")),
    )
    try:
        other._call("GET", "/v1/tenants/acme/usage")
        raise AssertionError("cross-tenant usage must be refused")
    except ApiError as exc:
        assert exc.status == 403 and exc.code == "scope_denied"
    print("quickstart: cross-tenant read refused (403 scope_denied)")

    # --- summary ---------------------------------------------------------------
    captured = capsys.readouterr().out
    assert "quickstart:" in captured
    print("quickstart: offline governed-agent path OK")
