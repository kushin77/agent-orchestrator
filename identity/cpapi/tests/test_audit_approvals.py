"""Audit-ledger + approval-workflow tests.

Every mutation must append to the audit ledger (issue #38 AC3) and destructive
operations are approval-gated (govctl-style). These tests verify both.
"""

import pytest

from cpapi.fakes import build_test_app


@pytest.fixture()
def rig():
    return build_test_app(tenant_id="acme", admin_subject="u_admin")


def _approved_retire(rig, admin):
    """Run the full retire flow for ``worker-1`` and return the retire envelope."""
    rig.app.handle("POST", "/v1/agents/worker-1/retire", principal=admin, body={"reason": "decommission"})
    pending = rig.approvals.store.list(status="pending")[0]
    rig.authorizer.add_principal("u_approver", "acme", "approval:approve")
    approver = rig.principal("acme", "u_approver")
    rig.app.handle("POST", f"/v1/approvals/{pending.approvalId}/approve", principal=approver, body={})
    return rig.app.handle("POST", "/v1/agents/worker-1/retire", principal=admin, body={"reason": "decommission"})


# --- audit --------------------------------------------------------------------


def test_mutations_append_audit_records(rig):
    admin = rig.principal("acme", "u_admin")
    rig.app.handle("POST", "/v1/agents", principal=admin, body={"agentId": "worker-1", "profileRef": "coder"})
    rig.app.handle("POST", "/v1/agents/worker-1/activate", principal=admin, body={})
    actions = [a.action for a in rig.audit.records]
    assert "registry.register" in actions
    assert "registry.activate" in actions
    assert all(a.actor == "user:u_admin" for a in rig.audit.records)
    assert all(a.tenantId == "acme" for a in rig.audit.records)


def test_audit_query_filters(rig):
    admin = rig.principal("acme", "u_admin")
    rig.app.handle("POST", "/v1/agents", principal=admin, body={"agentId": "worker-1", "profileRef": "coder"})
    rig.app.handle("POST", "/v1/agents/worker-1/activate", principal=admin, body={})

    envelope = rig.app.handle("GET", "/v1/audit", principal=admin, query={"action": "registry.register"})
    assert envelope["status"] == 200
    assert envelope["data"]["count"] == 1
    assert envelope["data"]["items"][0]["action"] == "registry.register"

    envelope = rig.app.handle("GET", "/v1/audit", principal=admin, query={"actor": "user:u_admin"})
    assert envelope["data"]["count"] == 2


def test_audit_query_cross_tenant_scope_denied(rig):
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle("GET", "/v1/audit", principal=admin, query={"tenantId": "globex"})
    assert envelope["status"] == 403
    assert envelope["error"]["code"] == "scope_denied"


def test_destructive_retire_is_audited_with_approval_id(rig):
    admin = rig.principal("acme", "u_admin")
    rig.app.handle("POST", "/v1/agents", principal=admin, body={"agentId": "worker-1", "profileRef": "coder"})
    envelope = _approved_retire(rig, admin)
    assert envelope["status"] == 200
    retire_audits = [a for a in rig.audit.records if a.action == "registry.retire"]
    assert len(retire_audits) == 1
    assert retire_audits[0].detail["approvalId"]
    # approval.request + approval.approve are also audited.
    assert any(a.action == "approval.request" for a in rig.audit.records)
    assert any(a.action == "approval.approve" for a in rig.audit.records)


# --- approvals ----------------------------------------------------------------


def test_approvals_list_reflects_statuses(rig):
    admin = rig.principal("acme", "u_admin")
    rig.app.handle("POST", "/v1/agents", principal=admin, body={"agentId": "worker-1", "profileRef": "coder"})
    rig.app.handle("POST", "/v1/agents/worker-1/retire", principal=admin, body={"reason": "x"})

    envelope = rig.app.handle("GET", "/v1/approvals", principal=admin, query={"status": "pending"})
    assert envelope["status"] == 200
    assert envelope["data"]["count"] == 1
    assert envelope["data"]["items"][0]["action"] == "agent.retire"


def test_denied_approval_never_executes(rig):
    admin = rig.principal("acme", "u_admin")
    rig.app.handle("POST", "/v1/agents", principal=admin, body={"agentId": "worker-1", "profileRef": "coder"})

    first = rig.app.handle("POST", "/v1/agents/worker-1/retire", principal=admin, body={"reason": "x"})
    approval_id = first["data"]["approval"]["approvalId"]

    rig.authorizer.add_principal("u_approver", "acme", "approval:approve")
    approver = rig.principal("acme", "u_approver")
    denied = rig.app.handle("POST", f"/v1/approvals/{approval_id}/deny", principal=approver, body={})
    assert denied["status"] == 200
    assert denied["data"]["status"] == "denied"

    # The requester retries; the denied approval does not authorize execution,
    # so a fresh pending approval is created and the agent is still alive.
    retry = rig.app.handle("POST", "/v1/agents/worker-1/retire", principal=admin, body={"reason": "x"})
    assert retry["status"] == 202
    assert retry["data"]["status"] == "approval_required"
    assert rig.agent_ops.get("acme", "worker-1").status == "registered"
    assert any(e.type == "approval.denied" for e in rig.outbox.records)


def test_approve_and_deny_publish_decision_events(rig):
    admin = rig.principal("acme", "u_admin")
    rig.app.handle("POST", "/v1/agents", principal=admin, body={"agentId": "worker-1", "profileRef": "coder"})
    rig.app.handle("POST", "/v1/agents/worker-1/retire", principal=admin, body={"reason": "x"})
    pending = rig.approvals.store.list(status="pending")[0]
    rig.authorizer.add_principal("u_approver", "acme", "approval:approve")
    approver = rig.principal("acme", "u_approver")
    rig.app.handle("POST", f"/v1/approvals/{pending.approvalId}/approve", principal=approver, body={})
    types = {e.type for e in rig.outbox.records}
    assert "approval.approved" in types
