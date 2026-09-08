"""Namespace isolation tests (negative: no cross-namespace state).

Tenant namespaces are the engine's isolation boundary (namespace-per-tenant,
``multi_tenancy.go``).  A workflow, resume, or cost/SLA lookup is always
scoped to exactly one namespace and never falls back across a boundary.
"""

from __future__ import annotations

import pytest

from core.errors import (
    NamespaceExistsError,
    UnknownNamespaceError,
    UnknownWorkflowError,
)
from core.model import Step, StepKind, WorkflowSpec
from core.namespaces import NamespaceRegistry
from core.runtime import Engine
from support import FakeClock, FakeGateway

SPEC = WorkflowSpec(
    name="ns-task",
    steps=[Step(step_id="t", kind=StepKind.TASK, handler="core.task",
                args={"agent_id": "worker", "task_type": "do-thing"})],
)


def _two_tenant_world():
    reg = NamespaceRegistry()
    reg.create("tenant-a", plan="standard")
    reg.create("tenant-b", plan="free")
    engine = Engine(namespaces=reg, gateway=FakeGateway())
    return engine


def test_workflows_of_one_namespace_are_invisible_to_another():
    engine = _two_tenant_world()
    wf_a = engine.run_workflow("tenant-a", SPEC, inputs={"who": "a"})
    wf_b = engine.run_workflow("tenant-b", SPEC, inputs={"who": "b"})
    # Each tenant sees only its own workflow id.
    assert engine.get_workflow("tenant-a", wf_a.workflow_id) is not None
    assert engine.get_workflow("tenant-b", wf_b.workflow_id) is not None
    # Negative: tenant-b cannot see or resume tenant-a's workflow.
    with pytest.raises(UnknownWorkflowError):
        engine.get_workflow("tenant-b", wf_a.workflow_id)
    with pytest.raises(UnknownWorkflowError):
        engine.resume("tenant-b", wf_a.workflow_id)


def test_resume_unknown_namespace_fails_closed():
    engine = _two_tenant_world()
    wf_a = engine.run_workflow("tenant-a", SPEC)
    # The workflow exists — but only under its own namespace.
    with pytest.raises(UnknownNamespaceError):
        engine.resume("does-not-exist", wf_a.workflow_id)


def test_store_reads_are_namespace_scoped_even_when_ids_overlap():
    reg = NamespaceRegistry()
    reg.create("t1")
    reg.create("t2")
    engine = Engine(namespaces=reg, gateway=FakeGateway())
    # Same workflow id in two namespaces stays distinct.
    wf1 = engine.run_workflow("t1", SPEC, workflow_id="same-id")
    wf2 = engine.run_workflow("t2", SPEC, workflow_id="same-id")
    assert wf1.workflow_id == wf2.workflow_id == "same-id"
    assert engine.store.events("t1", "same-id") != engine.store.events("t2", "same-id")
    assert engine.get_workflow("t1", "same-id").namespace_id == "t1"
    assert engine.get_workflow("t2", "same-id").namespace_id == "t2"


def test_provisioning_isolation_and_duplicate_refusal():
    engine = Engine()  # only system namespace
    engine.provision_tenant("acme", plan="standard")
    assert engine.namespaces.require("acme").plan == "standard"
    with pytest.raises(NamespaceExistsError):
        engine.namespaces.create("acme")


def test_unknown_namespace_required_nowhere_falls_back():
    reg = NamespaceRegistry()
    with pytest.raises(UnknownNamespaceError):
        reg.require("ghost")


def test_engine_start_requires_existing_namespace():
    engine = Engine(namespaces=NamespaceRegistry())
    with pytest.raises(UnknownNamespaceError):
        engine.start_workflow("not-provisioned", SPEC)


def test_cost_report_is_per_namespace():
    engine = _two_tenant_world()
    engine.run_workflow("tenant-a", SPEC)  # 1 x cost 0.25 in a
    engine.run_workflow("tenant-a", SPEC)  # another in a
    engine.run_workflow("tenant-b", SPEC)  # 1 x cost 0.25 in b
    report_a = engine.namespaces.cost_report("tenant-a")
    report_b = engine.namespaces.cost_report("tenant-b")
    assert report_a["total_cost"] == pytest.approx(0.5)
    assert report_b["total_cost"] == pytest.approx(0.25)
    assert report_a["breakdown"] != report_b["breakdown"]
    assert report_b["breakdown"]["api_calls"] == pytest.approx(0.25)


def test_sla_report_is_per_namespace():
    from support import slow_run
    from core.handlers import Handler

    reg = NamespaceRegistry()
    reg.create("t1")
    reg.create("t2")
    engine = Engine(namespaces=reg, gateway=FakeGateway(), clock=FakeClock())
    slow_spec = WorkflowSpec(
        name="slow",
        steps=[Step(step_id="s", kind=StepKind.NOOP, handler="slow")],
        sla_seconds=1.0,
    )
    engine.register_handler("slow", Handler(run=slow_run(5.0)))
    engine.run_workflow("t1", slow_spec)  # breaches the 1s SLA
    engine.run_workflow("t2", slow_spec)  # also breaches in t2
    # A fast workflow in t1 only.
    fast = WorkflowSpec(
        name="fast",
        steps=[Step(step_id="f", kind=StepKind.NOOP)],
        sla_seconds=60.0,
    )
    engine.run_workflow("t1", fast)
    assert engine.namespaces.sla_report("t1")["sla_breached"] == 1
    assert engine.namespaces.sla_report("t1")["sla_met"] == 1
    assert engine.namespaces.sla_report("t2")["sla_breached"] == 1
    assert engine.namespaces.sla_report("t2")["sla_met"] == 0
