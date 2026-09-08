"""Full tool-call audit: one append-only hash-chained record per call.

Issue #20 criterion 4: every tool call - allowed or denied - appends one audit
record answering who/what/tenant/result to an injected append-only ledger. The
record shape (seq / ts / prevHash / hash / RFC 3339) is consumed from the
registry/events append-only contract; the ledger refuses tampering.
"""

from __future__ import annotations

import json
import os

import pytest

from mcp.audit import (
    GENESIS_HASH,
    AuditLogError,
    AuditLogIntegrityError,
    HashChainAuditLog,
)


def _text(resp: dict) -> dict:
    return json.loads(resp["result"]["content"][0]["text"])


def _error_code(resp: dict) -> int:
    return resp["error"]["code"]


# --- per-call audit content ------------------------------------------------- #
def test_allowed_call_is_audited_with_who_what_tenant_result(
    make_gateway, mint, in_memory_audit, two_tenant_kb
):
    gateway = make_gateway(
        kb_registry=two_tenant_kb, audit=in_memory_audit
    )
    token = mint(
        "acme", "agent-a", subject="principal-7",
        allowed_tools=("kb.summary",),
    )
    resp = gateway.call_tool("kb.summary", {}, session_token=token)
    assert "error" not in resp

    records = in_memory_audit.events()
    assert len(records) == 1
    record = records[0]
    assert record["seq"] == 1
    assert record["event"] == "tool_call"
    assert record["status"] == "ok"
    # who
    assert record["actor"] == "principal-7"
    assert record["agentId"] == "agent-a"
    # what
    assert record["detail"]["tool"] == "kb.summary"
    assert record["detail"]["permission"] == "tool:call"
    assert record["detail"]["arguments"] == {}
    # tenant
    assert record["tenantId"] == "acme"
    # result
    assert record["detail"]["result"]["outcome"] == "ok"
    # chain integrity
    assert record["prevHash"] == GENESIS_HASH
    assert len(record["hash"]) == 64


def test_denied_calls_are_audited_with_their_status(
    make_gateway, mint, deny_guard
):
    audit = HashChainAuditLog()
    # Gateway 1: permissive authz (default) -> allowlist / unknown / context /
    # authn denials are reachable. Gateway 2: deny_guard -> authz denial.
    g_permit = make_gateway(audit=audit)
    g_deny = make_gateway(authz=deny_guard, audit=audit)
    token = mint("acme", "agent-a", allowed_tools=("platform.whoami",))
    restricted = mint("acme", "agent-a", allowed_tools=())

    _error_code(g_permit.call_tool("platform.whoami", {}, session_token=restricted))
    _error_code(g_permit.call_tool("no.such.tool", {}, session_token=token))
    g_permit.call_tool("platform.whoami", {}, session_token="")
    invalid_cred = "not-a-real-session-token"
    g_permit.call_tool("platform.whoami", {}, session_token=invalid_cred)
    _error_code(g_deny.call_tool("platform.whoami", {}, session_token=token))

    records = audit.events()
    assert len(records) == 5
    statuses = [r["status"] for r in records]
    assert statuses == ["allowlist", "unknown", "context", "authn", "authz"]
    assert all(r["event"] == "tool_call_denied" for r in records)
    assert all(r["detail"]["result"]["outcome"] == "denied" for r in records)
    # context/authn denials carry no identity (nothing to attribute them to)
    assert records[2]["tenantId"] is None
    assert records[3]["tenantId"] is None
    assert records[0]["tenantId"] == "acme"
    assert records[-1]["tenantId"] == "acme"


def test_rate_limited_call_is_audited(make_gateway, mint):
    from mcp.rategate import LimitsRateGate
    from limits.ratelimit import RateLimitPolicy, RateLimiter

    audit = HashChainAuditLog()
    gateway = make_gateway(
        audit=audit,
        rate_gate=LimitsRateGate(
            RateLimiter(default_policy=RateLimitPolicy(limit=1, window_seconds=60))
        ),
    )
    token = mint("acme", "agent-a", allowed_tools=("kb.summary",))
    gateway.call_tool("kb.summary", {}, session_token=token)
    gateway.call_tool("kb.summary", {}, session_token=token)
    assert audit.events()[-1]["status"] == "rate"


def test_no_audit_sink_ships_disabled(make_gateway, mint):
    """Audit is injected: with no sink the gateway still serves (flag-gated)."""
    gateway = make_gateway(audit=None)
    token = mint("acme", "agent-a", allowed_tools=("platform.whoami",))
    resp = gateway.call_tool("platform.whoami", {}, session_token=token)
    assert "error" not in resp
    assert gateway.audit is None


# --- append-only ledger integrity ------------------------------------------- #
def test_hash_chain_links_and_verify(make_gateway, mint):
    audit = HashChainAuditLog()
    gateway = make_gateway(audit=audit)
    token = mint("acme", "agent-a", allowed_tools=("platform.whoami",))
    gateway.call_tool("platform.whoami", {}, session_token=token)
    gateway.call_tool("platform.whoami", {}, session_token=token)

    records = audit.events()
    assert len(records) == 2
    assert records[1]["prevHash"] == records[0]["hash"]
    assert audit.state() == (2, records[1]["hash"])
    assert audit.verify() == (2, records[1]["hash"])


def test_file_backed_log_reopens_and_detects_tamper(tmp_path):
    path = str(tmp_path / "audit.jsonl")
    log = HashChainAuditLog(path)
    log.append(
        "tool_call",
        status="ok",
        tenant_id="acme",
        agent_id="agent-a",
        actor="u",
        detail={"tool": "kb.summary", "result": {"outcome": "ok"}},
    )
    tail_state = log.state()

    reopened = HashChainAuditLog(path)
    assert reopened.verify(expected=tail_state) == tail_state
    assert len(reopened) == 1

    # Truncation: drop the only line and reopen against the old state.
    with open(path, "r", encoding="utf-8") as handle:
        lines = [line for line in handle if not line.startswith("#")]
    assert len(lines) == 1
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("")
    truncated = HashChainAuditLog(path)
    assert len(truncated) == 0
    with pytest.raises(AuditLogIntegrityError):
        truncated.verify(expected=tail_state)

    # Tamper: rewrite a record with altered content.
    os.remove(path)
    log2 = HashChainAuditLog(path)
    log2.append(
        "tool_call",
        status="ok",
        tenant_id="acme",
        agent_id="agent-a",
        actor="u",
        detail={"tool": "kb.summary", "result": {"outcome": "ok"}},
    )
    with open(path, "r", encoding="utf-8") as handle:
        raw = next(line for line in handle if not line.startswith("#"))
    record = json.loads(raw)
    record["actor"] = "attacker"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    with pytest.raises(AuditLogIntegrityError):
        HashChainAuditLog(path)


def test_ledger_closed_kind_and_status_vocabulary():
    log = HashChainAuditLog()
    with pytest.raises(AuditLogError):
        log.append("register", status="ok")
    with pytest.raises(AuditLogError):
        log.append("tool_call", status="not-a-status")
    log.append("tool_call", status="ok")
    log.append("tool_call_denied", status="authz")
    assert len(log) == 2
