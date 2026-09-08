"""The engine hosts every declared workflow kind (issue #21 acceptance #1).

The core is kind-agnostic: task execution, agent-loop steps, multi-agent
fan-out/join, tenant provisioning and sagas are all specs of steps that run
through the same durable, namespace-scoped, saga-capable scheduler.  Handlers
for loop/fan-out/join semantics are registered by sibling phase-3 lanes on
this same seam; these tests register local deterministic handlers to prove
the engine hosts each workflow shape end to end.
"""

from __future__ import annotations

import pytest

from core.errors import UnknownNamespaceError
from core.handlers import Handler
from core.model import (
    Compensation,
    EventKind,
    Step,
    StepKind,
    StepStatus,
    WorkflowKind,
    WorkflowSpec,
    WorkflowStatus,
    spec_from_dict,
)
from core.namespaces import NamespaceRegistry
from core.runtime import Engine, provisioning_spec
from support import FakeGateway, record_compensate, record_run


def _world(engine_kwargs=None):
    reg = NamespaceRegistry()
    reg.create("acme")
    kwargs = dict(engine_kwargs or {})
    kwargs.setdefault("namespaces", reg)
    kwargs.setdefault("gateway", FakeGateway())
    return Engine(**kwargs)


def test_hosts_task_execution_kind():
    engine = _world()
    spec = WorkflowSpec(
        name="task-exec",
        kind=WorkflowKind.TASK_EXECUTION,
        steps=[
            Step(step_id="t1", kind=StepKind.TASK, name="classify"),
            Step(step_id="t2", kind=StepKind.TASK, name="summarise"),
        ],
    )
    execution = engine.run_workflow("acme", spec)
    assert execution.status is WorkflowStatus.SUCCEEDED
    started = execution.events[0]
    assert spec_from_dict(started.payload["spec"]).kind is WorkflowKind.TASK_EXECUTION


def test_hosts_agent_loop_kind():
    calls: list = []
    engine = _world()
    engine.register_handler(
        "loop-iteration",
        Handler(run=record_run("iteration", calls), compensate=record_compensate("undo", calls)),
    )
    spec = WorkflowSpec(
        name="agent-loop",
        kind=WorkflowKind.AGENT_LOOP,
        steps=[
            Step(step_id="iter-1", kind=StepKind.AGENT_LOOP, name="step", handler="loop-iteration"),
            Step(step_id="iter-2", kind=StepKind.AGENT_LOOP, name="step", handler="loop-iteration"),
        ],
    )
    execution = engine.run_workflow("acme", spec)
    assert execution.status is WorkflowStatus.SUCCEEDED
    assert calls == ["iteration", "iteration"]
    assert all(
        execution.state_for(s).status is StepStatus.SUCCEEDED for s in ("iter-1", "iter-2")
    )


def test_hosts_fan_out_join_kind():
    fan_results: list = []

    def fan_out(step, ctx):
        batch = step.args.get("batch", [])
        results = [{"item": item, "len": len(item)} for item in batch]
        fan_results.extend(results)
        return {"fanned_out": len(results), "results": results}

    def join(step, ctx):
        return {"joined": len(fan_results)}

    engine = _world()
    engine.register_handler("fan-out-handler", Handler(run=fan_out))
    engine.register_handler("join-handler", Handler(run=join))
    spec = WorkflowSpec(
        name="fan-join",
        kind=WorkflowKind.FAN_OUT_JOIN,
        steps=[
            Step(step_id="fan", kind=StepKind.FAN_OUT, handler="fan-out-handler",
                 args={"batch": ["alpha", "beta", "gamma"]}),
            Step(step_id="join", kind=StepKind.JOIN, handler="join-handler"),
        ],
    )
    execution = engine.run_workflow("acme", spec)
    assert execution.status is WorkflowStatus.SUCCEEDED
    assert len(fan_results) == 3
    # The join step's output is persisted in the transcript.
    join_output = execution.state_for("join").output
    assert join_output == {"joined": 3}


def test_hosts_tenant_provisioning_kind():
    engine = Engine()  # only system namespace
    execution = engine.provision_tenant("demo", plan="premium")
    assert execution.status is WorkflowStatus.SUCCEEDED
    namespace = engine.namespaces.require("demo")
    assert namespace.plan == "premium"
    assert namespace.retention_days == 365
    assert namespace.default_queue == "demo:default"  # per-tenant task queue
    assert list(namespace.queues) == ["default", "high-priority", "scheduled"]
    started = execution.events[0]
    assert spec_from_dict(started.payload["spec"]).kind is WorkflowKind.TENANT_PROVISIONING


def test_provisioning_saga_rolls_back_removing_the_namespace():
    calls: list = []
    engine = Engine()
    # Swap the billing step's handler for a failing one -> rollback removes the
    # namespace so the tenant does NOT exist afterwards.
    engine.register_handler("failing-billing", Handler(run=_failing_billing(calls)))
    spec = provisioning_spec("rollme", plan="standard")
    steps = list(spec.steps)
    steps = [
        Step(step_id=s.step_id, kind=s.kind, name=s.name,
             handler="failing-billing" if s.step_id == "init-billing" else s.handler,
             args=s.args, retry=s.retry, compensate=s.compensate, cost_unit=s.cost_unit)
        for s in steps
    ]
    spec = WorkflowSpec(name=spec.name, kind=spec.kind, steps=steps,
                        saga=spec.saga, sla_seconds=spec.sla_seconds, description=spec.description)
    execution = engine.run_workflow("system", spec, inputs={"tenant_id": "rollme", "plan": "standard"})
    assert execution.status is WorkflowStatus.ROLLED_BACK
    with pytest.raises(UnknownNamespaceError):
        engine.namespaces.require("rollme")


def _failing_billing(calls):
    from core.errors import StepFailure

    def _run(step, ctx):
        calls.append("billing")
        raise StepFailure("billing provider down")

    return _run


def test_hosts_saga_kind():
    calls: list = []
    engine = _world()
    engine.register_handler("reserve", Handler(run=record_run("reserve", calls),
                                               compensate=record_compensate("release", calls)))
    engine.register_handler("pay", Handler(run=record_run("pay", calls),
                                           compensate=record_compensate("refund", calls)))
    spec = WorkflowSpec(
        name="mini-saga",
        kind=WorkflowKind.SAGA,
        saga=True,
        steps=[
            Step(step_id="reserve", kind=StepKind.NOOP, handler="reserve",
                 compensate=Compensation(name="release", handler="reserve", args={})),
            Step(step_id="pay", kind=StepKind.NOOP, handler="pay",
                 compensate=Compensation(name="refund", handler="pay", args={})),
        ],
    )
    execution = engine.run_workflow("acme", spec)
    assert execution.status is WorkflowStatus.SUCCEEDED  # saga can succeed too
    compensated = [e for e in execution.events if e.kind is EventKind.STEP_COMPENSATED]
    assert compensated == []
