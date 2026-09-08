"""Registry-read + tenant/budget endpoint tests (profiles, personas, prompts,
policies, tenant, usage, quotas, pause rail)."""

import pytest

from cpapi.fakes import build_test_app


@pytest.fixture()
def rig():
    return build_test_app(tenant_id="acme", admin_subject="u_admin")


def test_list_and_get_profiles(rig):
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle("GET", "/v1/profiles", principal=admin)
    assert envelope["status"] == 200
    ids = [p["id"] for p in envelope["data"]["items"]]
    assert "coder" in ids and "reviewer" in ids and "orchestrator" in ids

    envelope = rig.app.handle("GET", "/v1/profiles/coder", principal=admin)
    assert envelope["status"] == 200
    assert envelope["data"]["systemPromptRef"] == "coder/primary@v1"

    envelope = rig.app.handle("GET", "/v1/profiles/nope", principal=admin)
    assert envelope["status"] == 404
    assert envelope["error"]["code"] == "unknown_profile"


def test_list_and_get_personas(rig):
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle("GET", "/v1/personas", principal=admin)
    assert envelope["status"] == 200
    ids = [p["id"] for p in envelope["data"]["items"]]
    assert "coder" in ids and "security-sme" in ids

    envelope = rig.app.handle("GET", "/v1/personas/security-sme", principal=admin)
    assert envelope["status"] == 200
    assert envelope["data"]["posture"] == "reviewer"


def test_list_and_get_prompt_modules(rig):
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle("GET", "/v1/prompts", principal=admin)
    assert envelope["status"] == 200
    types = [p["taskType"] for p in envelope["data"]["items"]]
    assert "classify-route" in types

    envelope = rig.app.handle("GET", "/v1/prompts/classify-route", principal=admin)
    assert envelope["status"] == 200
    assert envelope["data"]["status"] == "published"


def test_list_and_get_policies(rig):
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle("GET", "/v1/policies", principal=admin)
    assert envelope["status"] == 200
    ids = [p["id"] for p in envelope["data"]["items"]]
    assert "worker-bundle" in ids

    envelope = rig.app.handle("GET", "/v1/policies/worker-bundle", principal=admin)
    assert envelope["status"] == 200
    assert envelope["data"]["mode"] == "enforce"


def test_get_tenant(rig):
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle("GET", "/v1/tenants/acme", principal=admin)
    assert envelope["status"] == 200
    assert envelope["data"]["tenantId"] == "acme"
    assert envelope["data"]["plan"] == "enterprise"


def test_get_tenant_other_tenant_is_scope_denied(rig):
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle("GET", "/v1/tenants/globex", principal=admin)
    assert envelope["status"] == 403
    assert envelope["error"]["code"] == "scope_denied"


def test_usage_and_quotas(rig):
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle("GET", "/v1/tenants/acme/usage", principal=admin)
    assert envelope["status"] == 200
    assert envelope["data"]["tenantId"] == "acme"
    assert envelope["data"]["budgetPositions"]["position"] == "allow"

    envelope = rig.app.handle("GET", "/v1/tenants/acme/quotas", principal=admin)
    assert envelope["status"] == 200
    assert envelope["data"]["quotas"]["requestsPerDay"] == 100000


def test_tenant_pause_is_approval_gated_then_executes(rig):
    admin = rig.principal("acme", "u_admin")

    # pause (kill-switch rail) is destructive -> 202 approval_required first.
    envelope = rig.app.handle("POST", "/v1/tenants/acme/pause", principal=admin, body={"reason": "incident"})
    assert envelope["status"] == 202
    assert envelope["data"]["status"] == "approval_required"
    approval_id = envelope["data"]["approval"]["approvalId"]
    assert "acme" not in rig.budgets.paused

    rig.authorizer.add_principal("u_approver", "acme", "approval:approve", "budget:manage")
    approver = rig.principal("acme", "u_approver")
    envelope = rig.app.handle("POST", f"/v1/approvals/{approval_id}/approve", principal=approver, body={})
    assert envelope["status"] == 200

    envelope = rig.app.handle("POST", "/v1/tenants/acme/pause", principal=admin, body={"reason": "incident"})
    assert envelope["status"] == 200
    assert envelope["data"]["killSwitch"] is True
    assert "acme" in rig.budgets.paused
    assert any(e.type == "control.pause" for e in rig.outbox.records)


def test_tenant_resume_clears_kill_switch(rig):
    admin = rig.principal("acme", "u_admin")
    # pause via approval flow
    rig.app.handle("POST", "/v1/tenants/acme/pause", principal=admin, body={"reason": "incident"})
    pending = rig.approvals.store.list(status="pending")[0]
    rig.authorizer.add_principal("u_approver", "acme", "approval:approve")
    approver = rig.principal("acme", "u_approver")
    rig.app.handle("POST", f"/v1/approvals/{pending.approvalId}/approve", principal=approver, body={})
    rig.app.handle("POST", "/v1/tenants/acme/pause", principal=admin, body={"reason": "incident"})
    assert "acme" in rig.budgets.paused

    envelope = rig.app.handle("POST", "/v1/tenants/acme/resume", principal=admin, body={"reason": "all clear"})
    assert envelope["status"] == 200
    assert envelope["data"]["killSwitch"] is False
    assert "acme" not in rig.budgets.paused
    assert any(e.type == "control.resume" for e in rig.outbox.records)
