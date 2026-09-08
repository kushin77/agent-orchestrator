"""Approval-gated destructive operations (govctl-style).

Issue #38 AC3: destructive control-plane actions (agent retire, tenant pause /
kill switch) do not execute until an approver with the right authority has
explicitly approved a request naming exactly that action+resource. This is the
shared-governance ``approval_workflows`` pattern ported to the control plane:
request → (approve | deny) → execute, with every step appended to the audit
ledger by the caller.

Lifecycle::

    pending --approve--> approved --(action runs + consumed)--> consumed
    pending --deny-----> denied
    approved --expire (after ttl / once consumed)--> consumed

Gate semantics (``ApprovalGate.guard``) used by destructive endpoints:

1. look for an active (``approved``, not yet consumed) approval covering the
   exact ``(action, resource)``;
2. if found → the caller executes and then ``consume``s it (one-shot);
3. if not → an idempotent ``pending`` request is recorded for the requester
   (a re-request returns the same id), the caller returns
   ``202 approval_required`` and emits an ``approval.required`` outbox event —
   the destructive mutation does **not** run.

All ids are injectable-``rng``-derived and timestamps come from the clock seam
so the whole gate is deterministic under test.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .errors import conflict, not_found, refused
from .model import ApprovalView

# Approval lifecycle states.
APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_DENIED = "denied"
APPROVAL_CONSUMED = "consumed"


class ApprovalStore:
    """Persists approval requests (in-memory default)."""

    def __init__(self, *, clock: Any, rng: Any) -> None:
        self._clock = clock
        self._rng = rng
        self._by_id: Dict[str, ApprovalView] = {}
        self._order: List[str] = []

    def request(
        self,
        *,
        action: str,
        resource: str,
        requester: str,
        reason: str = "",
    ) -> ApprovalView:
        """Record a pending approval request (idempotent per requester+action+resource)."""
        for approval_id in self._order:
            existing = self._by_id[approval_id]
            if (
                existing.status == APPROVAL_PENDING
                and existing.action == action
                and existing.resource == resource
                and existing.requester == requester
            ):
                return existing
        approval = ApprovalView(
            approvalId=f"apr_{self._rng()[:16]}",
            action=action,
            resource=resource,
            requester=requester,
            reason=reason,
            status=APPROVAL_PENDING,
        )
        self._by_id[approval.approvalId] = approval
        self._order.append(approval.approvalId)
        return approval

    def get(self, approval_id: str) -> ApprovalView:
        approval = self._by_id.get(approval_id)
        if approval is None:
            raise not_found(f"unknown approval request {approval_id!r}", code="not_found")
        return approval

    def list(self, *, status: Optional[str] = None, limit: int = 100) -> List[ApprovalView]:
        matches = [self._by_id[i] for i in self._order]
        if status is not None:
            matches = [a for a in matches if a.status == status]
        return matches[-limit:]

    def decide(
        self, approval_id: str, *, approve: bool, approver: str
    ) -> ApprovalView:
        """Approve or deny a pending request (one-way; idempotent on repeat)."""
        approval = self.get(approval_id)
        if approval.status == APPROVAL_APPROVED and approve:
            return approval
        if approval.status == APPROVAL_DENIED and not approve:
            return approval
        if approval.status != APPROVAL_PENDING:
            raise conflict(
                f"approval {approval_id} is {approval.status!r}, not pending",
                code="approval_not_pending",
            )
        updated = ApprovalView(
            approvalId=approval.approvalId,
            action=approval.action,
            resource=approval.resource,
            requester=approval.requester,
            reason=approval.reason,
            status=APPROVAL_APPROVED if approve else APPROVAL_DENIED,
            approver=approver,
            decidedAt=self._clock.now_utc(),
        )
        self._by_id[approval_id] = updated
        return updated

    def consume(self, approval_id: str) -> ApprovalView:
        """Mark an approved approval consumed after its action executed."""
        approval = self.get(approval_id)
        if approval.status == APPROVAL_CONSUMED:
            return approval
        if approval.status != APPROVAL_APPROVED:
            raise conflict(
                f"approval {approval_id} is {approval.status!r}, only approved approvals run",
                code="approval_not_approved",
            )
        updated = ApprovalView(
            approvalId=approval.approvalId,
            action=approval.action,
            resource=approval.resource,
            requester=approval.requester,
            reason=approval.reason,
            status=APPROVAL_CONSUMED,
            approver=approval.approver,
            decidedAt=approval.decidedAt,
        )
        self._by_id[approval_id] = updated
        return updated

    def active_approval(self, action: str, resource: str) -> Optional[ApprovalView]:
        """The one approved-not-yet-consumed approval covering action+resource."""
        for approval_id in self._order:
            approval = self._by_id[approval_id]
            if (
                approval.status == APPROVAL_APPROVED
                and approval.action == action
                and approval.resource == resource
            ):
                return approval
        return None


class ApprovalGate:
    """Guard destructive endpoints: nothing destructive runs unapproved.

    ``guard`` returns either ``(execute=True, approval=None)`` when an active
    approval already covers the action, or ``(execute=False, approval=pending)``
    after recording an idempotent pending request. The endpoint decides what to
    do with each outcome (run + consume, or 202 approval_required).
    """

    def __init__(self, store: ApprovalStore) -> None:
        self.store = store

    def guard(self, *, action: str, resource: str, requester: str, reason: str = ""):
        active = self.store.active_approval(action, resource)
        if active is not None:
            return True, active
        pending = self.store.request(
            action=action, resource=resource, requester=requester, reason=reason
        )
        return False, pending

    def require(self, *, action: str, resource: str, requester: str) -> ApprovalView:
        """Internal helper: the approved approval the caller must consume."""
        active = self.store.active_approval(action, resource)
        if active is None:
            raise refused(
                f"no active approval for {action} on {resource!r}; request one first"
            )
        return active
