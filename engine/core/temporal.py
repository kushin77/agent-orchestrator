"""Temporal adapter seam — the contract for a future Temporal transport.

This core is intentionally **not** wired to a Temporal server: the fleet
ships the durable engine on its own event-sourced persistence first (offline,
deterministic, testable) and the Temporal transport lands later behind a
feature flag (GR-5 / AO-GR-6 — new surfaces ship flag-gated OFF).  This
module fixes the *contract* that transport must satisfy so the engine core
never changes when the transport arrives.

A :class:`TemporalAdapter` would expose the same durable operations the
engine already has — start / signal / query / terminate a workflow by
(tenant namespace, workflow id) — and map every engine concept onto
Temporal's model.  The mapping table is the normative reference:

| Engine core                         | Temporal                                                       |
|-------------------------------------|----------------------------------------------------------------|
| ``NamespaceRegistry`` per tenant    | per-tenant Temporal **namespace** (namespace-per-tenant)       |
| per-tenant queues on a Namespace    | per-tenant **task queues** (``tenant:default``, …)             |
| ``WorkflowSpec`` + ``Step``         | **Workflow** + **Activity** definitions                        |
| ``EventRecord`` transcript          | Temporal **history / event** log                               |
| saga workflow (reverse compensation)| Temporal **saga** pattern (compensations in reverse order)     |
| ``RetryPolicy`` (backoff)           | Temporal ``RetryPolicy`` (initial/backoff/max interval/attempts)|
| ``WorkflowExecution.resume``        | replay of workflow history after worker restart                |
| ``WORKFLOW_RESUMED``                | workflow task **replay** marker                                |

The adapter protocol below is deliberately duck-typed (no Temporal SDK
dependency in this repo, per the offline stack rule) so the future transport
can be implemented against either the Temporal Python SDK or the REST/HTTP
worker API without the core importing either.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Protocol

from .model import WorkflowStatus


class TemporalAdapter(Protocol):
    """Surface a future Temporal transport must implement.

    Each call is scoped by ``namespace_id`` (the Temporal namespace) and
    ``workflow_id``, mirroring :class:`Engine`'s namespace-scoped API so the
    transport is a drop-in replacement for the in-process store + runtime.
    """

    def start_workflow(
        self,
        namespace_id: str,
        workflow_type: str,
        spec: Mapping[str, Any],
        inputs: Optional[Mapping[str, Any]] = None,
        workflow_id: Optional[str] = None,
        task_queue: Optional[str] = None,
    ) -> str:
        """Start a workflow in the tenant's namespace/queue; return its id."""
        ...

    def get_status(self, namespace_id: str, workflow_id: str) -> WorkflowStatus:
        """Describe a workflow's status in one namespace."""
        ...

    def signal_workflow(
        self, namespace_id: str, workflow_id: str, signal_name: str, payload: Mapping[str, Any]
    ) -> None:
        """Deliver a signal to a running workflow (e.g. resume/continue)."""
        ...

    def terminate_workflow(self, namespace_id: str, workflow_id: str, reason: str) -> None:
        """Force-terminate a workflow (no compensation run)."""
        ...


# concept-level contract documentation (imported by the core README example).
TEMPORAL_CONCEPT_MAP: Dict[str, str] = {
    "namespace": "per-tenant Temporal namespace (ProvisionTenantWorkflow pattern)",
    "task_queue": "per-tenant queues: default / high-priority / scheduled",
    "workflow": "WorkflowSpec (durable, event-sourced steps)",
    "activity": "a single Step handler invocation",
    "saga": "compensations registered per succeeded step, run in reverse order",
    "retry_policy": "exponential backoff on non-saga steps",
    "history": "the append-only EventRecord transcript (WorkflowExecution.resume replays it)",
}


def workflow_status_to_temporal(status: WorkflowStatus) -> str:
    """Map an engine status onto Temporal's workflow execution status names."""
    return {
        WorkflowStatus.PENDING: "WORKFLOW_EXECUTION_STATUS_RUNNING",
        WorkflowStatus.RUNNING: "WORKFLOW_EXECUTION_STATUS_RUNNING",
        WorkflowStatus.SUCCEEDED: "WORKFLOW_EXECUTION_STATUS_COMPLETED",
        WorkflowStatus.FAILED: "WORKFLOW_EXECUTION_STATUS_FAILED",
        WorkflowStatus.ROLLED_BACK: "WORKFLOW_EXECUTION_STATUS_TERMINATED",
    }[status]
