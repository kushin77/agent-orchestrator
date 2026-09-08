"""Registry lifecycle tests (issue #10): tenant-scoped register / activate /
pause / retire, event emission, and tenant isolation.

An agent is created from a frozen AgentProfile seed (issue #9) - the profile's
closed capabilitySet and toolAllowlist become the agent's row. Lifecycle
transitions are enforced by a state machine: registered -> active (activate),
active -> paused (pause), paused -> active (activate), any non-terminal ->
retired (terminal).
"""

from __future__ import annotations

import pytest

from service import (
    STATUS_ACTIVE,
    STATUS_PAUSED,
    STATUS_REGISTERED,
    STATUS_RETIRED,
    AgentAlreadyRegisteredError,
    AgentRegistry,
    InvalidAgentIdError,
    InvalidTenantIdError,
    InvalidTransitionError,
    UnknownAgentError,
    UnknownProfileError,
)
from events import EventLogIntegrityError, open_event_log


@pytest.fixture()
def registry():
    return AgentRegistry()


def _register(reg, tenant="acme", agent="worker-1", profile="coder", role=None):
    return reg.register(tenant, agent, profile, role=role)


# --------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------- #
def test_register_creates_tenant_scoped_agent_from_profile(registry):
    agent = _register(registry)
    assert agent.agent_id == "worker-1"
    assert agent.tenant_id == "acme"
    assert agent.status == STATUS_REGISTERED
    assert agent.profile_ref == "coder"
    assert agent.profile_version == "1.0.0"
    assert agent.owner == "platform/execution"
    assert agent.model_tier == "LOW"
    assert agent.last_seen is None
    # capabilities + tools come from the frozen coder profile (issue #9)
    assert "code-author" in agent.capabilities
    assert "test-run" in agent.capabilities
    assert "file_write" in agent.tools
    assert "sql_query" not in agent.tools


def test_register_record_shape_uses_contract_keys(registry):
    agent = _register(registry)
    record = agent.to_record()
    assert record["agentId"] == "worker-1"
    assert record["tenantId"] == "acme"
    assert record["profileRef"] == "coder"
    assert record["status"] == STATUS_REGISTERED
    assert record["owner"] == "platform/execution"
    assert record["lastSeen"] is None
    assert record["capabilities"] == list(agent.capabilities)


def test_duplicate_register_same_tenant_is_refused(registry):
    _register(registry)
    with pytest.raises(AgentAlreadyRegisteredError):
        _register(registry)


def test_same_agent_id_allowed_in_another_tenant(registry):
    first = _register(registry, tenant="acme", agent="worker-1")
    second = _register(registry, tenant="globex", agent="worker-1")
    assert first.tenant_id == "acme"
    assert second.tenant_id == "globex"
    assert len(registry.list("acme")) == 1
    assert len(registry.list("globex")) == 1


def test_invalid_agent_id_is_refused(registry):
    with pytest.raises(InvalidAgentIdError):
        _register(registry, agent="Worker_1")
    with pytest.raises(InvalidAgentIdError):
        _register(registry, agent="")


def test_invalid_tenant_id_is_refused(registry):
    with pytest.raises(InvalidTenantIdError):
        _register(registry, tenant="Bad Tenant")


def test_unknown_profile_is_refused(registry):
    with pytest.raises(UnknownProfileError):
        _register(registry, profile="no-such-profile")


def test_get_is_tenant_scoped(registry):
    _register(registry, tenant="acme", agent="worker-1")
    agent = registry.get("acme", "worker-1")
    assert agent.agent_id == "worker-1"
    # the same agent id registered in another tenant is unknown here
    with pytest.raises(UnknownAgentError):
        registry.get("globex", "worker-1")


# --------------------------------------------------------------------- #
# lifecycle state machine
# --------------------------------------------------------------------- #
def test_lifecycle_happy_path(registry):
    agent = _register(registry)
    assert agent.status == STATUS_REGISTERED
    agent = registry.activate("acme", "worker-1")
    assert agent.status == STATUS_ACTIVE
    agent = registry.pause("acme", "worker-1")
    assert agent.status == STATUS_PAUSED
    agent = registry.activate("acme", "worker-1")
    assert agent.status == STATUS_ACTIVE
    agent = registry.retire("acme", "worker-1")
    assert agent.status == STATUS_RETIRED


