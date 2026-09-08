"""Agent endpoint tests: register / get / list / lifecycle / dispatch / tasks."""

import pytest

from cpapi.fakes import build_test_app


@pytest.fixture()
def rig():
    return build_test_app(tenant_id="acme", admin_subject="u_admin")


def _register(rig, admin, agent_id="worker-1", profile="coder"):
    return rig.app.handle(
        "POST", "/v1/agents", principal=admin,
        body={"agentId": agent_id, "profileRef": profile},
    )


def test_register_agent_returns_201_and_view(rig):
    admin = rig.principal("acme", "u_admin")
    envelope = _register(rig, admin)
    assert envelope["status"] == 201
    data = envelope["data"]
    assert data["agentId"] == "worker-1"
    assert data["tenantId"] == "acme"
    assert data["profileRef"] == "coder"
    assert data["status"] == "registered"
    assert data["capabilities"] == ["code-author"]


def test_register_publishes_domain_events_to_outbox(rig):
    admin = rig.principal("acme", "u_admin")
    _register(rig, admin)
    types = {e.type for e in rig.outbox.records}
    assert "agent.registered" in types
    assert "agent.provision" in types
    assert all(e.tenant_id == "acme" for e in rig.outbox.records)


def test_get_agent_after_register(rig):
    admin = rig.principal("acme", "u_admin")
    _register(rig, admin)
    envelope = rig.app.handle("GET", "/v1/agents/worker-1", principal=admin)
    assert envelope["status"] == 200
    assert envelope["data"]["agentId"] == "worker-1"


def test_get_unknown_agent_is_404(rig):
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle("GET", "/v1/agents/nope", principal=admin)
    assert envelope["status"] == 404
    assert envelope["error"]["code"] == "unknown_agent"


def test_duplicate_register_is_conflict(rig):
    admin = rig.principal("acme", "u_admin")
    _register(rig, admin)
    envelope = _register(rig, admin)
    assert envelope["status"] == 409
    assert envelope["error"]["code"] == "already_registered"


def test_list_agents_is_tenant_scoped(rig):
    admin = rig.principal("acme", "u_admin")
    _register(rig, admin, "worker-1")
    _register(rig, admin, "reviewer-1", profile="reviewer")
    envelope = rig.app.handle("GET", "/v1/agents", principal=admin)
    assert envelope["status"] == 200
    assert envelope["data"]["count"] == 2

    # Another tenant's admin never sees acme's agents (no cross-tenant leakage).
    rig.authorizer.add_admin("g_admin", "globex")
    globex_admin = rig.principal("globex", "g_admin")
    envelope = rig.app.handle("GET", "/v1/agents", principal=globex_admin)
    assert envelope["status"] == 200
    assert envelope["data"]["count"] == 0


def test_lifecycle_activate_pause_and_dispatch(rig):
    admin = rig.principal("acme", "u_admin")
    _register(rig, admin, "worker-1")

    envelope = rig.app.handle("POST", "/v1/agents/worker-1/activate", principal=admin, body={"reason": "go live"})
    assert envelope["status"] == 200
    assert envelope["data"]["status"] == "active"

    # dispatch to an active agent -> 202 accepted task + outbox event.
    envelope = rig.app.handle(
        "POST", "/v1/agents/worker-1/tasks", principal=admin,
        body={"taskType": "classify-route", "input": {"q": "billing outage"}},
    )
    assert envelope["status"] == 202
    task_id = envelope["data"]["taskId"]
    assert task_id.startswith("task_")
    assert envelope["data"]["status"] == "PENDING"
    assert any(e.type == "task.dispatched" for e in rig.outbox.records)

    envelope = rig.app.handle("GET", f"/v1/agents/worker-1/tasks/{task_id}", principal=admin)
    assert envelope["status"] == 200
    assert envelope["data"]["taskId"] == task_id

    envelope = rig.app.handle("POST", "/v1/agents/worker-1/pause", principal=admin, body={"reason": "quiet hours"})
    assert envelope["status"] == 200
    assert envelope["data"]["status"] == "paused"


