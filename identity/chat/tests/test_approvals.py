"""Approvals: proposals reach the control plane; the chat surface never writes.

The two halves under test are "the proposal really lands in ``identity.cpapi``'s
store" and "nothing runs without an approved request". The second half is
paired with the control that proves ``perform`` can run at all - otherwise a
router that simply never executed would pass.
"""

from __future__ import annotations

import inspect

import pytest

from identity.chat.approvals import (
    ROUTER_PUBLIC_SURFACE,
    ChatApprovalRouter,
    Proposal,
)
from identity.chat.errors import (
    ApprovalRequired,
    CrossTenantRefused,
    DirectWriteRefused,
    InvalidScope,
)
from identity.cpapi.approvals import (
    APPROVAL_APPROVED,
    APPROVAL_CONSUMED,
    APPROVAL_DENIED,
    APPROVAL_PENDING,
    ApprovalStore,
)
from identity.cpapi.errors import ApiError
from identity.cpapi.fakes import FakeClock, fake_rng

TENANT_A = "tenant-acme"
TENANT_B = "tenant-globex"
AGENT_A = "coder"
CONVERSATION_1 = "conv-1"
ACTION = "memory.erase"
APPROVER = "root@acme.example"
CLIENT = "openwebui"


@pytest.fixture()
def store() -> ApprovalStore:
    return ApprovalStore(clock=FakeClock(), rng=fake_rng("apr"))


@pytest.fixture()
def router(store: ApprovalStore) -> ChatApprovalRouter:
    return ChatApprovalRouter(store)


def test_a_proposal_lands_in_the_control_plane_gate_and_does_not_run(
    router, store, credential
):
    """The proposal reaches identity.cpapi; the action itself has not run."""
    with pytest.raises(ApprovalRequired) as caught:
        router.propose(credential, action=ACTION, resource=credential.container_id)

    approval_id = caught.value.approval_id
    pending = store.list(status=APPROVAL_PENDING)
    assert [view.approvalId for view in pending] == [approval_id]
    assert pending[0].requester == credential.subject
    assert pending[0].action == ACTION
    assert pending[0].resource == credential.container_id
    assert router.status_of(approval_id) == APPROVAL_PENDING


def test_the_approval_required_outcome_is_the_control_planes_own(router, credential):
    """No second error vocabulary: the wire object is cpapi's, not a chat copy."""
    import identity.cpapi.errors as cpapi_errors

    with pytest.raises(ApprovalRequired) as caught:
        router.propose(credential, action=ACTION, resource=credential.container_id)
    wire = caught.value.as_api_error()
    reference = cpapi_errors.approval_required(caught.value.approval_id)
    assert isinstance(wire, ApiError)
    assert (wire.status, wire.code) == (reference.status, reference.code)
    assert wire.code == "approval_required"
    assert wire.details == {"approvalId": caught.value.approval_id}


def test_execute_is_refused_while_the_request_is_still_pending(
    router, store, credential
):
    """A direct write attempt with a pending approval is refused; nothing runs."""
    ran: list = []
    with pytest.raises(ApprovalRequired) as caught:
        router.propose(credential, action=ACTION, resource=credential.container_id)
    approval_id = caught.value.approval_id

    with pytest.raises(DirectWriteRefused) as refused:
        router.execute(
            credential,
            action=ACTION,
            resource=credential.container_id,
            approval_id=approval_id,
            perform=lambda: ran.append("executed"),
        )
    assert refused.value.code == "direct_write_refused"
    assert ran == []
    assert router.status_of(approval_id) == APPROVAL_PENDING


def test_execute_runs_once_after_approval_and_consumes_it(router, store, credential):
    """Control for the refusal above: approval makes ``perform`` run, once."""
    ran: list = []
    with pytest.raises(ApprovalRequired) as caught:
        router.propose(credential, action=ACTION, resource=credential.container_id)
    approval_id = caught.value.approval_id

    store.decide(approval_id, approve=True, approver=APPROVER)
    assert router.status_of(approval_id) == APPROVAL_APPROVED

    proposal = router.propose(
        credential, action=ACTION, resource=credential.container_id
    )
    assert isinstance(proposal, Proposal)
    assert proposal.approved is True
    assert proposal.approval_id == approval_id

    result = router.execute(
        credential,
        action=ACTION,
        resource=credential.container_id,
        approval_id=approval_id,
        perform=lambda: ran.append("executed") or "ok",
    )
    assert result == "ok"
    assert ran == ["executed"]
    assert router.status_of(approval_id) == APPROVAL_CONSUMED

    # One-shot: the consumed approval cannot be replayed.
    with pytest.raises(DirectWriteRefused):
        router.execute(
            credential,
            action=ACTION,
            resource=credential.container_id,
            approval_id=approval_id,
            perform=lambda: ran.append("again"),
        )
    assert ran == ["executed"]


