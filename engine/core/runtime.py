"""Engine runtime — durable, namespace-scoped, saga-capable workflow executor.

The :class:`Engine` runs a :class:`WorkflowSpec` inside one namespace against
an append-only :class:`EventStore`.  Everything the engine does is an event
appended to the store and projected onto the in-memory
:class:`WorkflowExecution` — so an interruption loses nothing (every append
is flushed) and a restart rebuilds the exact same state from the log
(:meth:`Engine.resume`, deterministic replay).

Highlights (issue #21 acceptance criteria):

1. *Hosts workflows* — task execution, agent-loop steps, fan-out/join, tenant
   provisioning are all just specs of steps; any registered step kind runs
   through the same durable scheduler (:meth:`Engine.provision_tenant` is a
   tenant-provisioning saga).
2. *Namespace/queue isolation per tenant* — every call is scoped to exactly
   one namespace; per-workflow cost + SLA land on the owning namespace.
3. *Durable, resumes mid-flight* — event-sourced persistence + replay.
4. *Provider-agnostic* — ``task`` steps dispatch through an injected
   :class:`ModelGateway`; no provider is ever imported or called directly.

A step's ``retry`` policy is honored for non-saga workflows (exponential
backoff recorded as ``retry_scheduled`` events, no real sleeping — the delay
is deterministic from the policy).  Saga workflows never retry a step
(``advanced_patterns.go``: "No retries in saga"); a failing saga step runs
the registered compensations of every earlier succeeded step in *reverse*
completion order, then the workflow ends ROLLED_BACK (or FAILED when a
compensation itself fails).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple

from .errors import (
    UnknownStepHandlerError,
    UnknownWorkflowError,
    WorkflowValidationError,
)
from .events import EventRecord, EventStore, InMemoryEventStore
from .handlers import (
    BUILTIN_HANDLERS,
    DEFAULT_HANDLERS,
    Handler,
    StepContext,
)
from .model import (
    Compensation,
    EventKind,
    Step,
    StepKind,
    StepStatus,
    WorkflowKind,
    WorkflowSpec,
    WorkflowStatus,
    spec_to_dict,
)
from .namespaces import NamespaceRegistry
from .workflow import WorkflowExecution


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RealClock:
    """Wall clock producing RFC-3339-ish UTC timestamps."""

    def now_iso(self) -> str:
        return _now_iso()


def _json_safe(value: Any) -> Any:
    """Return value when JSON-safe, else None (never store a non-serializable)."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ExecutionReport:
    """Result of one :meth:`Engine.advance` call."""

    namespace_id: str
    workflow_id: str
    status: str
    terminal: bool
    handlers_executed: int
    events_appended: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "namespace_id": self.namespace_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "terminal": self.terminal,
            "handlers_executed": self.handlers_executed,
            "events_appended": self.events_appended,
        }


def provisioning_spec(tenant_id: str, plan: str = "standard") -> WorkflowSpec:
    """Tenant-provisioning saga (namespace-per-tenant pattern).

    Mirrors ``shared-temporal/patterns/multi_tenancy.go``'s
    ProvisionTenantWorkflow as a saga: create the per-tenant namespace,
    configure retention for the plan, then initialise billing.  Each step
    that creates a durable artifact registers its removal as a compensation,
    so a later failure rolls the tenant back to *not existing*.
    """
    if not tenant_id:
        raise ValueError("tenant_id must be non-empty")
    steps = [
        Step(
            step_id="create-namespace",
            kind=StepKind.PROVISION,
            name="create tenant namespace",
            handler="core.provision_create_namespace",
            args={"tenant_id": tenant_id, "plan": plan},
            compensate=Compensation(
                name="remove-namespace",
                handler="core.provision_create_namespace",
                args={"tenant_id": tenant_id},
            ),
        ),
        Step(
            step_id="configure-retention",
            kind=StepKind.PROVISION,
            name="configure retention",
            handler="core.provision_configure",
            args={"tenant_id": tenant_id},
        ),
        Step(
            step_id="init-billing",
            kind=StepKind.PROVISION,
            name="initialise billing",
            handler="core.provision_billing",
            args={"tenant_id": tenant_id},
            compensate=Compensation(
                name="remove-billing",
                handler="core.provision_billing",
                args={"tenant_id": tenant_id},
            ),
        ),
    ]
    return WorkflowSpec(
        name="tenant-provisioning",
        kind=WorkflowKind.TENANT_PROVISIONING,
        steps=steps,
        saga=True,
        description=f"provision tenant namespace for {tenant_id} ({plan})",
    )


