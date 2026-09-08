"""Declared indexing tools are callable against the tenant fake index.

Issue #20 criteria 5+6: definitions / references / search / query / freshness
(plus summary) are declared tools with schemas, backed by a per-tenant fake KB
registry so the gateway is fully exercisable offline. Results are canonical,
and every lookup is tenant-scoped.
"""

from __future__ import annotations

import json


def _body(resp: dict) -> dict:
    return json.loads(resp["result"]["content"][0]["text"])


def _ok(resp: dict) -> bool:
    return "error" not in resp


def _token(mint):
    return mint(
        "acme",
        "code-agent",
        allowed_tools=(
            "code.definitions",
            "code.references",
            "code.search",
            "kb.query",
            "kb.freshness",
            "kb.summary",
        ),
    )


def test_definitions_found_and_missing(make_gateway, mint, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = _token(mint)
    found = gateway.call_tool(
        "code.definitions", {"symbol": "charge"}, session_token=token
    )
    assert _ok(found)
    body = _body(found)
    assert body["count"] == 1
    assert body["hits"][0]["repo"] == "acme/payments"
    assert body["hits"][0]["fidelity"] == "semantic"

    missing = gateway.call_tool(
        "code.definitions", {"symbol": "does_not_exist"}, session_token=token
    )
    assert _ok(missing)
    assert _body(missing)["count"] == 0


def test_references_resolve(make_gateway, mint, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = _token(mint)
    resp = gateway.call_tool(
        "code.references", {"symbol": "charge"}, session_token=token
    )
    assert _ok(resp)
    body = _body(resp)
    assert body["count"] == 1
    assert body["hits"][0]["reference"]["path"] == "src/payments/api.py"


def test_search_substring(make_gateway, mint, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = _token(mint)
    resp = gateway.call_tool("code.search", {"q": "post"}, session_token=token)
    assert _ok(resp)
    names = [hit["name"] for hit in _body(resp)["hits"]]
    assert names == ["post_entry"]


def test_kb_query_full_graph_and_filters(make_gateway, mint, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = _token(mint)
    full = _body(gateway.call_tool("kb.query", {}, session_token=token))
    assert full["query"]["tenantId"] == "acme"
    assert set(full["query"]["repos"]) == {"acme/payments", "acme/ledger"}

    repo = _body(
        gateway.call_tool(
            "kb.query", {"repo": "acme/ledger"}, session_token=token
        )
    )
    assert set(repo["query"]["repos"]) == {"acme/ledger"}

    module = _body(
        gateway.call_tool(
            "kb.query", {"module_id": "payments.core"}, session_token=token
        )
    )
    assert module["query"]["repos"]["acme/payments"]["modules"] == [
        "payments.core"
    ]

    absent = _body(
        gateway.call_tool(
            "kb.query", {"module_id": "secret.alpha"}, session_token=token
        )
    )
    assert absent["query"]["repos"] == {}


def test_kb_freshness_known_and_unknown(make_gateway, mint, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = _token(mint)
    known = _body(
        gateway.call_tool(
            "kb.freshness", {"repo": "acme/payments"}, session_token=token
        )
    )
    assert known["status"] == "ok"
    assert known["commit"] == "abc123"

    unknown = _body(
        gateway.call_tool(
            "kb.freshness", {"repo": "acme/never-indexed"}, session_token=token
        )
    )
    assert unknown["notIndexed"] is True


def test_kb_summary_counts(make_gateway, mint, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = _token(mint)
    summary = _body(gateway.call_tool("kb.summary", {}, session_token=token))
    assert summary["tenantId"] == "acme"
    assert summary["repos"] == 2
    assert summary["modules"] == 3
    assert summary["symbols"] == 3


def test_results_are_canonical_and_deterministic(make_gateway, mint, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = _token(mint)
    first = gateway.call_tool("kb.query", {}, session_token=token)
    second = gateway.call_tool("kb.query", {}, session_token=token)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_required_argument_enforced(make_gateway, mint, two_tenant_kb):
    gateway = make_gateway(kb_registry=two_tenant_kb)
    token = _token(mint)
    resp = gateway.call_tool("code.definitions", {}, session_token=token)
    assert "error" in resp
    assert resp["error"]["code"] == -32602
    assert "symbol" in resp["error"]["message"]
