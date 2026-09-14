"""Approvals - the chat surface's only route to a side effect.

A turn that proposes an action does not perform it. It is handed to the merged
control-plane approval gate (``identity.cpapi.approvals``): the gate either
finds an active, already-approved authorization for exactly that
``(action, resource)``, or it records an idempotent pending request and the
chat surface returns the control plane's ``approval_required`` outcome - the
mutation does not run.

The chat surface deliberately has **no approver**. Approval authority lives on
the control-plane surface (``ApprovalStore.decide``, driven by the portal); a
router that could approve its own request would make the gate a formality.
:data:`ROUTER_PUBLIC_SURFACE` names this class's entire public call surface,
and a test asserts nothing outside it is exposed - so a later lane cannot
quietly add a write path back onto the chat surface.

``execute`` is the single side effect: it requires an ``approved`` (not
pending, not denied, not consumed) request naming exactly this action and
resource, requested by exactly this principal, on a resource inside the
principal's own tenant; it then runs the caller-supplied operation and consumes
the approval (one-shot). Every other combination is a
:class:`DirectWriteRefused`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, List, Tuple

from identity.cpapi.approvals import (
    APPROVAL_APPROVED,
    ApprovalGate,
    ApprovalStore,
)

from .errors import (
    ApprovalRequired,
    CrossTenantRefused,
    DirectWriteRefused,
    InvalidScope,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .credential import ChatCredential

#: The router's entire public call surface (approve/decide is *not* on it).
ROUTER_PUBLIC_SURFACE: Tuple[str, ...] = ("execute", "pending", "propose", "status_of")


@dataclass(frozen=True)
class Proposal:
    """An action proposed through the approval gate."""

    approval_id: str
    action: str
    resource: str
    requester: str
    approved: bool


def _requester_of(credential: "ChatCredential") -> str:
    """The principal an approval is requested for (the credential's subject)."""
    return credential.subject or credential.agent_id


def _assert_resource_in_scope(credential: "ChatCredential", resource: str) -> None:
    """A proposal may only name a resource inside the credential's own tenant.

    Either the conversation's own memory container, or a resource explicitly
    prefixed with the tenant id (``<tenant>:<thing>``). Anything else is a
    cross-tenant proposal and is refused before the gate ever sees it.
    """
    if resource == credential.container_id:
        return
    if resource.startswith(f"{credential.tenant_id}:"):
        return
    raise CrossTenantRefused(
        f"resource {resource!r} is not inside tenant {credential.tenant_id!r}; "
        "the chat surface never proposes an action on another tenant's resource"
    )


class ChatApprovalRouter:
    """Routes the chat surface's proposals into ``identity.cpapi``'s gate."""

    def __init__(self, store: ApprovalStore) -> None:
        self.store = store
        self.gate = ApprovalGate(store)

    # --- propose ---------------------------------------------------------- #

    def propose(
        self,
        credential: "ChatCredential",
        *,
        action: str,
        resource: str,
        reason: str = "",
    ) -> Proposal:
        """Ask the control-plane gate for authorization to run ``action``.

        Returns an approved :class:`Proposal` only when the gate reports an
        active approval. Otherwise the pending request is recorded by the gate
        and :class:`ApprovalRequired` is raised carrying its id - nothing ran.
        """
        if not action or not isinstance(action, str):
            raise InvalidScope("action is required to propose an approval")
        if not resource or not isinstance(resource, str):
            raise InvalidScope("resource is required to propose an approval")
        _assert_resource_in_scope(credential, resource)
        requester = _requester_of(credential)
        execute, approval = self.gate.guard(
            action=action, resource=resource, requester=requester, reason=reason
        )
        if not execute:
            raise ApprovalRequired(approval.approvalId)
        return Proposal(
            approval_id=approval.approvalId,
            action=approval.action,
            resource=approval.resource,
            requester=approval.requester,
            approved=True,
        )

    # --- execute ---------------------------------------------------------- #

    def execute(
        self,
        credential: "ChatCredential",
        *,
        action: str,
        resource: str,
        approval_id: str,
        perform: Callable[[], Any],
    ) -> Any:
        """Run ``perform`` for an already-approved request, then consume it.

        The only side effect the chat surface can take. A pending, denied,
        consumed or mismatched request is a :class:`DirectWriteRefused` and
        ``perform`` is never called.
        """
        _assert_resource_in_scope(credential, resource)
        approval = self.store.get(approval_id)
        if approval.status != APPROVAL_APPROVED:
            raise DirectWriteRefused(
                f"approval {approval_id} is {approval.status!r}; the chat "
                "surface has no direct write path"
            )
        if approval.action != action or approval.resource != resource:
            raise DirectWriteRefused(
                f"approval {approval_id} covers "
                f"({approval.action!r}, {approval.resource!r}), not "
                f"({action!r}, {resource!r})"
            )
        if approval.requester != _requester_of(credential):
            raise DirectWriteRefused(
                f"approval {approval_id} was requested by "
                f"{approval.requester!r}, not this principal"
            )
        result = perform()
        self.store.consume(approval_id)
        return result

    # --- inspect ---------------------------------------------------------- #

    def pending(self, credential: "ChatCredential") -> List[Proposal]:
        """This principal's proposals still awaiting an approver."""
        requester = _requester_of(credential)
        return [
            Proposal(
                approval_id=approval.approvalId,
                action=approval.action,
                resource=approval.resource,
                requester=approval.requester,
                approved=approval.status == APPROVAL_APPROVED,
            )
            for approval in self.store.list(status="pending")
            if approval.requester == requester
        ]

    def status_of(self, approval_id: str) -> str:
        """The raw lifecycle status the control plane holds for an id."""
        return str(self.store.get(approval_id).status)
