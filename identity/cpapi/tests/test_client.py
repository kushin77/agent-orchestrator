"""Client + in-process transport round-trip tests (issue #38 AC4).

The typed client speaks the ``openapi.yaml`` wire contract over the in-process
transport, so these tests verify the full client -> envelope ->
authN/authZ -> handler -> client stack offline.
"""

import pytest

from cpapi import ApiError
from cpapi.clients import ControlPlaneClient
from cpapi.fakes import build_test_app
from cpapi.transport import InProcessTransport


@pytest.fixture()
def rig():
    return build_test_app(tenant_id="acme", admin_subject="u_admin")


@pytest.fixture()
def admin_client(rig):
    token = rig.verifier.issue("acme", "u_admin")
    return ControlPlaneClient(InProcessTransport(rig.app), token=token)


def test_client_register_list_dispatch_roundtrip(rig, admin_client):
    agent = admin_client.register_agent("worker-1", "coder")
    assert agent["status"] == "registered"
    assert agent["tenantId"] == "acme"

    assert [a["agentId"] for a in admin_client.list_agents()] == ["worker-1"]

    admin_client.activate_agent("worker-1", reason="go live")
    task = admin_client.dispatch_task("worker-1", "classify-route", input_={"q": "x"})
    assert task["taskId"].startswith("task_")
    status = admin_client.get_task_status("worker-1", task["taskId"])
    assert status["status"] == "PENDING"

    profiles = admin_client.list_profiles()
    assert "coder" in [p["id"] for p in profiles]
    usage = admin_client.get_usage("acme")
    assert usage["tenantId"] == "acme"


def test_client_unwraps_api_error(rig, admin_client):
    with pytest.raises(ApiError) as exc:
        admin_client.register_agent("worker-1", "coder")
        admin_client.register_agent("worker-1", "coder")  # duplicate
    assert exc.value.status == 409
    assert exc.value.code == "already_registered"


def test_client_outbox_consumer_contract(rig, admin_client):
    admin_client.register_agent("worker-1", "coder")
    events = admin_client.poll_outbox("user:u_admin", limit=10)
    assert len(events) == 2  # agent.registered + agent.provision
    for event in events:
        acked = admin_client.ack_outbox(event["eventId"], consumer="user:u_admin")
        assert acked["state"] == "delivered"


def test_client_approval_gated_retire_via_two_principals(rig, admin_client):
    admin_client.register_agent("worker-1", "coder")
    pending = admin_client.retire_agent("worker-1", reason="decommission")
    assert pending["status"] == "approval_required"
    approval_id = pending["approval"]["approvalId"]

    # The approver is a distinct principal; approve through its own client.
    rig.authorizer.add_principal("u_approver", "acme", "approval:approve")
    approver_token = rig.verifier.issue("acme", "u_approver")
    approver_client = ControlPlaneClient(InProcessTransport(rig.app), token=approver_token)
    decided = approver_client.approve(approval_id)
    assert decided["status"] == "approved"

    retired = admin_client.retire_agent("worker-1", reason="decommission")
    assert retired["status"] == "retired"
    assert rig.approvals.store.get(approval_id).status == "consumed"


def test_client_error_payload_matches_spec_codes(rig):
    token = rig.verifier.issue("acme", "u_admin")
    client = ControlPlaneClient(InProcessTransport(rig.app), token=token)
    with pytest.raises(ApiError) as exc:
        client.get_agent("missing-agent")
    assert exc.value.status == 404
    assert exc.value.code == "unknown_agent"
