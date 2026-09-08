"""Rate limit on every MCP call (injected gate consuming gateway/limits).

Issue #20 criterion 3: a rate limit applies on EVERY tool call. The shipped
:class:`LimitsRateGate` adapts the gateway/limits token-bucket ``RateLimiter``
(issue #19) - this suite exercises the real adapter, plus the explicit
no-fail-open property (a limited call is never served).
"""

from __future__ import annotations

from mcp.audit import HashChainAuditLog
from mcp.protocol import RATE_LIMITED
from mcp.rategate import LimitsRateGate


def _error_code(resp: dict) -> int:
    return resp["error"]["code"]


def _rate_gate(limit: int = 2):
    from limits.ratelimit import RateLimitPolicy, RateLimiter

    limiter = RateLimiter(default_policy=RateLimitPolicy(limit=limit, window_seconds=60))
    return LimitsRateGate(limiter)


def test_rate_limit_exceeded_denied(make_gateway, mint):
    audit = HashChainAuditLog()
    gateway = make_gateway(rate_gate=_rate_gate(limit=2), audit=audit)
    token = mint("acme", "agent-a", allowed_tools=("kb.summary",))

    assert "error" not in gateway.call_tool("kb.summary", {}, session_token=token)
    assert "error" not in gateway.call_tool("kb.summary", {}, session_token=token)
    third = gateway.call_tool("kb.summary", {}, session_token=token)
    assert _error_code(third) == RATE_LIMITED
    assert "rate limit" in third["error"]["message"].lower()

    # The limited call is audited as a denial with status 'rate' (who/what/
    # tenant/result), and every call wrote exactly one record (2 ok + 1 denied).
    records = audit.events()
    assert len(records) == 3
    denied = records[-1]
    assert denied["event"] == "tool_call_denied"
    assert denied["status"] == "rate"
    assert denied["tenantId"] == "acme"
    assert denied["actor"] == "agent-a"
    assert denied["detail"]["tool"] == "kb.summary"


def test_rate_scope_is_isolated_per_tenant_agent_tool(make_gateway, mint):
    gateway = make_gateway(rate_gate=_rate_gate(limit=1))
    token = mint(
        "acme", "agent-a", allowed_tools=("kb.summary", "platform.whoami")
    )
    assert "error" not in gateway.call_tool("kb.summary", {}, session_token=token)
    # Different tool => different scope bucket => still allowed.
    assert "error" not in gateway.call_tool(
        "platform.whoami", {}, session_token=token
    )
    # Same tool again => bucket now empty => denied.
    assert _error_code(
        gateway.call_tool("kb.summary", {}, session_token=token)
    ) == RATE_LIMITED


def test_other_agent_not_starved_by_bucket_use(make_gateway, mint):
    gateway = make_gateway(rate_gate=_rate_gate(limit=1))
    agent_a = mint("acme", "agent-a", allowed_tools=("kb.summary",))
    agent_b = mint("acme", "agent-b", allowed_tools=("kb.summary",))
    assert "error" not in gateway.call_tool("kb.summary", {}, session_token=agent_a)
    # agent-b's bucket is independent - not starved by agent-a's consumption.
    assert "error" not in gateway.call_tool("kb.summary", {}, session_token=agent_b)


def test_no_rate_gate_ships_disabled(make_gateway, mint):
    """Rate limiting is flag-gated: no gate injected => calls are not limited."""
    gateway = make_gateway(rate_gate=None)
    token = mint("acme", "agent-a", allowed_tools=("kb.summary",))
    for _ in range(5):
        assert "error" not in gateway.call_tool("kb.summary", {}, session_token=token)