def test_dispatch_refused_when_agent_not_active(rig):
    admin = rig.principal("acme", "u_admin")
    _register(rig, admin, "worker-1")  # still 'registered'
    envelope = rig.app.handle(
        "POST", "/v1/agents/worker-1/tasks", principal=admin,
        body={"taskType": "classify-route", "input": {}},
    )
    assert envelope["status"] == 422
    assert envelope["error"]["code"] == "refused"


def test_invalid_lifecycle_transition_is_refused(rig):
    admin = rig.principal("acme", "u_admin")
    _register(rig, admin, "worker-1")
    # pause from 'registered' is not legal in the closed transition table.
    envelope = rig.app.handle("POST", "/v1/agents/worker-1/pause", principal=admin, body={})
    assert envelope["status"] == 422
    assert envelope["error"]["code"] == "refused"


def test_retire_is_approval_gated(rig):
    admin = rig.principal("acme", "u_admin")
    _register(rig, admin, "worker-1")

    # First attempt: destructive action awaits approval -> 202 approval_required.
    envelope = rig.app.handle("POST", "/v1/agents/worker-1/retire", principal=admin, body={"reason": "decommission"})
    assert envelope["status"] == 202
    assert envelope["data"]["status"] == "approval_required"
    approval_id = envelope["data"]["approval"]["approvalId"]
    # Agent not retired yet; approval.required published + audit stamped.
    assert rig.agent_ops.get("acme", "worker-1").status == "registered"
    assert any(e.type == "approval.required" for e in rig.outbox.records)
    assert any(a.action == "approval.request" for a in rig.audit.records)

    # Approver (distinct principal holding approval:approve) approves.
    rig.authorizer.add_principal("u_approver", "acme", "approval:approve", "approval:read", "agent:delete")
    approver = rig.principal("acme", "u_approver")
    envelope = rig.app.handle("POST", f"/v1/approvals/{approval_id}/approve", principal=approver, body={})
    assert envelope["status"] == 200
    assert envelope["data"]["status"] == "approved"

    # Retry by the requester now executes + consumes the approval.
    envelope = rig.app.handle("POST", "/v1/agents/worker-1/retire", principal=admin, body={"reason": "decommission"})
    assert envelope["status"] == 200
    assert envelope["data"]["status"] == "retired"
    assert rig.approvals.store.get(approval_id).status == "consumed"
    assert any(e.type == "agent.retired" for e in rig.outbox.records)


def test_retired_agent_cannot_be_reactivated(rig):
    admin = rig.principal("acme", "u_admin")
    _register(rig, admin, "worker-1")
    # retire through the approval flow.
    envelope = rig.app.handle("POST", "/v1/agents/worker-1/retire", principal=admin, body={"reason": "x"})
    assert envelope["status"] == 202
    approval_id = envelope["data"]["approval"]["approvalId"]
    rig.authorizer.add_principal("u_approver", "acme", "approval:approve")
    approver = rig.principal("acme", "u_approver")
    rig.app.handle("POST", f"/v1/approvals/{approval_id}/approve", principal=approver, body={})
    rig.app.handle("POST", "/v1/agents/worker-1/retire", principal=admin, body={"reason": "x"})

    envelope = rig.app.handle("POST", "/v1/agents/worker-1/activate", principal=admin, body={})
    assert envelope["status"] == 422
    assert envelope["error"]["code"] == "refused"


def test_dispatch_idempotency_key_returns_same_task(rig):
    admin = rig.principal("acme", "u_admin")
    _register(rig, admin, "worker-1")
    rig.app.handle("POST", "/v1/agents/worker-1/activate", principal=admin, body={})
    body = {"taskType": "classify-route", "input": {}, "idempotencyKey": "dispatch-abc"}
    first = rig.app.handle("POST", "/v1/agents/worker-1/tasks", principal=admin, body=body)
    second = rig.app.handle("POST", "/v1/agents/worker-1/tasks", principal=admin, body=body)
    assert first["data"]["taskId"] == second["data"]["taskId"]
