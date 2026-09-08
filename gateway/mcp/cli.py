#!/usr/bin/env python3
"""Offline CLI for the tenant-scoped MCP tool gateway (gateway/mcp, issue #20).

Subcommands
-----------
- ``python3 gateway/mcp/cli.py list-tools``
    Print the declared tool catalog (names + descriptions).
- ``python3 gateway/mcp/cli.py demo``
    End-to-end offline walkthrough against a real identity/rbac store and a
    real gateway/limits rate limiter: tenant-scoped calls, cross-tenant and
    unknown-tool denials, authN/authZ failures, allowlist, rate limit and the
    append-only audit ledger. Prints ``demo: OK (all assertions passed)`` and
    exits 0 when every assertion holds (mirrors the gateway/limits demo).

Everything runs in-process and offline (stdlib + PyYAML): no network, no
server, no MCP SDK required. The demo consumes, read-only, the merged
identity/rbac store and the merged gateway/limits rate limiter from this
checkout - it never modifies them.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_GATEWAY_ROOT = os.path.dirname(_HERE)  # gateway/
_REPO_ROOT = os.path.dirname(_GATEWAY_ROOT)
# Prepend gateway/ so this checkout's ``mcp`` (and sibling ``limits``) resolve
# over any third-party ``mcp`` in site-packages; append identity/ so the demo
# can consume the real ``rbac`` store (identity/ has no __init__.py).
sys.path.insert(0, _GATEWAY_ROOT)
_identity_root = os.path.join(_REPO_ROOT, "identity")
if _identity_root not in sys.path:
    sys.path.append(_identity_root)

from mcp import authn  # noqa: E402
from mcp.audit import HashChainAuditLog  # noqa: E402
from mcp.authz import RbacScopeGuard  # noqa: E402
from mcp.gateway import MCPToolGateway  # noqa: E402
from mcp.kb import KbRegistry, RepoIndex, Symbol, TenantKb  # noqa: E402
from mcp.protocol import (  # noqa: E402
    AUTHN_FAILED,
    AUTHZ_DENIED,
    METHOD_NOT_FOUND,
    RATE_LIMITED,
    TENANT_CONTEXT_REQUIRED,
    TOOL_NOT_ALLOWED,
)
from mcp.rategate import LimitsRateGate  # noqa: E402
from mcp.tools import build_registry  # noqa: E402

SIGNING_KEY = bytes(range(32))
TOOL_IDS = (
    "code.definitions",
    "code.references",
    "code.search",
    "kb.query",
    "kb.freshness",
    "kb.summary",
    "platform.whoami",
)


class Demo:
    """Collects assertions and prints one PASS/FAIL line per step."""

    def __init__(self) -> None:
        self.failures = 0
        self.steps = 0

    def check(self, label: str, condition: bool) -> None:
        self.steps += 1
        if condition:
            print(f"  PASS  {label}")
        else:
            self.failures += 1
            print(f"  FAIL  {label}")


def _seed_kb() -> KbRegistry:
    registry = KbRegistry()

    def put(tenant: str, repos: dict) -> None:
        kb = TenantKb(tenant_id=tenant)
        for repo_name, spec in repos.items():
            index = RepoIndex(
                repo=repo_name,
                modules=spec.get("modules", []),
                commit=spec.get("commit", "abc123"),
                indexed_at=spec.get("indexed_at", "2026-09-08T00:00:00Z"),
            )
            for symbol_name, sym in spec.get("symbols", {}).items():
                index.add_symbol(
                    Symbol(
                        name=symbol_name,
                        repo=repo_name,
                        kind=sym.get("kind", "function"),
                        path=sym.get("path", f"src/{repo_name}.py"),
                        line=sym.get("line", 1),
                        references=sym.get("references", []),
                    )
                )
            kb.add_repo(index)
        registry.put(kb)

    put(
        "acme",
        {
            "acme/payments": {
                "modules": ["payments.api", "payments.core"],
                "symbols": {
                    "charge": {
                        "path": "src/payments/core.py",
                        "line": 12,
                        "references": [{"path": "src/payments/api.py", "line": 40}],
                    }
                },
            }
        },
    )
    put(
        "globex",
        {
            "globex/secret-project": {
                "modules": ["secret.alpha"],
                "symbols": {
                    "launch_codes": {"path": "src/secret/alpha.py", "line": 99}
                },
            }
        },
    )
    return registry


def _seed_rbac():
    from rbac import InMemoryStore, grant_role, seed_org

    store = InMemoryStore()
    org = store.add_org("acme", "Acme", tenant_type="platform")
    seed_org(store, org)
    store.add_team("acme", "Engineering", team_id="eng")
    store.add_agent("acme", "eng", "code-agent", agent_id="code-agent")
    grant_role(store, "acme", "u_op", "agent-operator", team_id="eng")
    grant_role(store, "acme", "u_mem", "member")
    return store


def _mint(tenant: str, agent: str, **kw) -> str:
    session = authn.mint_session(
        tenant, agent, SIGNING_KEY, allowed_tools=TOOL_IDS, **kw
    )
    return authn.session_to_token(session, SIGNING_KEY)


def _text(resp: dict) -> dict:
    return json.loads(resp["result"]["content"][0]["text"])


def _code(resp: dict) -> int:
    return resp["error"]["code"]


def _demo() -> int:
    demo = Demo()
    print("gateway/mcp demo - tenant-scoped MCP tool gateway (issue #20)")
    print("=" * 70)

    store = _seed_rbac()
    kb = _seed_kb()
    audit_dir = tempfile.mkdtemp(prefix="ao20-audit-")
    audit_path = os.path.join(audit_dir, "audit.jsonl")
    audit = HashChainAuditLog(audit_path)

    from limits.ratelimit import RateLimitPolicy, RateLimiter

    gateway = MCPToolGateway(
        build_registry(),
        kb_registry=kb,
        authz=RbacScopeGuard(store),
        audit=audit,
        rate_gate=LimitsRateGate(
            RateLimiter(default_policy=RateLimitPolicy(limit=20, window_seconds=60))
        ),
        signing_key=SIGNING_KEY,
    )

    # --- protocol handshake ----------------------------------------------
    init = gateway.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    demo.check(
        "initialize advertises MCP protocol",
        init["result"]["protocolVersion"] == "2024-11-05",
    )
    listing = gateway.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tool_names = [t["name"] for t in listing["result"]["tools"]]
    demo.check(
        "tools/list enumerates the declared catalog",
        tool_names == sorted(TOOL_IDS),
    )

    # --- tenant-scoped positive calls -------------------------------------
    acme_op = _mint("acme", "code-agent", subject="u_op", role="agent-operator")
    who = _text(gateway.call_tool("platform.whoami", {}, session_token=acme_op))
    demo.check("tenant context resolved per request (acme)", who["tenantId"] == "acme")

    summary = _text(gateway.call_tool("kb.summary", {}, session_token=acme_op))
    demo.check("tenant KB summary served (acme)", summary["tenantId"] == "acme")

    defs = _text(
        gateway.call_tool("code.definitions", {"symbol": "charge"}, session_token=acme_op)
    )
    demo.check(
        "declared indexing tool (code.definitions) callable",
        defs["count"] == 1 and defs["hits"][0]["repo"] == "acme/payments",
    )

    # --- cross-tenant negatives -------------------------------------------
    mismatched = gateway.call_tool(
        "platform.whoami", {}, session_token=acme_op, tenant_id="globex"
    )
    demo.check(
        "explicit tenant mismatch denied (no cross-tenant fallback)",
        _code(mismatched) == AUTHZ_DENIED,
    )
    leak = gateway.call_tool(
        "kb.query", {"repo": "globex/secret-project"}, session_token=acme_op
    )
    leak_text = json.dumps(_text(leak), sort_keys=True)
    demo.check(
        "acme context cannot reach globex data",
        "error" not in leak and "launch_codes" not in leak_text,
    )
    unscoped = gateway.call_tool("platform.whoami", {})
    demo.check(
        "unscoped call refused (tools require tenant context)",
        _code(unscoped) == TENANT_CONTEXT_REQUIRED,
    )

    # --- unknown tool (fail closed) ---------------------------------------
    unknown = gateway.call_tool("kb.delete_all", {}, session_token=acme_op)
    demo.check(
        "unknown tool rejected (fail closed, -32601)",
        _code(unknown) == METHOD_NOT_FOUND,
    )

    # --- authN -------------------------------------------------------------
    expired = _mint(
        "acme", "code-agent", subject="u_op", role="agent-operator", ttl_seconds=-5
    )
    authn_denied = gateway.call_tool("kb.summary", {}, session_token=expired)
    demo.check("expired session denied", _code(authn_denied) == AUTHN_FAILED)
    tampered = acme_op[:-1] + ("A" if acme_op[-1] != "A" else "B")
    demo.check(
        "tampered session denied",
        _code(gateway.call_tool("kb.summary", {}, session_token=tampered)) == AUTHN_FAILED,
    )

    # --- authZ (rbac scope gate) ------------------------------------------
    member = _mint("acme", "code-agent", subject="u_mem", role="member")
    perm = gateway.call_tool("code.search", {"q": "charge"}, session_token=member)
    demo.check(
        "authZ permission denial (member lacks tool:call)",
        _code(perm) == AUTHZ_DENIED
        and perm["error"]["data"]["reason"] == "permission",
    )
    globex_op = _mint("globex", "code-agent", subject="u_op", role="agent-operator")
    scope = gateway.call_tool("code.search", {"q": "charge"}, session_token=globex_op)
    demo.check(
        "authZ scope denial (u_op unbound in globex)",
        _code(scope) == AUTHZ_DENIED and scope["error"]["data"]["reason"] == "scope",
    )

    # --- allowlist ---------------------------------------------------------
    restricted = authn.mint_session(
        "acme", "code-agent", SIGNING_KEY, role="agent-operator",
        subject="u_op", allowed_tools=(),
    )
    restricted_token = authn.session_to_token(restricted, SIGNING_KEY)
    demo.check(
        "empty allowlist denies every tool",
        _code(
            gateway.call_tool("kb.summary", {}, session_token=restricted_token)
        ) == TOOL_NOT_ALLOWED,
    )

    # --- rate limit --------------------------------------------------------
    throttle = MCPToolGateway(
        build_registry(),
        kb_registry=kb,
        authz=RbacScopeGuard(store),
        rate_gate=LimitsRateGate(
            RateLimiter(default_policy=RateLimitPolicy(limit=1, window_seconds=60))
        ),
        signing_key=SIGNING_KEY,
    )
    burst = _mint("acme", "code-agent", subject="u_op", role="agent-operator")
    first = throttle.call_tool("kb.summary", {}, session_token=burst)
    second = throttle.call_tool("kb.summary", {}, session_token=burst)
    demo.check(
        "rate limit exceeded denied",
        "error" not in first and _code(second) == RATE_LIMITED,
    )

    # --- audit -------------------------------------------------------------
    audit_records = audit.events()
    denied = [r for r in audit_records if r["event"] == "tool_call_denied"]
    demo.check("audit ledger records every call (append-only)", len(audit_records) >= 10)
    demo.check("denied calls are audited with status", len(denied) >= 4)
    tail = audit.tail()
    demo.check(
        "audit answers who/what/tenant/result",
        tail is not None
        and "tool" in tail["detail"]
        and "tenantId" in tail
        and "result" in tail["detail"],
    )
    chain_ok = True
    try:
        audit.verify()
    except Exception:  # noqa: BLE001 - demo surfaces chain state as a check
        chain_ok = False
    demo.check("audit hash chain verifies", chain_ok)

    print("-" * 70)
    print(f"audit ledger: {len(audit_records)} records -> {audit_path}")
    print(json.dumps(audit.tail(), indent=2, sort_keys=True))
    print("-" * 70)

    if demo.failures:
        print(f"demo: {demo.failures} assertion(s) FAILED of {demo.steps}")
        return 1
    print(f"demo: OK (all {demo.steps} assertions passed)")
    return 0


def _list_tools() -> int:
    registry = build_registry()
    for schema in registry.schemas():
        print(f"{schema['name']:22s} {schema['description']}")
    return 0


def main(argv: list) -> int:
    if len(argv) >= 2 and argv[1] == "demo":
        return _demo()
    if len(argv) >= 2 and argv[1] == "list-tools":
        return _list_tools()
    print(__doc__)
    return 0 if len(argv) >= 2 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
