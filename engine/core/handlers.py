"""Step/compensation handler seam and built-in handlers.

A workflow step is executed by a :class:`Handler` registered on the engine
under a string key (``engine.register_handler(key, handler)``).  A handler
runs a step (``run(step, ctx)``) and, when the step carries a compensation,
undoes it (``compensate(comp, ctx)``).  Handlers are the extension seam: the
core ships the built-ins below, and sibling phase-3 lanes register the real
``agent_loop`` / ``fan_out`` / ``join`` handlers on top of the same seam.

Built-in handler keys (registered by :class:`Engine`):

* ``core.task`` — dispatch one task through the injected model gateway
  (provider-agnostic, issue #21 acceptance #4).  Returns the gateway result.
* ``core.noop`` / ``core.log`` / ``core.notify`` — trivial deterministic
  steps used to exercise the scheduler.
* ``core.provision_*`` — tenant-provisioning saga effects (create namespace,
  configure retention, initialise billing, and their removals).

A handler raises :class:`StepFailure` to record a domain failure into the
event log; any other exception is also captured (with its message) as a step
failure so the engine never dies mid-transcript.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .errors import StepFailure
from .gateway_port import GatewayRequest
from .model import Compensation, Step

# Maps a StepKind value to the built-in handler key the engine registers.
DEFAULT_HANDLERS: Mapping[str, str] = {
    "task": "core.task",
    "noop": "core.noop",
    "notify": "core.notify",
    "log": "core.log",
    "provision": "core.provision_create_namespace",
}


@dataclass
class StepContext:
    """What a handler may observe/do for one step.

    ``engine`` is the running :class:`Engine` (for services such as the
    namespace registry and clock); ``namespace_id`` / ``workflow_id`` /
    ``inputs`` scope the handler to exactly one workflow in one namespace.
    """

    namespace_id: str
    workflow_id: str
    step_id: str
    attempt: int
    inputs: Mapping[str, Any]
    engine: Any = None

    @property
    def clock(self) -> Any:
        return self.engine.clock if self.engine is not None else None


@dataclass
class Handler:
    """A registered run/compensate pair for a step kind or handler key."""

    run: Callable[[Step, StepContext], Any]
    compensate: Optional[Callable[[Compensation, StepContext], None]] = None


# --------------------------------------------------------------------------
# Built-in handlers
# --------------------------------------------------------------------------


def _task_run(step: Step, ctx: StepContext) -> Any:
    gateway = ctx.engine.gateway
    if gateway is None:
        raise StepFailure("engine has no model gateway configured")
    agent_id = step.args.get("agent_id", "")
    task_type = step.args.get("task_type", step.name or "task")
    request = GatewayRequest(
        tenant_id=ctx.namespace_id,
        agent_id=agent_id,
        task_type=task_type,
        input_=dict(ctx.inputs),
    )
    result = gateway.dispatch(request)
    if result.outcome != "success":
        raise StepFailure(f"gateway outcome {result.outcome!r}")
    return result


def _noop_run(step: Step, ctx: StepContext) -> Any:
    return None


def _notify_run(step: Step, ctx: StepContext) -> Any:
    return {"notified": step.name or step.step_id}


def _log_run(step: Step, ctx: StepContext) -> Any:
    return {"logged": step.name or step.step_id}


def _provision_create_run(step: Step, ctx: StepContext) -> Any:
    tenant_id = step.args.get("tenant_id", ctx.inputs.get("tenant_id"))
    plan = step.args.get("plan", ctx.inputs.get("plan", "standard"))
    if not tenant_id:
        raise StepFailure("provision step requires a tenant_id")
    registry = ctx.engine.namespaces
    if registry.get(tenant_id) is not None:
        raise StepFailure(f"namespace already exists: {tenant_id}")
    registry.create(namespace_id=tenant_id, plan=plan, created_at=ctx.clock.now_iso())
    return {"created": tenant_id, "plan": plan}


def _provision_configure_run(step: Step, ctx: StepContext) -> Any:
    tenant_id = step.args.get("tenant_id", ctx.inputs.get("tenant_id"))
    registry = ctx.engine.namespaces
    namespace = registry.require(tenant_id)
    retention = int(step.args.get("retention_days", namespace.retention_days))
    namespace.retention_days = retention
    return {"configured": tenant_id, "retention_days": retention}


def _provision_billing_run(step: Step, ctx: StepContext) -> Any:
    tenant_id = step.args.get("tenant_id", ctx.inputs.get("tenant_id"))
    ctx.engine.namespaces.require(tenant_id)  # billing only for a live namespace
    return {"billing": tenant_id}


def _provision_remove_namespace_run(comp: Compensation, ctx: StepContext) -> None:
    tenant_id = comp.args.get("tenant_id")
    registry = ctx.engine.namespaces
    if registry.get(tenant_id) is not None:
        registry.remove(tenant_id)


def _provision_remove_billing_run(comp: Compensation, ctx: StepContext) -> None:
    # Billing removal is a ledger effect keyed to the namespace; removing the
    # namespace already drops its ledger, so this compensation is a no-op that
    # exists to keep the saga's reverse-order compensation observable.
    return None


BUILTIN_HANDLERS: Mapping[str, Handler] = {
    "core.task": Handler(run=_task_run),
    "core.noop": Handler(run=_noop_run),
    "core.notify": Handler(run=_notify_run),
    "core.log": Handler(run=_log_run),
    "core.provision_create_namespace": Handler(
        run=_provision_create_run, compensate=_provision_remove_namespace_run
    ),
    "core.provision_configure": Handler(run=_provision_configure_run),
    "core.provision_billing": Handler(run=_provision_billing_run, compensate=_provision_remove_billing_run),
}
