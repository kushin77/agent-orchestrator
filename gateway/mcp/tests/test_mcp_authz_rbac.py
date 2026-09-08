"""authZ: the MCP gateway consumes the identity/rbac scope gate (issue #12).

The gateway authorizes through an injected :class:`PermissionGuard`. This
suite wires the real adapter (:class:`RbacScopeGuard`) over an identity/rbac
``InMemoryStore`` and proves the two-gate semantics hold on MCP tool calls:

- a subject whose role grants ``tool:call`` in scope is allowed;
- a subject in scope whose role lacks ``tool:call`` is denied with reason
  ``permission``;
- a subject with an acme grant has NO cross-tenant fallback: presented as a
  globex session it is denied at the scope gate with reason ``scope``.

(identity/ is appended to sys.path by this suite's conftest so ``rbac`` is
importable exactly as the rbac tests arrange it.)
"""

from __future__ import annotations

import json

import pytest

from mcp.authz import RbacScopeGuard
from mcp.protocol import AUTHZ_DENIED


def _error_code(resp: dict) -> int:
    return resp["error"]["code"]


def _error_data(resp: dict) -> dict:
    return resp["error"].get("data") or {}


def _rbac_store():
    from rbac import InMemoryStore, grant_role, seed_org

    store = InMemoryStore()
    org = store.add_org("acme", "Acme", tenant_type="platform")
    seed_org(store, org)
    store.add_team("acme", "Engineering", team_id="eng")
    store.add_agent("acme", "eng", "code-agent", agent_id="code-agent")
    # u_op: team-scoped agent-operator inside eng (grants tool:call there).
    grant_role(store, "acme", "u_op", "agent-operator", team_id="eng")
    # u_mem: org-wide member (in scope everywhere in acme, no tool:call).
    grant_role(store, "acme", "u_mem", "member")
    return store


@pytest.fixture
def make_rbac_gateway(make_gateway, two_tenant_kb):
    """Factory producing a gateway authorized by the real rbac scope guard."""

    def _make():
        store = _rbac_store()
        return make_gateway(
            authz=RbacScopeGuard(store), kb_registry=two_tenant_kb
        )

    return _make


def test_allowed_when_role_grants_tool_call(make_rbac_gateway, mint):
    gateway = make_rbac_gateway()
    token = mint(
        "acme", "code-agent", subject="u_op", role="agent-operator",
        allowed_tools=("code.search",),
    )
    resp = gateway.call_tool("code.search", {"q": "charge"}, session_token=token)
    assert "error" not in resp
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["count"] == 1


def test_permission_denied_when_role_lacks_tool_call(make_rbac_gateway, mint):
    gateway = make_rbac_gateway()
    # member is in scope (org-wide) but the role does not grant tool:call.
    token = mint(
        "acme", "code-agent", subject="u_mem", role="member",
        allowed_tools=("code.search",),
    )
    resp = gateway.call_tool("code.search", {"q": "charge"}, session_token=token)
    assert _error_code(resp) == AUTHZ_DENIED
    data = _error_data(resp)
    assert data["reason"] == "permission"
    assert data["code"] == "denied"


def test_scope_denied_no_cross_tenant_fallback(make_rbac_gateway, mint):
    gateway = make_rbac_gateway()
    # u_op holds agent-operator in acme - but has NO binding in globex. A
    # session claiming globex is denied at the scope gate; the acme grant does
    # not follow the subject across tenants.
    token = mint(
        "globex", "code-agent", subject="u_op", role="agent-operator",
        allowed_tools=("code.search",),
    )
    resp = gateway.call_tool("code.search", {"q": "charge"}, session_token=token)
    assert _error_code(resp) == AUTHZ_DENIED
    data = _error_data(resp)
    assert data["reason"] == "scope"
    assert data["permission"] == "tool:call"


def test_scope_denied_for_unknown_agent(make_rbac_gateway, mint):
    gateway = make_rbac_gateway()
    # An acme subject requesting an agent that does not exist in acme is out of
    # scope (fail closed), not silently allowed.
    token = mint(
        "acme", "ghost-agent", subject="u_op", role="agent-operator",
        allowed_tools=("code.search",),
    )
    resp = gateway.call_tool("code.search", {"q": "charge"}, session_token=token)
    assert _error_code(resp) == AUTHZ_DENIED
    assert _error_data(resp)["reason"] == "scope"
