"""Real-module wiring tests: the seams over the *merged* pillar modules.

These prove the control plane enforces authorization through the real
``identity/rbac`` guard (not a re-implementation) and drives the real
``registry/service`` facade for agent lifecycle — the "handlers call the
merged pillar modules" acceptance. Everything stays offline (in-memory rbac
store + in-memory registry with its in-memory event log); no network, no
filesystem writes.
"""

import os
import sys

import pytest

import rbac  # importable because identity/ is on sys.path (see conftest)

from cpapi.control import ControlPlane
from cpapi.fakes import (
    FakeAuthorizer,
    FakeBudgetOps,
    FakeClock,
    FakePersonaRegistry,
    FakePolicyStore,
    FakeProfileCatalog,
    FakePromptLibrary,
    FakeSessionVerifier,
    FakeTenantOps,
    build_test_app,
)
from cpapi.audit import AuditStore
from cpapi.approvals import ApprovalGate, ApprovalStore
from cpapi.outbox import Outbox
from cpapi.wiring import RbacAuthorizer, RegistryAgentOps

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _seed_real_rbac_store():
    """Org 'acme' with a cp-admin role + an org-wide binding for u_admin."""
    store = rbac.InMemoryStore()
    store.add_org("acme", "Acme", tenant_type="platform")
    admin_permissions = (
        "org:read", "agent:read", "agent:create", "agent:write", "agent:delete",
        "agent:run", "budget:read", "budget:manage", "audit:read", "prompt:read",
        "approval:read", "approval:approve", "event:read", "event:consume",
    )
    role = store.create_role(
        "acme", "cp-admin", "Control-plane Admin", permissions=admin_permissions, level="org"
    )
    store.add_binding("acme", "u_admin", "user", role.id)
    return store


def test_rbac_authorizer_allows_denies_and_scopes():
    store = _seed_real_rbac_store()
    authz = RbacAuthorizer(store)

    allowed = authz.authorize("u_admin", "acme", "agent:create")
    assert allowed.allowed

    denied = authz.authorize("u_admin", "acme", "roles:manage")
    assert not denied.allowed
    assert denied.reason == "permission"

    # A subject with no binding in the org is refused at the scope gate.
    scoped = authz.authorize("u_stranger", "acme", "agent:create")
    assert not scoped.allowed
    assert scoped.reason == "scope"


def test_full_app_enforces_via_real_rbac_guard():
    store = _seed_real_rbac_store()
    rig = build_test_app(tenant_id="acme", admin_subject="u_admin")
    rig.app.authorizer = RbacAuthorizer(store)  # swap in the real enforcement core

    admin = rig.principal("acme", "u_admin")
    ok = rig.app.handle("POST", "/v1/agents", principal=admin, body={"agentId": "worker-1", "profileRef": "coder"})
    assert ok["status"] == 201

    # A member bound only to agent:read in the real store is permission-denied.
    member_role = store.create_role("acme", "cp-reader", "Read Only", permissions=("agent:read",), level="org")
    store.add_binding("acme", "u_member", "user", member_role.id)
    member = rig.principal("acme", "u_member")
    denied = rig.app.handle("POST", "/v1/agents", principal=member, body={"agentId": "worker-2", "profileRef": "coder"})
    assert denied["status"] == 403
    assert denied["error"]["code"] == "permission_denied"

    # An unbound subject is refused at the scope gate even though the session
    # nominally names the same tenant (no binding -> out of scope).
    stranger = rig.principal("acme", "u_stranger")
    scoped = rig.app.handle("POST", "/v1/agents", principal=stranger, body={"agentId": "worker-3", "profileRef": "coder"})
    assert scoped["status"] == 403
    assert scoped["error"]["code"] == "scope_denied"


@pytest.mark.skipif(
    not os.path.isdir(os.path.join(_REPO_ROOT, "registry", "service")),
    reason="registry/service not present in this checkout",
)
def test_real_registry_agent_ops_roundtrip():
    """The registry facade (real merged module) behind the agent seam."""
    agent_ops = RegistryAgentOps.build(repo_root=_REPO_ROOT)

    agent = agent_ops.register("acme", "worker-1", "coder", actor="user:u_admin")
    assert agent.status == "registered"
    assert agent.profileRef == "coder"
    assert agent.tenantId == "acme"

    agent = agent_ops.activate("acme", "worker-1", actor="user:u_admin")
    assert agent.status == "active"

    agent = agent_ops.pause("acme", "worker-1", actor="user:u_admin")
    assert agent.status == "paused"

    listed = agent_ops.list("acme")
    assert [a.agentId for a in listed] == ["worker-1"]

    with pytest.raises(Exception) as exc:
        agent_ops.get("acme", "no-such-agent")
    assert getattr(exc.value, "status", 0) == 404


@pytest.mark.skipif(
    not os.path.isdir(os.path.join(_REPO_ROOT, "registry", "service")),
    reason="registry/service not present in this checkout",
)
def test_handle_dispatch_via_real_registry_requires_backend():
    """Dispatch through the real registry facade fails closed without a backend."""
    agent_ops = RegistryAgentOps.build(repo_root=_REPO_ROOT)
    rig = build_test_app(tenant_id="acme", admin_subject="u_admin")
    rig.app.agent_ops = agent_ops  # type: ignore[assignment]
    admin = rig.principal("acme", "u_admin")

    registered = rig.app.handle("POST", "/v1/agents", principal=admin, body={"agentId": "worker-1", "profileRef": "coder"})
    assert registered["status"] == 201

    dispatch = rig.app.handle("POST", "/v1/agents/worker-1/tasks", principal=admin,
                              body={"taskType": "classify-route", "input": {}})
    # No engine/queue backend wired -> clear 503, never an ad-hoc path.
    assert dispatch["status"] == 503
    assert dispatch["error"]["code"] == "unavailable"
