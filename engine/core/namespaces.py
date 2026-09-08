"""Per-tenant namespaces and their isolation.

The engine mirrors Temporal's namespace-per-tenant model
(``shared-temporal/patterns/multi_tenancy.go``): every tenant is isolated in
its own :class:`Namespace` with plan-derived retention and its own task
queues.  A :class:`NamespaceRegistry` is the only way to reach a namespace,
and every engine lookup is scoped to exactly one namespace id — there is no
cross-namespace fallback (the no-cross-tenant doctrine shared with
``registry/service``, ``identity/rbac`` and the tenant-scoped MCP gateway).

Namespaces also carry the per-workflow cost + SLA aggregates harvested from
``shared-temporal/governance/cost-tracking.ts`` / ``sla-enforcement.ts``: a
completed workflow is recorded into *its own* namespace's ledger, so a cost
or SLA report for tenant B never includes tenant A's workflows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import NamespaceExistsError, UnknownNamespaceError
from .model import CostEntry

_PLAN_RETENTION_DAYS: Mapping[str, int] = {
    "free": 7,
    "standard": 30,
    "premium": 365,
}

_DEFAULT_QUEUES: Tuple[str, ...] = ("default", "high-priority", "scheduled")


@dataclass
class Namespace:
    """A tenant-scoped execution namespace (queue + state isolation).

    ``cost_entries`` aggregates per-workflow cost lines; the counters track
    completed/failed/sla-breached workflows for the per-namespace reports.
    """

    namespace_id: str
    plan: str = "standard"
    retention_days: int = 30
    queues: Tuple[str, ...] = _DEFAULT_QUEUES
    status: str = "active"
    created_at: str = ""
    cost_entries: List[CostEntry] = field(default_factory=list)
    total_cost: float = 0.0
    completed_workflows: int = 0
    failed_workflows: int = 0
    rolled_back_workflows: int = 0
    sla_breached_workflows: int = 0

    @property
    def default_queue(self) -> str:
        return f"{self.namespace_id}:{self.queues[0]}"


def plan_retention_days(plan: str) -> int:
    """Plan-derived retention (free/standard/premium), fail-closed default."""
    return _PLAN_RETENTION_DAYS.get(plan, _PLAN_RETENTION_DAYS["standard"])


class NamespaceRegistry:
    """Owns the namespace set; every access is by explicit namespace id."""

    def __init__(self) -> None:
        self._namespaces: Dict[str, Namespace] = {}

    # -- lifecycle ----------------------------------------------------------

    def create(
        self,
        namespace_id: str,
        plan: str = "standard",
        retention_days: Optional[int] = None,
        queues: Optional[Sequence[str]] = None,
        created_at: str = "",
    ) -> Namespace:
        if not namespace_id:
            raise ValueError("namespace_id must be non-empty")
        if namespace_id in self._namespaces:
            raise NamespaceExistsError(f"namespace already exists: {namespace_id}")
        ns = Namespace(
            namespace_id=namespace_id,
            plan=plan,
            retention_days=retention_days if retention_days is not None else plan_retention_days(plan),
            queues=tuple(queues) if queues is not None else _DEFAULT_QUEUES,
            created_at=created_at,
        )
        self._namespaces[namespace_id] = ns
        return ns

    def remove(self, namespace_id: str) -> None:
        """Remove a namespace (used by tenant-provisioning rollback)."""
        self.require(namespace_id)
        del self._namespaces[namespace_id]

    # -- reads --------------------------------------------------------------

    def require(self, namespace_id: str) -> Namespace:
        """The namespace or :class:`UnknownNamespaceError` (never a fallback)."""
        ns = self._namespaces.get(namespace_id)
        if ns is None:
            raise UnknownNamespaceError(f"unknown namespace: {namespace_id}")
        return ns

    def get(self, namespace_id: str) -> Optional[Namespace]:
        return self._namespaces.get(namespace_id)

    def list(self) -> List[Namespace]:
        return sorted(self._namespaces.values(), key=lambda ns: ns.namespace_id)

    # -- per-workflow cost + SLA recording (issue #21 acceptance #2) --------

    def record_terminal_workflow(
        self,
        namespace_id: str,
        *,
        succeeded: bool,
        rolled_back: bool,
        cost_entries: Sequence[CostEntry],
        sla_breached: bool,
    ) -> None:
        ns = self.require(namespace_id)
        ns.cost_entries.extend(cost_entries)
        ns.total_cost += sum(entry.total_cost for entry in cost_entries)
        if rolled_back:
            ns.rolled_back_workflows += 1
        elif succeeded:
            ns.completed_workflows += 1
        else:
            ns.failed_workflows += 1
        if sla_breached:
            ns.sla_breached_workflows += 1

    def cost_report(self, namespace_id: str) -> Mapping[str, object]:
        """Per-namespace cost report (scoped to exactly one tenant)."""
        ns = self.require(namespace_id)
        by_resource: Dict[str, float] = {}
        for entry in ns.cost_entries:
            by_resource[entry.resource_type] = (
                by_resource.get(entry.resource_type, 0.0) + entry.total_cost
            )
        return {
            "namespace_id": ns.namespace_id,
            "total_cost": ns.total_cost,
            "workflow_count": self._workflow_count(ns),
            "breakdown": by_resource,
        }

    def _workflow_count(self, ns: Namespace) -> int:
        return ns.completed_workflows + ns.failed_workflows + ns.rolled_back_workflows

    def sla_report(self, namespace_id: str) -> Mapping[str, object]:
        ns = self.require(namespace_id)
        total = self._workflow_count(ns)
        return {
            "namespace_id": ns.namespace_id,
            "workflows": total,
            "sla_breached": ns.sla_breached_workflows,
            "sla_met": total - ns.sla_breached_workflows,
        }
