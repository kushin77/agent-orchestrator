"""Identity issuance with tenant-scoped claims (issue #10).

Scoped claims: tenantId, agentId, role and a closed allowedTools set (the
issuing agent's profile tool allowlist). The no-cross-tenant doctrine is
enforced twice:

- at issuance: an agent is looked up only in the requested tenant, so an agent
  of tenant A can never obtain a session claiming tenant B;
- at use: a session minted for tenant A is refused for tenant B (require_scope /
  authorize_tool_use) - there is no cross-tenant fallback.
"""

from __future__ import annotations

import pytest

from service import (
    AgentNotActiveError,
    AgentRegistry,
    CrossTenantDenied,
    InvalidCredentialError,
    SessionExpiredError,
    ToolNotAllowedError,
    UnknownAgentError,
)

# A fixed, clearly-labelled unit-test key. Not a secret: it exists only so
# token signing/verification is deterministic across test runs.
TEST_SIGNING_KEY = b"unit-test-signing-key-not-a-secret-0123456789"


@pytest.fixture()
def reg():
    return AgentRegistry(signing_key=TEST_SIGNING_KEY)


def _register_active(reg, tenant="acme", agent="worker-1", profile="coder", role=None):
    reg.register(tenant, agent, profile, role=role)
    return reg.activate(tenant, agent)


# --------------------------------------------------------------------- #
# issuance + claims
# --------------------------------------------------------------------- #
def test_session_claims_are_tenant_scoped(reg):
    _register_active(reg, role="worker")
    session = reg.issue_session("acme", "worker-1")
    claims = session.to_claims()
    assert claims["tenantId"] == "acme"
    assert claims["agentId"] == "worker-1"
    assert claims["sub"] == "worker-1"
    assert claims["role"] == "worker"
    assert claims["iss"] == "urn:agent-orchestrator:registry"
    assert claims["iat"] <= claims["exp"]
    # allowed tools are the coder profile's closed tool allowlist
    assert "file_write" in claims["allowedTools"]
    assert "shell_exec" in claims["allowedTools"]
    assert "sql_query" not in claims["allowedTools"]


def test_session_token_roundtrip(reg):
    _register_active(reg)
    token = reg.issue_session("acme", "worker-1").encode(TEST_SIGNING_KEY)
    session = reg.verify_session(token, signing_key=TEST_SIGNING_KEY)
    assert session.tenant_id == "acme"
    assert session.agent_id == "worker-1"


def test_role_defaults_to_agent(reg):
    _register_active(reg)
    session = reg.issue_session("acme", "worker-1")
    assert session.role == "agent"


# --------------------------------------------------------------------- #
# no cross-tenant fallback (the acceptance negatives)
# --------------------------------------------------------------------- #
def test_agent_of_tenant_a_cannot_obtain_session_claiming_tenant_b(reg):
    _register_active(reg, tenant="acme", agent="worker-1")
    # worker-1 belongs to acme only; asking tenant B for a session must fail
    with pytest.raises(UnknownAgentError):
        reg.issue_session("globex", "worker-1")
    # and the tenant-B view never sees acme's agent at all
    with pytest.raises(UnknownAgentError):
        reg.get("globex", "worker-1")


def test_session_for_tenant_a_cannot_be_used_in_tenant_b(reg):
    _register_active(reg, tenant="acme", agent="worker-1")
    session = reg.issue_session("acme", "worker-1")
    # use in tenant B is refused even though the role/tools would be identical
    with pytest.raises(CrossTenantDenied):
        reg.require_scope(session, "globex")
    # require_scope succeeds in the session's own tenant
    assert reg.require_scope(session, "acme").tenant_id == "acme"


def test_same_agent_id_in_two_tenants_have_independent_sessions(reg):
    _register_active(reg, tenant="acme", agent="worker-1", role="worker")
    _register_active(reg, tenant="globex", agent="worker-1", role="reviewer")
    acme_session = reg.issue_session("acme", "worker-1")
    globex_session = reg.issue_session("globex", "worker-1")
    assert acme_session.tenant_id == "acme"
    assert globex_session.tenant_id == "globex"
    assert acme_session.role == "worker"
    assert globex_session.role == "reviewer"
    with pytest.raises(CrossTenantDenied):
        reg.require_scope(acme_session, "globex")
    with pytest.raises(CrossTenantDenied):
        reg.require_scope(globex_session, "acme")


def test_forged_claims_do_not_verify(reg):
    _register_active(reg, tenant="acme", agent="worker-1")
    token = reg.issue_session("acme", "worker-1").encode(TEST_SIGNING_KEY)
    # flip the tenant claim inside the payload: signature must no longer verify
    import base64
    import json as _json

    header, payload, signature = token.split(".")
    padded = payload + "=" * (-len(payload) % 4)
    claims = _json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    claims["tenantId"] = "globex"
    raw = _json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    forged_payload = (
        base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    )
    forged = f"{header}.{forged_payload}.{signature}"
    with pytest.raises(InvalidCredentialError):
        reg.verify_session(forged, signing_key=TEST_SIGNING_KEY)


def test_verify_with_wrong_key_is_refused(reg):
    _register_active(reg)
    token = reg.issue_session("acme", "worker-1").encode(TEST_SIGNING_KEY)
    with pytest.raises(InvalidCredentialError):
        reg.verify_session(token, signing_key=b"a-different-key-0000000000")


def test_expired_session_is_refused(reg):
    _register_active(reg)
    # ttl of zero with a fixed now -> immediately expired
    token = reg.issue_session(
        "acme", "worker-1", ttl_seconds=0, now=1_000_000
    ).encode(TEST_SIGNING_KEY)
    with pytest.raises(SessionExpiredError):
        reg.verify_session(token, signing_key=TEST_SIGNING_KEY, now=1_000_001)


# --------------------------------------------------------------------- #
# active-only issuance + tool scoping
# --------------------------------------------------------------------- #
def test_paused_and_retired_agents_get_no_new_sessions(reg):
    _register_active(reg)
    reg.pause("acme", "worker-1")
    with pytest.raises(AgentNotActiveError):
        reg.issue_session("acme", "worker-1")
    reg.activate("acme", "worker-1")
    reg.retire("acme", "worker-1")
    with pytest.raises(AgentNotActiveError):
        reg.issue_session("acme", "worker-1")


def test_session_allows_tool_membership(reg):
    _register_active(reg)
    session = reg.issue_session("acme", "worker-1")
    assert reg.identity().session_allows_tool(session, "file_write")
    assert not reg.identity().session_allows_tool(session, "sql_query")


def test_authorize_tool_use_scope_then_tool(reg):
    _register_active(reg)
    session = reg.issue_session("acme", "worker-1")
    assert reg.authorize_tool_use(session, "acme", "file_write") is True
    with pytest.raises(ToolNotAllowedError):
        reg.authorize_tool_use(session, "acme", "sql_query")
    with pytest.raises(CrossTenantDenied):
        reg.authorize_tool_use(session, "globex", "file_write")


def test_session_issuance_appends_session_event(reg):
    _register_active(reg)
    reg.issue_session("acme", "worker-1")
    events = reg.events.events()
    session_events = [e for e in events if e["event"] == "session"]
    assert len(session_events) == 1
    assert session_events[0]["tenantId"] == "acme"
    assert session_events[0]["agentId"] == "worker-1"
    reg.events.verify()