def test_invalid_transitions_are_refused(registry):
    _register(registry)
    with pytest.raises(InvalidTransitionError):
        registry.pause("acme", "worker-1")  # registered -> pause is illegal
    registry.activate("acme", "worker-1")
    with pytest.raises(InvalidTransitionError):
        registry.activate("acme", "worker-1")  # active -> activate is illegal
    registry.retire("acme", "worker-1")
    with pytest.raises(InvalidTransitionError):
        registry.activate("acme", "worker-1")  # retired is terminal


def test_retire_is_terminal_and_allowed_from_any_non_terminal(registry):
    for setup in ("registered", "active", "paused"):
        reg = AgentRegistry()
        agent = _register(reg)
        if setup == "active":
            agent = reg.activate("acme", "worker-1")
        elif setup == "paused":
            reg.activate("acme", "worker-1")
            agent = reg.pause("acme", "worker-1")
        retired = reg.retire("acme", "worker-1")
        assert retired.status == STATUS_RETIRED


def test_list_filters_by_status_and_records(registry):
    _register(registry, agent="a1")
    _register(registry, agent="a2")
    _register(registry, tenant="other", agent="x1")
    registry.activate("acme", "a2")
    active = registry.list("acme", status=STATUS_ACTIVE)
    assert [a.agent_id for a in active] == ["a2"]
    assert len(registry.list("acme")) == 2
    records = registry.records("acme")
    assert {r["agentId"] for r in records} == {"a1", "a2"}


def test_touch_updates_last_seen(registry):
    _register(registry)
    assert registry.get("acme", "worker-1").last_seen is None
    agent = registry.touch("acme", "worker-1", ts="2026-09-08T12:00:00Z")
    assert agent.last_seen == "2026-09-08T12:00:00Z"


# --------------------------------------------------------------------- #
# event emission (audit trail)
# --------------------------------------------------------------------- #
def test_lifecycle_emits_append_only_events(registry):
    _register(registry)
    registry.activate("acme", "worker-1")
    registry.pause("acme", "worker-1")
    registry.retire("acme", "worker-1")
    events = registry.events.events()
    assert [e["event"] for e in events] == [
        "register",
        "activate",
        "pause",
        "retire",
    ]
    assert [e["status"] for e in events] == [
        STATUS_REGISTERED,
        STATUS_ACTIVE,
        STATUS_PAUSED,
        STATUS_RETIRED,
    ]
    for e in events:
        assert e["tenantId"] == "acme"
        assert e["agentId"] == "worker-1"
    # chain is intact and tail state is reproducible
    state = registry.events.state()
    assert state[0] == 4
    assert registry.events.verify(expected=state) == state


def test_register_event_carries_profile_detail(registry):
    agent = _register(registry)
    register_event = registry.events.events()[0]
    assert register_event["detail"]["profileRef"] == agent.profile_ref
    assert register_event["detail"]["profileVersion"] == agent.profile_version


def test_file_backed_audit_log_persists_and_verifies(tmp_path):
    path = str(tmp_path / "audit.jsonl")
    log = open_event_log(path)
    reg = AgentRegistry(event_log=log)
    _register(reg)
    reg.activate("acme", "worker-1")
    reg.pause("acme", "worker-1")
    expected = reg.events.state()

    reopened = open_event_log(path)
    assert reopened.state() == expected
    assert len(reopened) == 3
    reopened.verify(expected=expected)


def test_tampered_audit_log_is_refused(registry, tmp_path):
    path = str(tmp_path / "audit.jsonl")
    log = open_event_log(path)
    reg = AgentRegistry(event_log=log)
    _register(reg)
    reg.activate("acme", "worker-1")
    # hand-edit the persisted register event's status
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    data_indices = [
        i for i, line in enumerate(lines) if line.strip() and not line.strip().startswith("#")
    ]
    import json

    record = json.loads(lines[data_indices[0]])
    record["status"] = "forged"
    lines[data_indices[0]] = json.dumps(record, separators=(",", ":"))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    with pytest.raises(EventLogIntegrityError):
        open_event_log(path)
