"""Pytest bootstrap + shared fixtures for the gateway/mcp test suite.

``gateway/`` has no ``__init__.py``, so this prepends ``gateway/`` (three
levels up) so ``mcp`` - and its sibling ``limits``, consumed by the rate-gate
adapter - resolve no matter where pytest is invoked from. Prepending is
required so this package wins over any third-party ``mcp`` in site-packages
(the gateway/limits lesson from issue #19).

``identity/`` is appended (not prepended) so tests that consume the real
identity/rbac scope gate (``RbacScopeGuard``) can ``import rbac`` without
shadowing anything.

Keep this conftest free of sibling test constants - pytest shares the plain
module name ``conftest`` across test directories, so ``from conftest import
...`` is ambiguous when two suites run in the same invocation (issue #10
lesson). Shared builders live here as fixtures only.
"""

from __future__ import annotations

import os
import sys

import pytest

_here = os.path.dirname(os.path.abspath(__file__))
# gateway/mcp/tests -> gateway/mcp -> gateway
_gateway_root = os.path.dirname(os.path.dirname(_here))
if _gateway_root not in sys.path:
    sys.path.insert(0, _gateway_root)
_identity_root = os.path.join(_gateway_root, "..", "identity")
if _identity_root not in sys.path:
    sys.path.append(_identity_root)

from mcp import authn
from mcp.audit import HashChainAuditLog
from mcp.authz import AuthzDecision
from mcp.gateway import MCPToolGateway
from mcp.kb import KbRegistry, RepoIndex, Symbol, TenantKb
from mcp.registry import ToolRegistry
from mcp.tools import build_registry

SIGNING_KEY = bytes(range(32))

# --------------------------------------------------------------------------- #
# authorization guards (deterministic; the real rbac adapter has its own suite)
# --------------------------------------------------------------------------- #
class AllowAllGuard:
    """PermissionGuard that allows every call (isolates other enforcement)."""

    def authorize(self, session, permission: str) -> AuthzDecision:
        return AuthzDecision(allowed=True, permission=permission)


class DenyAllGuard:
    """PermissionGuard that denies every call at the permission gate."""

    def authorize(self, session, permission: str) -> AuthzDecision:
        return AuthzDecision(
            allowed=False, permission=permission, reason="permission", code="denied"
        )


# --------------------------------------------------------------------------- #
# fixture plumbing
# --------------------------------------------------------------------------- #
@pytest.fixture
def signing_key() -> bytes:
    return SIGNING_KEY


@pytest.fixture
def allow_guard() -> AllowAllGuard:
    return AllowAllGuard()


@pytest.fixture
def deny_guard() -> DenyAllGuard:
    return DenyAllGuard()


@pytest.fixture
def mint() -> object:
    """Factory: mint a session token for (tenant, agent) with scoped claims."""

    def _mint(
        tenant_id: str,
        agent_id: str,
        *,
        subject: str | None = None,
        role: str = "agent",
        allowed_tools: tuple = (),
        ttl_seconds: int = 3600,
        now: int | None = None,
    ) -> str:
        session = authn.mint_session(
            tenant_id,
            agent_id,
            SIGNING_KEY,
            role=role,
            allowed_tools=tuple(allowed_tools),
            subject=subject,
            ttl_seconds=ttl_seconds,
            now=now,
        )
        return authn.session_to_token(session, SIGNING_KEY)

    return _mint


def _seed_kb(registry: KbRegistry, tenant_id: str, repos: dict) -> None:
    kb = TenantKb(tenant_id=tenant_id)
    for repo_name, spec in repos.items():
        index = RepoIndex(
            repo=repo_name,
            modules=spec.get("modules", []),
            commit=spec.get("commit", "abc123"),
            indexed_at=spec.get("indexed_at", "2026-09-08T00:00:00Z"),
            status=spec.get("status", "ok"),
        )
        for symbol_name, symbol_spec in spec.get("symbols", {}).items():
            index.add_symbol(
                Symbol(
                    name=symbol_name,
                    repo=repo_name,
                    kind=symbol_spec.get("kind", "function"),
                    path=symbol_spec.get("path", f"src/{repo_name}.py"),
                    line=symbol_spec.get("line", 1),
                    references=symbol_spec.get("references", []),
                )
            )
        kb.add_repo(index)
    registry.put(kb)


@pytest.fixture
def two_tenant_kb() -> KbRegistry:
    """A KbRegistry with tenant 'acme' and tenant 'globex' fake indexes."""
    registry = KbRegistry()
    _seed_kb(
        registry,
        "acme",
        {
            "acme/payments": {
                "modules": ["payments.api", "payments.core"],
                "symbols": {
                    "charge": {
                        "kind": "function",
                        "path": "src/payments/core.py",
                        "line": 12,
                        "references": [
                            {"path": "src/payments/api.py", "line": 40,
                             "context": "charge(account)"}
                        ],
                    },
                    "refund": {
                        "kind": "function",
                        "path": "src/payments/core.py",
                        "line": 55,
                    },
                },
            },
            "acme/ledger": {
                "modules": ["ledger.entries"],
                "symbols": {
                    "post_entry": {"kind": "function", "path": "src/ledger/entries.py", "line": 3}
                },
            },
        },
    )
    _seed_kb(
        registry,
        "globex",
        {
            "globex/secret-project": {
                "modules": ["secret.alpha"],
                "symbols": {
                    "launch_codes": {
                        "kind": "function",
                        "path": "src/secret/alpha.py",
                        "line": 99,
                    }
                },
            }
        },
    )
    return registry


@pytest.fixture
def make_gateway() -> object:
    """Factory: build an MCPToolGateway over the declared tools + a KB registry."""

    def _make(
        *,
        authz=AllowAllGuard(),
        kb_registry: KbRegistry | None = None,
        audit=None,
        rate_gate=None,
        registry: ToolRegistry | None = None,
        signing_key: bytes = SIGNING_KEY,
    ) -> MCPToolGateway:
        return MCPToolGateway(
            registry or build_registry(),
            kb_registry=kb_registry if kb_registry is not None else KbRegistry(),
            authz=authz,
            audit=audit,
            rate_gate=rate_gate,
            signing_key=signing_key,
        )

    return _make


@pytest.fixture
def in_memory_audit() -> HashChainAuditLog:
    return HashChainAuditLog()