def test_a_denied_proposal_never_executes(router, store, credential):
    ran: list = []
    with pytest.raises(ApprovalRequired) as caught:
        router.propose(credential, action=ACTION, resource=credential.container_id)
    approval_id = caught.value.approval_id
    store.decide(approval_id, approve=False, approver=APPROVER)
    assert router.status_of(approval_id) == APPROVAL_DENIED

    with pytest.raises(DirectWriteRefused):
        router.execute(
            credential,
            action=ACTION,
            resource=credential.container_id,
            approval_id=approval_id,
            perform=lambda: ran.append("executed"),
        )
    assert ran == []


def test_execute_refuses_an_approval_for_a_different_action_or_resource(
    router, store, credential
):
    ran: list = []
    with pytest.raises(ApprovalRequired) as caught:
        router.propose(credential, action=ACTION, resource=credential.container_id)
    approval_id = caught.value.approval_id
    store.decide(approval_id, approve=True, approver=APPROVER)

    for action, resource in (
        ("memory.purge", credential.container_id),
        (ACTION, f"{TENANT_A}:other-conversation"),
    ):
        with pytest.raises(DirectWriteRefused):
            router.execute(
                credential,
                action=action,
                resource=resource,
                approval_id=approval_id,
                perform=lambda: ran.append("executed"),
            )
    assert ran == []


def test_a_foreign_principal_cannot_consume_anothers_approval(
    router, store, credential, mint
):
    ran: list = []
    with pytest.raises(ApprovalRequired) as caught:
        router.propose(credential, action=ACTION, resource=credential.container_id)
    approval_id = caught.value.approval_id
    store.decide(approval_id, approve=True, approver=APPROVER)

    other = mint(TENANT_A, AGENT_A, CONVERSATION_1, subject="someone-else")
    with pytest.raises(DirectWriteRefused):
        router.execute(
            other,
            action=ACTION,
            resource=other.container_id,
            approval_id=approval_id,
            perform=lambda: ran.append("executed"),
        )
    assert ran == []
    assert router.status_of(approval_id) == APPROVAL_APPROVED


def test_a_proposal_on_another_tenants_resource_is_refused(router, credential):
    with pytest.raises(CrossTenantRefused) as caught:
        router.propose(
            credential,
            action=ACTION,
            resource=f"session:{TENANT_B}:analyst:conv-9",
        )
    assert caught.value.code == "cross_tenant"
    # Control: the credential's own container is an acceptable resource, so the
    # refusal above is about the tenant and not about the resource's shape.
    with pytest.raises(ApprovalRequired):
        router.propose(credential, action=ACTION, resource=credential.container_id)


def test_an_incomplete_proposal_is_refused(router, credential):
    for kwargs in ({"action": "", "resource": "x"}, {"action": "a", "resource": ""}):
        with pytest.raises(InvalidScope):
            router.propose(credential, **kwargs)


def test_the_router_exposes_no_direct_write_path():
    """The approval gate is the *only* way out, and approving is not ours."""
    surface = {
        name
        for name, member in inspect.getmembers(ChatApprovalRouter, callable)
        if not name.startswith("_")
    }
    assert surface == set(ROUTER_PUBLIC_SURFACE)
    forbidden = {
        "decide",
        "approve",
        "deny",
        "write",
        "put",
        "delete",
        "create",
        "mutate",
        "execute_unapproved",
    }
    assert surface & forbidden == set()


def test_pending_lists_only_this_principals_proposals(router, credential, mint):
    with pytest.raises(ApprovalRequired):
        router.propose(credential, action=ACTION, resource=credential.container_id)
    other = mint(TENANT_A, AGENT_A, CONVERSATION_1, subject="someone-else")
    assert len(router.pending(credential)) == 1
    assert router.pending(other) == []
