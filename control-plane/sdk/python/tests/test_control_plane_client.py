"""Control-plane client tests — usage, audit export, policy (issue #41 AC1)."""

from __future__ import annotations

import pytest

from aosdk.auth import TokenSource
from aosdk.controlplane import ControlPlaneClient
from aosdk.errors import (
    ApiError,
    ConfigurationError,
    PermissionDeniedError,
    UnauthorizedError,
)
from aosdk.model import AuditRecord, PolicyBinding, UsageReport

from _fakes import FakeControlPlane, mint_session_token


def _client(token: str, transport: FakeControlPlane, **kwargs):
    return ControlPlaneClient(
        transport, token_source=TokenSource(callback=lambda: token), **kwargs
    )


def _token(tenant="acme", role="admin", **kwargs):
    return mint_session_token(tenant, subject="alice", role=role, **kwargs)


def test_usage_report_typed():
    transport = FakeControlPlane()
    client = _client(_token(), transport)
    usage = client.get_usage()
    assert isinstance(usage, UsageReport)
    assert usage.tenant_id == "acme"
    assert usage.calls == 42
    assert usage.total_tokens == 4800 + 2100
    assert usage.budget is not None
    assert usage.budget.action == "observe"


def test_my_usage_public_me_route():
    transport = FakeControlPlane()
    client = _client(_token(), transport)
    usage = client.my_usage()
    assert isinstance(usage, UsageReport)
    assert usage.tenant_id == "acme"
    assert usage.period == "2026-09"


def test_audit_export_typed():
    transport = FakeControlPlane()
    client = _client(_token(), transport)
    records = client.export_audit()
    assert isinstance(records, list)
    assert all(isinstance(r, AuditRecord) for r in records)
    assert all(r.tenant_id == "acme" for r in records)
    assert len(records) == 2


def test_audit_query_with_action_filter():
    transport = FakeControlPlane()
    client = _client(_token(), transport)
    records = client.query_audit(action="agent.register")
    assert len(records) == 1
    assert records[0].action == "agent.register"


def test_policies_typed():
    transport = FakeControlPlane()
    client = _client(_token(role="admin"), transport)
    policies = client.list_policies()
    assert isinstance(policies, list)
    assert all(isinstance(p, PolicyBinding) for p in policies)
    assert {p.policy_id for p in policies} == {"worker-bundle", "reviewer-bundle"}
    one = client.get_policy("worker-bundle")
    assert one.policy_id == "worker-bundle"
    assert one.enabled is True
    assert "no-secrets" in one.controls


def test_policy_binding_parses_the_real_guardrail_store_view():
    """The REAL ``GET /v1/policies`` payload parses (issue #1235).

    The literal row below is what ``identity/cpapi`` actually serves. It is
    written out here on purpose: ``_fakes.py`` serves a *different*, richer
    shape (``policyId`` + a list of control ids), which is exactly why this
    disagreement was invisible until the SDK had a real consumer.
    """
    row = {
        "controls": 3,
        "description": "reviewer posture guardrails",
        "id": "reviewer-bundle",
        "mode": "enforce",
    }
    parsed = PolicyBinding.from_dict(row)
    assert parsed.policy_id == "reviewer-bundle", "`id` must map, never silently ''"
    assert parsed.name == "reviewer posture guardrails"
    assert parsed.description == "reviewer posture guardrails"
    assert parsed.mode == "enforce"
    assert parsed.controls == []
    assert parsed.controls_count == 3, "the served value is a COUNT, not ids"
    assert PolicyBinding.from_dict(parsed.to_dict()) == parsed


def test_policy_binding_still_accepts_the_sdk_vocabulary():
    """The widening is additive: the original shape keeps working unchanged."""
    parsed = PolicyBinding.from_dict(
        {"policyId": "worker-bundle", "controls": ["no-secrets", "verify-before-done"]}
    )
    assert parsed.policy_id == "worker-bundle"
    assert parsed.controls == ["no-secrets", "verify-before-done"]
    assert parsed.controls_count is None


def test_policy_binding_fails_closed_on_a_malformed_controls_value():
    """An unknown ``controls`` type still fails closed rather than guessing."""
    with pytest.raises(TypeError):
        PolicyBinding.from_dict({"policyId": "worker-bundle", "controls": True})
    with pytest.raises(TypeError):
        PolicyBinding.from_dict({"policyId": "worker-bundle", "controls": "no-secrets"})


def test_policy_permission_denied_for_non_reader_role():
    transport = FakeControlPlane()
    client = _client(_token(role="analyst"), transport)
    with pytest.raises(PermissionDeniedError):
        client.list_policies()


def test_unknown_policy_is_not_found():
    transport = FakeControlPlane()
    client = _client(_token(role="admin"), transport)
    with pytest.raises(ApiError) as excinfo:
        client.get_policy("no-such-policy")
    assert excinfo.value.status == 404
    assert excinfo.value.code == "not_found"


def test_cross_tenant_read_refused_client_side():
    transport = FakeControlPlane()
    client = _client(_token(tenant="acme"), transport)
    with pytest.raises(ConfigurationError):
        client.get_usage(tenant_id="globex")


def test_cross_tenant_scope_denied_at_platform():
    # A token bound to globex can never read acme usage through the backend.
    transport = FakeControlPlane()
    client = _client(_token(tenant="globex"), transport)
    with pytest.raises(ApiError) as excinfo:
        client._call("GET", "/v1/tenants/acme/usage")
    assert excinfo.value.status == 403
    assert excinfo.value.code == "scope_denied"


def test_expired_token_rejected():
    transport = FakeControlPlane()
    client = _client(_token(ttl=-60), transport)
    with pytest.raises(UnauthorizedError):
        client.my_usage()
