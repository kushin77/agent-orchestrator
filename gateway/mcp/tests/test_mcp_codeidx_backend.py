"""The real codeidx backend behind a flag-gated seam (issue #476, ADR-0018).

The declared in-memory graph is the **offline fixture**; the real
``kushin77/code-indexing`` backend is a flag-gated option that serves the same
:class:`IndexBackend` protocol (definitions / references / search / query /
freshness) unchanged. These tests prove, offline, that:

* the flag is **OFF by default** and the declared fixture answers until opted in;
* every declared tool is served from the real backend with ``source`` =
  ``codeidx`` and the real-index fidelity note;
* an unreachable / malformed indexer **degrades explicitly and says so** - the
  envelope names the tool, and no result is invented;
* the required negative controls are refused, each naming its field;
* the real path runs behind the *same* authn / authz / allowlist / rate posture
  (no second authn or allowlist path is introduced).

A recorded fixture stands in for the indexer, so nothing here needs the network
or a running indexer; the live transport is never exercised.
"""

from __future__ import annotations

import json
import os

import pytest

from mcp.kb import (
    CODEIDX_FIDELITY_NOTE,
    CODEIDX_FLAG_ENV,
    CODEIDX_TOOLS,
    DEFAULT_CODEIDX_ENABLED,
    DEGRADED_FIDELITY_NOTE,
    FIDELITY_NOTE,
    SOURCE_CODEIDX,
    SOURCE_DECLARED,
    CodeidxBackend,
    CodeidxUnavailableError,
    KbRegistry,
    MemoryKbBackend,
    ProxyCodeidxClient,
    RecordedCodeidxClient,
    RepoIndex,
    Symbol,
    TenantKb,
    env_codeidx_enabled,
    fidelity_note_for,
)

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "codeidx_recorded.json"
)

TOOL_IDS = (
    "code.definitions",
    "code.references",
    "code.search",
    "kb.query",
    "kb.freshness",
    "kb.summary",
)


def _records() -> dict:
    with open(FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)


def _body(resp: dict) -> dict:
    return json.loads(resp["result"]["content"][0]["text"])


def _token(mint) -> str:
    return mint("acme", "code-agent", allowed_tools=TOOL_IDS)


def _declared_kb(*, codeidx_enabled: bool = False) -> KbRegistry:
    """A declared fixture whose freshness commit is NOT the real one."""
    registry = KbRegistry(codeidx_enabled=codeidx_enabled)
    kb = TenantKb(tenant_id="acme")
    index = RepoIndex(
        repo="acme/payments",
        modules=["payments.api", "payments.core"],
        commit="abc123",
        indexed_at="2026-09-08T00:00:00Z",
    )
    index.add_symbol(Symbol(name="charge", repo="acme/payments", kind="function",
                            path="src/payments/core.py", line=12))
    kb.add_repo(index)
    registry.put(kb)
    return registry


def _codeidx_kb(**client_kwargs) -> KbRegistry:
    registry = _declared_kb(codeidx_enabled=True)
    registry.opt_in("acme", RecordedCodeidxClient(_records(), **client_kwargs))
    return registry


# --------------------------------------------------------------------------- #
# the flag is OFF by default (GR-5)
# --------------------------------------------------------------------------- #
def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv(CODEIDX_FLAG_ENV, raising=False)
    assert DEFAULT_CODEIDX_ENABLED is False
    assert env_codeidx_enabled({}) is False
    assert env_codeidx_enabled({CODEIDX_FLAG_ENV: ""}) is False
    assert env_codeidx_enabled({CODEIDX_FLAG_ENV: "off"}) is False
    assert env_codeidx_enabled({CODEIDX_FLAG_ENV: "1"}) is True
    assert env_codeidx_enabled({CODEIDX_FLAG_ENV: "on"}) is True
    # The default registry (what the gateway builds) is OFF.
    assert KbRegistry().codeidx_enabled is False


def test_default_backend_is_the_declared_fixture():
    backend = _declared_kb().backend_for("acme")
    assert isinstance(backend, MemoryKbBackend)
    body = backend.definitions("charge")
    assert body["source"] == SOURCE_DECLARED
    assert body["fidelity_note"] == FIDELITY_NOTE
    assert body["count"] == 1


def test_enabled_registry_for_opted_in_tenant_serves_codeidx():
    backend = _codeidx_kb().backend_for("acme")
    assert isinstance(backend, CodeidxBackend)


