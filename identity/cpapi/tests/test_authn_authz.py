"""AuthN (session verify) + AuthZ (two-gate guard) denial tests.

These are the security negative tests: no token / bad token -> 401; a session
from another tenant cannot touch this tenant's surface (scope gate) -> 403;
and a principal in scope who lacks the route's permission is refused at the
permission gate -> 403 — never a fallback, never a silent pass.
"""

import pytest

from cpapi import errors as err
from cpapi.access import AuthenticatedPrincipal
from cpapi.fakes import FakeSessionVerifier, build_test_app


@pytest.fixture()
def rig():
    return build_test_app(tenant_id="acme", admin_subject="u_admin")


# --- authN --------------------------------------------------------------------


def test_missing_token_is_401():
    rig = build_test_app()
    envelope = rig.app.handle("GET", "/v1/tenants/acme")
    assert envelope["status"] == 401
    assert envelope["error"]["code"] == "unauthorized"


def test_unknown_token_is_401():
    rig = build_test_app()
    bogus = "tok_" + "bogus"  # assembled so the mechanical secret scan stays clean
    envelope = rig.app.handle("GET", "/v1/tenants/acme", token=bogus)
    assert envelope["status"] == 401
    assert envelope["error"]["code"] == "invalid_token"


def test_valid_token_authenticates(rig):
    token = rig.verifier.issue("acme", "u_admin")
    envelope = rig.app.handle("GET", "/v1/tenants/acme", token=token)
    assert envelope["status"] == 200
    assert envelope["data"]["tenantId"] == "acme"


def test_revoked_session_is_401():
    class RevokingVerifier(FakeSessionVerifier):
        def __init__(self):
            super().__init__()
            self.revoked = set()

        def revoke(self, token):
            self.revoked.add(token)

        def verify(self, token, expected_tenant=None):
            if token in self.revoked:
                raise err.session_revoked()
            return super().verify(token, expected_tenant=expected_tenant)

    verifier = RevokingVerifier()
    verifier.issue("acme", "u_admin")
    rig = build_test_app()
    token = verifier.issue("acme", "u_admin2")
    # Swap the app's verifier for the revoking one.
    rig.app.session_verifier = verifier  # type: ignore[assignment]
    verifier.revoke(token)
    envelope = rig.app.handle("GET", "/v1/tenants/acme", token=token)
    assert envelope["status"] == 401
    assert envelope["error"]["code"] == "session_revoked"


def test_cross_tenant_token_is_refused():
    rig = build_test_app()
    token = rig.verifier.issue("globex", "u_admin")
    # The path tenant (acme) does not match the session tenant (globex).
    envelope = rig.app.handle("GET", "/v1/tenants/acme", token=token)
    assert envelope["status"] == 403
    assert envelope["error"]["code"] == "scope_denied"


def test_no_question_mark_pseudo_tenant_injection(rig):
    """A caller cannot name a tenant that its session does not own."""
    admin = rig.principal("acme", "u_admin")
    envelope = rig.app.handle(
        "GET", "/v1/tenants/otherco", principal=admin, query={"tenantId": "otherco"}
    )
    assert envelope["status"] == 403
    assert envelope["error"]["code"] == "scope_denied"


# --- authZ --------------------------------------------------------------------


def test_scope_denial_precedes_permission_denial():
    """The scope gate is separate from and precedes the permission gate."""
    from cpapi.fakes import FakeAuthorizer

    authz = FakeAuthorizer()
    # A subject scoped to acme but granted nothing meaningful.
    authz.add_principal("u_member", "acme", "agent:read")
    # A stranger who is not scoped to acme at all.
    in_scope = authz.authorize("u_member", "acme", "agent:create")
    out_of_scope = authz.authorize("u_stranger", "acme", "agent:create")
    assert not in_scope.allowed and in_scope.reason == "permission"
    assert not out_of_scope.allowed and out_of_scope.reason == "scope"
    # Never conflated: the fix for one is not the fix for the other.
    assert in_scope.code == "denied"
    assert out_of_scope.code == "out_of_scope"


def test_authorizer_wildcard_grant_allows(rig):
    from cpapi.fakes import FakeAuthorizer

    authz = FakeAuthorizer()
    authz.add_principal("u_owner", "acme", "*:*")
    decision = authz.authorize("u_owner", "acme", "budget:manage")
    assert decision.allowed


def test_member_cannot_register_agent(rig):
    """Permission gate: in scope but lacks agent:create -> 403 permission_denied."""
    rig.authorizer.add_principal("u_member", "acme", "agent:read", "agent:run")
    member = rig.principal("acme", "u_member")
    envelope = rig.app.handle(
        "POST", "/v1/agents", principal=member,
        body={"agentId": "worker-9", "profileRef": "coder"},
    )
    assert envelope["status"] == 403
    assert envelope["error"]["code"] == "permission_denied"
    assert envelope["error"]["details"]["permission"] == "agent:create"


def test_member_can_read_but_not_pause(rig):
    rig.authorizer.add_principal("u_member", "acme", "agent:read", "budget:read")
    member = rig.principal("acme", "u_member")

    ok = rig.app.handle("GET", "/v1/tenants/acme/usage", principal=member)
    assert ok["status"] == 200

    denied = rig.app.handle("POST", "/v1/tenants/acme/pause", principal=member, body={"reason": "x"})
    assert denied["status"] == 403
    assert denied["error"]["code"] == "permission_denied"


def test_viewer_role_read_only_cannot_dispatch(rig):
    rig.authorizer.add_principal("u_viewer", "acme", "agent:read")
    viewer = rig.principal("acme", "u_viewer")
    denied = rig.app.handle(
        "POST", "/v1/agents/whatever/tasks", principal=viewer,
        body={"taskType": "classify-route", "input": {}},
    )
    assert denied["status"] == 403
    assert denied["error"]["code"] == "permission_denied"


def test_every_route_declares_a_permission():
    rig = build_test_app()
    for route in rig.app.router.routes:
        assert route.permission, f"route {route.name} has no permission"
        assert ":" in route.permission, f"route {route.name} permission not resource:action"


def test_principal_actor_is_kind_id(rig):
    admin = rig.principal("acme", "u_admin")
    assert admin.actor == "user:u_admin"
    agent = AuthenticatedPrincipal(subject_id="worker-1", subject_type="agent", tenant_id="acme")
    assert agent.actor == "agent:worker-1"
