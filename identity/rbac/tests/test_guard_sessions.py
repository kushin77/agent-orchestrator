"""Guard enforcement: agent sessions carry roles; every call is guarded.

Issue #12 criteria 5: an agent session is minted only by an authorized
principal and carries its roles; the guard (the enforcement core of the later
HTTP/guardrail middleware) checks every tool/API call against the live store.
"""

import pytest

from rbac import (
    AUTHORIZATION_DENIED_EVENT,
    SCOPE_DENIAL_PERMISSION,
    Decision,
    InMemoryStore,
    PermissionDeniedError,
    ScopeDeniedError,
    ScopeNode,
    UnknownAgentError,
    authorization_denied_payload,
    grant_role,
    guard,
    guard_required,
    guard_session,
    revoke_role,
    seed_org,
    start_agent_session,
)


def _build():
    store = InMemoryStore()
    org = store.add_org("acme", "Acme", tenant_type="platform")
    seed_org(store, org)
    store.add_team("acme", "Engineering", team_id="eng")
    store.add_agent("acme", "eng", "writer-agent", agent_id="agent_eng")
    return store


def _start_operator_session(store, subject="u_op"):
    grant_role(store, "acme", subject, "agent-operator", team_id="eng")
    return start_agent_session(store, subject, "agent_eng")


# --- session minting ---------------------------------------------------------


def test_start_session_requires_agent_run():
    store = _build()
    # member is in scope but lacks agent:run -> permission gate refuses.
    grant_role(store, "acme", "u_mem", "member")
    with pytest.raises(PermissionDeniedError):
        start_agent_session(store, "u_mem", "agent_eng")

    # A stranger is not even in scope -> scope gate refuses.
    with pytest.raises(ScopeDeniedError):
        start_agent_session(store, "stranger", "agent_eng")


def test_start_session_for_unknown_agent_raises():
    store = _build()
    with pytest.raises(UnknownAgentError):
        start_agent_session(store, "u_any", "no-such-agent")


# --- sessions carry roles; the guard checks every call ------------------------


def test_session_carries_the_resolved_role_keys():
    store = _build()
    session = _start_operator_session(store)
    assert session.subject_id == "u_op"
    assert session.agent_id == "agent_eng"
    assert session.org_id == "acme"
    assert session.roles == ("agent-operator",)


def test_guard_session_allows_granted_and_denies_ungranted():
    store = _build()
    session = _start_operator_session(store)

    # agent-operator grants tool:call / session:read within its team scope.
    assert guard_session(store, session, "tool:call").allowed
    assert guard_session(store, session, "session:read").allowed

    # ... but not model management: the permission gate denies.
    decision = guard_session(store, session, "model:manage")
    assert not decision.allowed
    assert decision.reason == "permission"


def test_guard_required_modes_all_and_any():
    store = _build()
    session = _start_operator_session(store)
    node = ScopeNode("acme", "eng", "agent_eng")

    # any-of: tool:call holds, so the combined check passes.
    decision = guard_required(
        store, "u_op", node, ["tool:call", "model:manage"], mode="any"
    )
    assert decision.allowed

    # all-of: model:manage is missing, so it fails and names the missing set.
    decision = guard_required(
        store, "u_op", node, ["tool:call", "model:manage"], mode="all"
    )
    assert not decision.allowed
    assert decision.reason == "permission"
    assert "model:manage" in decision.missing_permissions
    assert decision.permission == "tool:call"  # primary (first) permission


def test_guard_required_rejects_empty_requirement():
    store = _build()
    session = _start_operator_session(store)
    node = ScopeNode("acme", "eng", "agent_eng")
    with pytest.raises(ValueError):
        guard_required(store, "u_op", node, [], mode="all")


def test_single_guard_is_a_decision():
    store = _build()
    grant_role(store, "acme", "u_admin", "admin")
    decision = guard(store, "u_admin", ScopeNode("acme", "eng", "agent_eng"), "agent:run")
    assert isinstance(decision, Decision)
    assert decision.allowed
    assert decision.reason is None


# --- revocation takes effect immediately (no stale session grants) ------------


def test_revocation_takes_effect_mid_session():
    store = _build()
    session = _start_operator_session(store)
    assert guard_session(store, session, "tool:call").allowed

    # Revoke the operator's only binding mid-session.
    assert revoke_role(store, "acme", "u_op", "agent-operator", team_id="eng") is True

    # The next guarded call is denied at the scope gate: live bindings are the
    # source of truth, never the roles the session carried at start.
    decision = guard_session(store, session, "tool:call")
    assert not decision.allowed
    assert decision.reason == "scope"


def test_session_carried_roles_are_not_themselves_a_grant():
    store = _build()
    session = _start_operator_session(store)
    assert "agent-operator" in session.roles

    # A forged call under a role the session does not carry is still denied,
    # because enforcement re-resolves live bindings for the session subject.
    decision = guard_session(store, session, "agent:delete")
    assert not decision.allowed
    assert decision.reason == "permission"


# --- denial-log / observability contract --------------------------------------


def test_scope_denial_payload_names_the_reserved_fields():
    store = _build()
    decision = guard(store, "stranger", ScopeNode("acme", "eng", "agent_eng"), "tool:call")
    assert not decision.allowed
    assert decision.reason == "scope"

    payload = authorization_denied_payload(decision)
    assert payload["event"] == AUTHORIZATION_DENIED_EVENT
    assert payload["orgId"] == "acme"
    assert payload["tenantId"] == "acme"
    # A scope denial reports the pseudo-permission so the cause is attributable.
    assert payload["permission"] == SCOPE_DENIAL_PERMISSION
    assert payload["reason"] == "scope"
    assert payload["subject"] == "stranger"


def test_permission_denial_payload_names_the_requested_permission():
    store = _build()
    session = _start_operator_session(store)
    decision = guard_session(store, session, "model:manage")
    assert decision.reason == "permission"
    payload = authorization_denied_payload(decision)
    assert payload["event"] == AUTHORIZATION_DENIED_EVENT
    assert payload["permission"] == "model:manage"
    assert payload["reason"] == "permission"
    assert "model:manage" in payload["missingPermissions"]


def test_denial_payload_rejects_allowed_decisions():
    store = _build()
    session = _start_operator_session(store)
    decision = guard_session(store, session, "tool:call")
    assert decision.allowed
    with pytest.raises(ValueError):
        authorization_denied_payload(decision)