# --------------------------------------------------------------------------- #
# every declared tool is served from the real backend, shapes unchanged
# --------------------------------------------------------------------------- #
def test_real_backend_serves_every_declared_tool():
    backend = _codeidx_kb().backend_for("acme")
    calls = {
        "definitions": lambda: backend.definitions("charge"),
        "references": lambda: backend.references("charge"),
        "search": lambda: backend.search("post"),
        "query": lambda: backend.query(repo="acme/payments"),
        "freshness": lambda: backend.freshness("acme/payments"),
    }
    for tool in CODEIDX_TOOLS:
        body = calls[tool]()
        assert body["source"] == SOURCE_CODEIDX, tool
        assert body["fidelity_note"] == CODEIDX_FIDELITY_NOTE, tool
        assert "degraded" not in body, tool


def test_real_backend_answers_are_the_recorded_index_not_the_fixture():
    backend = _codeidx_kb().backend_for("acme")
    real = backend.freshness("acme/payments")
    declared = _declared_kb().backend_for("acme").freshness("acme/payments")
    # The fixture commit is "abc123"; the real indexer recorded "realc0de".
    assert real["commit"] == "realc0de"
    assert declared["commit"] == "abc123"
    assert real["source"] == SOURCE_CODEIDX
    assert declared["source"] == SOURCE_DECLARED


def test_summary_is_declared_only_and_labelled():
    backend = _codeidx_kb().backend_for("acme")
    body = backend.summary()
    assert body["source"] == SOURCE_DECLARED
    assert body["fidelity_note"] == FIDELITY_NOTE


# --------------------------------------------------------------------------- #
# explicit degradation - never a silent fallback, never an invented result
# --------------------------------------------------------------------------- #
def test_unreachable_indexer_degrades_and_says_so():
    kb = _codeidx_kb(unavailable=("definitions",))
    body = kb.backend_for("acme").definitions("charge")
    assert body["degraded"] is True
    assert body["source"] == SOURCE_DECLARED
    assert body["fidelity_note"] == DEGRADED_FIDELITY_NOTE
    assert body["degradeTool"] == "definitions"
    assert "definitions" in body["degradeReason"]
    # It degraded to the declared answer - it did not invent one.
    assert body["count"] == 1


def test_malformed_indexer_envelope_degrades_naming_the_tool():
    kb = _codeidx_kb(malformed=("search",))
    body = kb.backend_for("acme").search("post")
    assert body["degraded"] is True
    assert body["degradeTool"] == "search"
    assert "malformed" in body["degradeReason"]
    assert "search" in body["degradeReason"]


def test_proxy_client_transport_failure_degrades():
    def boom(name, arguments):
        raise RuntimeError("connection refused")

    kb = _declared_kb(codeidx_enabled=True)
    kb.opt_in("acme", ProxyCodeidxClient(boom))
    body = kb.backend_for("acme").freshness("acme/payments")
    assert body["degraded"] is True
    assert body["source"] == SOURCE_DECLARED
    assert "connection refused" in body["degradeReason"]


def test_proxy_client_rejects_non_object_envelope():
    kb = _declared_kb(codeidx_enabled=True)
    kb.opt_in("acme", ProxyCodeidxClient(lambda name, args: ["not", "an", "object"]))
    body = kb.backend_for("acme").search("post")
    assert body["degraded"] is True
    assert "malformed" in body["degradeReason"]


def test_no_cross_tenant_codeidx_fallback():
    """A tenant with no real index keeps the fixture - never another's index."""
    kb = _declared_kb(codeidx_enabled=True)
    kb.opt_in("globex", RecordedCodeidxClient(_records()))
    acme = kb.backend_for("acme")
    assert isinstance(acme, MemoryKbBackend)  # no globex client leaks into acme
    body = acme.search("post")  # the codeidx record only globex has
    assert body["source"] == SOURCE_DECLARED
    assert body["count"] == 0