class Engine:
    """Durable orchestration engine core."""

    def __init__(
        self,
        store: Optional[EventStore] = None,
        namespaces: Optional[NamespaceRegistry] = None,
        gateway: Any = None,
        clock: Any = None,
        extra_handlers: Optional[Mapping[str, Handler]] = None,
    ) -> None:
        self.store = store or InMemoryEventStore()
        self.namespaces = namespaces or NamespaceRegistry()
        self.gateway = gateway
        self.clock = clock or RealClock()
        self._handlers: Dict[str, Handler] = {}
        self._live: Dict[Tuple[str, str], WorkflowExecution] = {}
        for key, handler in BUILTIN_HANDLERS.items():
            self._handlers[key] = handler
        for key, handler in (extra_handlers or {}).items():
            self._handlers[key] = handler
        # A platform/system namespace always exists for provisioning workflows.
        if self.namespaces.get("system") is None:
            self.namespaces.create(
                namespace_id="system", plan="premium", created_at=self.clock.now_iso()
            )

    # -- handler registration ----------------------------------------------

    def register_handler(self, key: str, handler: Handler) -> None:
        self._handlers[key] = handler

    def has_handler(self, key: str) -> bool:
        return key in self._handlers

    def _resolve_step_handler(self, step: Step) -> Handler:
        key = step.handler or DEFAULT_HANDLERS.get(
            step.kind.value if isinstance(step.kind, StepKind) else str(step.kind)
        )
        handler = self._handlers.get(key) if key else None
        if handler is None:
            raise UnknownStepHandlerError(
                f"no handler registered for step {step.step_id!r} (kind={step.kind})"
            )
        return handler

    # -- workflow lifecycle ------------------------------------------------

    def start_workflow(
        self,
        namespace_id: str,
        spec: WorkflowSpec,
        inputs: Optional[Mapping[str, Any]] = None,
        workflow_id: Optional[str] = None,
    ) -> WorkflowExecution:
        """Start a workflow in one namespace (namespace must already exist)."""
        self.namespaces.require(namespace_id)  # fail closed on unknown namespace
        self._validate_spec(spec)
        wf_id = workflow_id or f"wf-{uuid.uuid4().hex[:12]}"
        if self.store.events(namespace_id, wf_id):
            raise WorkflowValidationError(f"workflow already exists: {namespace_id}/{wf_id}")
        execution = WorkflowExecution(
            namespace_id=namespace_id,
            workflow_id=wf_id,
            spec=spec,
            inputs=dict(inputs or {}),
        )
        self._append(
            execution,
            EventKind.WORKFLOW_STARTED,
            {
                "spec": dict(spec_to_dict(spec)),
                "inputs": dict(inputs or {}),
                "started_at": self.clock.now_iso(),
            },
        )
        self._live[(namespace_id, wf_id)] = execution
        return execution

    def advance(
        self, execution: WorkflowExecution, max_handlers: Optional[int] = None
    ) -> ExecutionReport:
        """Run the workflow forward until terminal or ``max_handlers`` runs.

        ``max_handlers`` bounds how many step/compensation handler calls one
        call may execute — callers simulate an interruption by advancing one
        handler at a time; each event is persisted before the next handler
        runs, so a crash between calls loses nothing.
        """
        start_event_count = len(execution.events)
        handlers_run = 0
        if not execution.status.terminal:
            while True:
                if execution.status.terminal:
                    break
                if max_handlers is not None and handlers_run >= max_handlers:
                    break
                action = self._next_action(execution)
                atype = action["type"]
                if atype == "complete":
                    self._append(
                        execution,
                        EventKind.WORKFLOW_COMPLETED,
                        {"completed_at": self.clock.now_iso()},
                    )
                    self._record_terminal(execution)
                    break
                if atype == "workflow_failed":
                    self._append(
                        execution,
                        EventKind.WORKFLOW_FAILED,
                        {
                            "failed_step_id": action["step_id"],
                            "error": action["error"],
                            "completed_at": self.clock.now_iso(),
                        },
                    )
                    self._record_terminal(execution)
                    break
                if atype == "finalize_rollback":
                    self._finalize_rollback(execution)
                    break
                if atype == "attempt":
                    self._run_attempt(execution, action["step"], action["attempt"])
                    handlers_run += 1
                    continue
                if atype == "compensate":
                    self._run_compensation(execution, action["step"])
                    handlers_run += 1
                    continue
                raise AssertionError(f"unknown action {atype!r}")
        return ExecutionReport(
            namespace_id=execution.namespace_id,
            workflow_id=execution.workflow_id,
            status=execution.status.value,
            terminal=execution.status.terminal,
            handlers_executed=handlers_run,
            events_appended=len(execution.events) - start_event_count,
        )

    def run_workflow(
        self,
        namespace_id: str,
        spec: WorkflowSpec,
        inputs: Optional[Mapping[str, Any]] = None,
        workflow_id: Optional[str] = None,
    ) -> WorkflowExecution:
        """Start a workflow and advance it to a terminal state."""
        execution = self.start_workflow(namespace_id, spec, inputs, workflow_id)
        self.advance(execution)
        return execution

    def resume(self, namespace_id: str, workflow_id: str) -> WorkflowExecution:
        """Rebuild a workflow from its persisted log and make it resumable.

        Raises :class:`UnknownWorkflowError` when the namespace does not know
        this workflow id (including when it belongs to another namespace).
        """
        self.namespaces.require(namespace_id)
        records = self.store.events(namespace_id, workflow_id)
        if not records:
            raise UnknownWorkflowError(
                f"unknown workflow {workflow_id!r} in namespace {namespace_id!r}"
            )
        execution = WorkflowExecution.from_events(records)
        if not execution.status.terminal:
            self._append(
                execution,
                EventKind.WORKFLOW_RESUMED,
                {"resumed_at": self.clock.now_iso()},
            )
        self._live[(namespace_id, workflow_id)] = execution
        return execution

    def get_workflow(self, namespace_id: str, workflow_id: str) -> WorkflowExecution:
        """Live or replayed projection — strictly namespace-scoped."""
        live = self._live.get((namespace_id, workflow_id))
        if live is not None:
            return live
        self.namespaces.require(namespace_id)
        records = self.store.events(namespace_id, workflow_id)
        if not records:
            raise UnknownWorkflowError(
                f"unknown workflow {workflow_id!r} in namespace {namespace_id!r}"
            )
        return WorkflowExecution.from_events(records)

    # -- tenant provisioning (acceptance #1) -------------------------------

    def provision_tenant(
        self, tenant_id: str, plan: str = "standard", workflow_id: Optional[str] = None
    ) -> WorkflowExecution:
        """Run the tenant-provisioning saga in the platform (system) namespace."""
        spec = provisioning_spec(tenant_id, plan)
        return self.run_workflow(
            "system", spec, inputs={"tenant_id": tenant_id, "plan": plan}, workflow_id=workflow_id
        )

    # -- internals ----------------------------------------------------------

    def _append(
        self, execution: WorkflowExecution, kind: EventKind, payload: Mapping[str, Any]
    ) -> None:
        record = self.store.append(
            EventRecord(
                seq=0,
                namespace_id=execution.namespace_id,
                workflow_id=execution.workflow_id,
                kind=kind,
                ts=self.clock.now_iso(),
                payload=dict(payload),
            )
        )
        execution.apply(record)

    def _validate_spec(self, spec: WorkflowSpec) -> None:
        if not spec.name:
            raise WorkflowValidationError("workflow spec requires a name")
        if not spec.steps:
            raise WorkflowValidationError("workflow spec requires at least one step")
        seen = set()
        for step in spec.steps:
            if not step.step_id:
                raise WorkflowValidationError("each step requires a step_id")
            if step.step_id in seen:
                raise WorkflowValidationError(f"duplicate step_id {step.step_id!r}")
            seen.add(step.step_id)
            try:
                kind = StepKind(step.kind) if not isinstance(step.kind, StepKind) else step.kind
            except ValueError as exc:
                raise WorkflowValidationError(f"unknown step kind {step.kind!r}") from exc
            if kind not in set(StepKind):
                raise WorkflowValidationError(f"unknown step kind {kind!r}")
            # Fail closed: every step must resolve to a registered handler now.
            self._resolve_step_handler(step)
            if step.compensate is not None and step.compensate.handler not in self._handlers:
                raise WorkflowValidationError(
                    f"compensation handler {step.compensate.handler!r} for "
                    f"step {step.step_id!r} is not registered"
                )

    def _allowed_attempts(self, execution: WorkflowExecution, step: Step) -> int:
        if step.retry is not None and not execution.spec.saga:
            return step.retry.max_attempts
        return 1  # saga steps never retry (advanced_patterns.go)

    def _next_action(self, execution: WorkflowExecution) -> Dict[str, Any]:
        if execution.rollback_in_progress:
            pending = execution.pending_compensations()
            if pending:
                return {"type": "compensate", "step": pending[0][0]}
            return {"type": "finalize_rollback"}
        for step in execution.steps():
            state = execution.state_for(step.step_id)
            if state.status in (StepStatus.SUCCEEDED, StepStatus.COMPENSATED):
                continue
            allowed = self._allowed_attempts(execution, step)
            if state.status is StepStatus.FAILED and state.attempts >= allowed:
                return {
                    "type": "workflow_failed",
                    "step_id": step.step_id,
                    "error": state.last_error or "step failed",
                }
            return {"type": "attempt", "step": step, "attempt": state.attempts + 1}
        return {"type": "complete"}

    def _run_attempt(self, execution: WorkflowExecution, step: Step, attempt: int) -> None:
        if attempt > 1:
            self._append(
                execution,
                EventKind.RETRY_SCHEDULED,
                {
                    "step_id": step.step_id,
                    "attempt": attempt,
                    "delay_seconds": step.retry.delay_before_retry(attempt)
                    if step.retry is not None
                    else 0.0,
                },
            )
        self._append(
            execution,
            EventKind.STEP_STARTED,
            {"step_id": step.step_id, "attempt": attempt},
        )
        handler = self._resolve_step_handler(step)
        ctx = StepContext(
            namespace_id=execution.namespace_id,
            workflow_id=execution.workflow_id,
            step_id=step.step_id,
            attempt=attempt,
            inputs=execution.inputs,
            engine=self,
        )
        try:
            result = handler.run(step, ctx)
        except Exception as exc:  # any handler failure is a recorded step failure
            self._append(
                execution,
                EventKind.STEP_FAILED,
                {"step_id": step.step_id, "attempt": attempt, "error": str(exc)},
            )
            if execution.spec.saga:
                self._append(
                    execution,
                    EventKind.COMPENSATION_BEGAN,
                    {"failed_step_id": step.step_id},
                )
            return
        cost = self._cost_dict(execution, step, result)
        # Persist a JSON-safe output: for a typed gateway result, store its
        # content; otherwise store the raw value when serializable.
        persisted_output = getattr(result, "content", result)
        self._append(
            execution,
            EventKind.STEP_SUCCEEDED,
            {
                "step_id": step.step_id,
                "attempt": attempt,
                "cost": cost,
                "output": _json_safe(persisted_output),
            },
        )

    def _run_compensation(self, execution: WorkflowExecution, step: Step) -> None:
        comp = step.compensate
        if comp is None:
            raise AssertionError("compensation requested for a step without one")
        self._append(
            execution,
            EventKind.COMPENSATION_STARTED,
            {"step_id": step.step_id, "compensation": comp.name},
        )
        handler = self._handlers.get(comp.handler)
        if handler is None or handler.compensate is None:
            self._append(
                execution,
                EventKind.COMPENSATION_FAILED,
                {
                    "step_id": step.step_id,
                    "error": f"no compensation handler for {comp.handler!r}",
                },
            )
            return
        ctx = StepContext(
            namespace_id=execution.namespace_id,
            workflow_id=execution.workflow_id,
            step_id=step.step_id,
            attempt=0,
            inputs=execution.inputs,
            engine=self,
        )
        try:
            handler.compensate(comp, ctx)
        except Exception as exc:  # a failed compensation is recorded, saga continues
            self._append(
                execution,
                EventKind.COMPENSATION_FAILED,
                {"step_id": step.step_id, "error": str(exc)},
            )
            return
        self._append(
            execution,
            EventKind.COMPENSATION_SUCCEEDED,
            {"step_id": step.step_id, "compensation": comp.name},
        )
        self._append(
            execution,
            EventKind.STEP_COMPENSATED,
            {"step_id": step.step_id},
        )

    def _finalize_rollback(self, execution: WorkflowExecution) -> None:
        payload: Dict[str, Any] = {"completed_at": self.clock.now_iso()}
        if execution.compensation_failures:
            payload["compensation_failures"] = list(execution.compensation_failures)
            self._append(execution, EventKind.WORKFLOW_FAILED, payload)
        else:
            self._append(execution, EventKind.WORKFLOW_ROLLED_BACK, payload)
        self._record_terminal(execution)

    def _cost_dict(
        self, execution: WorkflowExecution, step: Step, result: Any
    ) -> Optional[Dict[str, Any]]:
        cost = step.cost_unit
        resource = "compute"
        if step.kind is StepKind.TASK:
            resource = "api_calls"
            result_cost = getattr(result, "cost", None)
            if isinstance(result_cost, (int, float)):
                cost += float(result_cost)
        if cost <= 0:
            return None
        return {
            "step_id": step.step_id,
            "resource_type": resource,
            "quantity": 1.0,
            "unit_cost": cost,
            "total_cost": cost,
            "ts": self.clock.now_iso(),
        }

    def _sla_breached(self, execution: WorkflowExecution) -> bool:
        if execution.spec.sla_seconds is None:
            return False
        elapsed = execution.elapsed_seconds()
        if elapsed is None:
            return False
        return elapsed > execution.spec.sla_seconds

    def _record_terminal(self, execution: WorkflowExecution) -> None:
        if getattr(execution, "_ao_terminal_recorded", False):
            return
        if self.namespaces.get(execution.namespace_id) is None:
            return
        self.namespaces.record_terminal_workflow(
            execution.namespace_id,
            succeeded=execution.status is WorkflowStatus.SUCCEEDED,
            rolled_back=execution.status is WorkflowStatus.ROLLED_BACK,
            cost_entries=execution.cost_entries,
            sla_breached=self._sla_breached(execution),
        )
        execution._ao_terminal_recorded = True
