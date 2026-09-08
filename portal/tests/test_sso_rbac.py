"""SSO + RBAC tests (issue #39 AC #3).

SSO is the real merged identity/sso console flow (issue #35): relay-state,
RS256 os-session-token, ROOT_ADMIN allowlist. RBAC is super-admin
(root_admin) vs tenant-admin, with the rbac (issue #12) platform role pack
permission vocabulary enforced at the console boundary.
"""

from __future__ import annotations

import pytest
from conftest import ApiClient, login_as


def _data(payload):
    return payload["data"]


# -- authN: no/invalid/revoked session --------------------------------------

def test_unauthenticated_request_is_401(client):
    status, payload = client.get("/api/tenants")
    assert status == 401
    assert payload["ok"] is False
    assert payload["error"]["code"] == "unauthorized"


def test_tampered_token_is_rejected(app):
    api = login_as(app, "root@platform.example.com", "acme")
    api.cookies["os-session-token"] = "not.a.jwt"
    status, payload = api.get("/api/tenants")
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"


def test_revoked_session_is_refused_even_when_unexpired(app):
    api = login_as(app, "root@platform.example.com", "acme")
    token = api.cookies["os-session-token"]
    status, payload = api.post("/api/console/logout")
    assert status == 200
    # Cookie was cleared.
    assert "os-session-token" not in api.cookies
    # Re-presenting the (still unexpired) token is refused: jti revoked.
    api.cookies["os-session-token"] = token
    status, payload = api.get("/api/tenants")
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"


def test_login_denied_for_unbound_tenant(app):
    api = ApiClient(app)
    status, payload = api.post(
        "/api/console/login",
        {"email": "carol@globex.example.com", "tenantId": "acme", "state": "x"},
    )
    # carol is bound to globex, not acme -> scope denied before the relay
    # state is even consumed (fail closed on the tenant gate).
    assert status == 403
    assert payload["error"]["code"] == "scope_denied"


# -- RBAC: super-admin vs tenant-admin --------------------------------------

def test_super_admin_sees_all_tenants(super_client):
    status, payload = super_client.get("/api/console/me")
    assert status == 200
    me = _data(payload)
    assert me["superAdmin"] is True
    assert set(me["scopedTenants"]) == {"acme", "globex", "initech"}
    status, payload = super_client.get("/api/tenants")
    assert status == 200
    assert len(_data(payload)["tenants"]) == 3


def test_tenant_owner_is_scoped_to_own_tenant(app):
    api = login_as(app, "alice@acme.example.com", "acme")
    status, payload = api.get("/api/console/me")
    me = _data(payload)
    assert me["superAdmin"] is False
    assert me["scopedTenants"] == ["acme"]
    # Own tenant is readable.
    status, payload = api.get("/api/tenants")
    assert status == 200
    rows = _data(payload)["tenants"]
    assert [row["tenantId"] for row in rows] == ["acme"]
    # Other tenant is out of scope (no cross-tenant fallback).
    status, payload = api.get("/api/tenants/globex/agents")
    assert status == 403
    assert payload["error"]["code"] == "scope_denied"


def test_tenant_admin_cannot_toggle_policy_controls(app):
    # bob@acme is role `admin`: policy:read but not policy:manage.
    api = login_as(app, "bob@acme.example.com", "acme")
    status, payload = api.get("/api/tenants/acme/controls")
    assert status == 200
    status, payload = api.post(
        "/api/tenants/acme/controls/model-call-budget", {"enabled": True}
    )
    assert status == 403
    assert payload["error"]["code"] == "permission_denied"


def test_agent_operator_cannot_mutate_agents(app):
    # erin@acme role `agent-operator`: agent:run but not agent:write.
    api = login_as(app, "erin@acme.example.com", "acme")
    status, payload = api.post("/api/tenants/acme/agents/coder-1/pause")
    assert status == 403
    assert payload["error"]["code"] == "permission_denied"
    # Read is fine.
    status, payload = api.get("/api/tenants/acme/agents")
    assert status == 200


def test_tenant_owner_can_toggle_policy_controls(app):
    api = login_as(app, "alice@acme.example.com", "acme")
    status, payload = api.post(
        "/api/tenants/acme/controls/tool-use-guard", {"enabled": True}
    )
    assert status == 200
    assert payload["data"]["control"]["enabled"] is True