# --------------------------------------------------------------------------- #
# fidelity notes are accurate per path
# --------------------------------------------------------------------------- #
def test_fidelity_notes_are_distinct_and_accurate():
    assert FIDELITY_NOTE != CODEIDX_FIDELITY_NOTE
    assert FIDELITY_NOTE != DEGRADED_FIDELITY_NOTE
    assert CODEIDX_FIDELITY_NOTE != DEGRADED_FIDELITY_NOTE
    assert "fake" in FIDELITY_NOTE
    assert "real" in CODEIDX_FIDELITY_NOTE
    assert "DEGRADED" in DEGRADED_FIDELITY_NOTE
    assert "unreachable" in DEGRADED_FIDELITY_NOTE
    assert fidelity_note_for(SOURCE_CODEIDX) == CODEIDX_FIDELITY_NOTE
    assert fidelity_note_for(SOURCE_DECLARED) == FIDELITY_NOTE
    assert fidelity_note_for(SOURCE_DECLARED, degraded=True) == DEGRADED_FIDELITY_NOTE


def test_answers_are_canonical_and_deterministic():
    backend = _codeidx_kb().backend_for("acme")
    first = backend.definitions("charge")
    second = backend.definitions("charge")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# --------------------------------------------------------------------------- #
# the real path runs behind the SAME enforcement posture
# --------------------------------------------------------------------------- #
class _CountingClient(RecordedCodeidxClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = 0

    def call_tool(self, name, arguments):
        self.calls += 1
        return super().call_tool(name, arguments)


def test_codeidx_reachable_only_after_enforcement(make_gateway, mint, deny_guard):
    """An unauthorized call never reaches the real indexer (no second path)."""
    client = _CountingClient(_records())
    kb = _declared_kb(codeidx_enabled=True)
    kb.opt_in("acme", client)
    gateway = make_gateway(kb_registry=kb, authz=deny_guard)
    resp = gateway.call_tool(
        "code.definitions", {"symbol": "charge"}, session_token=_token(mint)
    )
    assert resp["error"]["code"] == -32003
    assert resp["error"]["data"]["reason"] == "permission"
    assert client.calls == 0


def test_real_backend_served_through_the_gateway(make_gateway, mint):
    kb = _codeidx_kb()
    gateway = make_gateway(kb_registry=kb)
    resp = gateway.call_tool(
        "code.definitions", {"symbol": "charge"}, session_token=_token(mint)
    )
    body = _body(resp)
    assert body["source"] == SOURCE_CODEIDX
    assert body["fidelity_note"] == CODEIDX_FIDELITY_NOTE
    assert body["count"] == 1


# --------------------------------------------------------------------------- #
# the five required negative controls, each refused and naming its field
# --------------------------------------------------------------------------- #
def test_negative_unknown_tool_refused_32601(make_gateway, mint):
    gateway = make_gateway(kb_registry=_codeidx_kb())
    resp = gateway.call_tool(
        "kb.delete_everything", {}, session_token=_token(mint)
    )
    assert resp["error"]["code"] == -32601
    assert "kb.delete_everything" in resp["error"]["message"]


def test_negative_malformed_envelope_refused(make_gateway):
    gateway = make_gateway(kb_registry=_codeidx_kb())
    resp = gateway.handle_message(["not", "an", "object"])
    assert resp["error"]["code"] == -32600
    assert "message must be a JSON object" in resp["error"]["message"]


def test_negative_cross_tenant_no_fallback(make_gateway, mint):
    gateway = make_gateway(kb_registry=_codeidx_kb())
    resp = gateway.call_tool(
        "kb.summary", {}, session_token=_token(mint), tenant_id="globex"
    )
    assert resp["error"]["code"] == -32003
    assert "cross-tenant" in resp["error"]["message"]
    assert "globex" in resp["error"]["message"]


def test_negative_indexer_unreachable_degrades_and_says_so(make_gateway, mint):
    gateway = make_gateway(kb_registry=_codeidx_kb(unavailable=("definitions",)))
    body = _body(
        gateway.call_tool(
            "code.definitions", {"symbol": "charge"}, session_token=_token(mint)
        )
    )
    assert body["degraded"] is True
    assert body["source"] == SOURCE_DECLARED
    assert "definitions" in body["degradeReason"]


def test_negative_authorized_false_denied(make_gateway, mint, deny_guard):
    gateway = make_gateway(kb_registry=_codeidx_kb(), authz=deny_guard)
    resp = gateway.call_tool(
        "code.search", {"q": "charge"}, session_token=_token(mint)
    )
    assert resp["error"]["code"] == -32003
    assert resp["error"]["data"]["reason"] == "permission"


def test_recorded_client_unrecorded_tool_is_unavailable():
    client = RecordedCodeidxClient({"definitions": {"count": 0, "hits": []}})
    with pytest.raises(CodeidxUnavailableError):
        client.call_tool("references", {})
