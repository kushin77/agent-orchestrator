"""Provider-agnostic gateway seam + per-workflow cost/SLA tracking tests.

Acceptance #4 (provider-agnostic) is enforced structurally too: no engine-core
source file may name a concrete provider, and a task step dispatches through
the injected gateway port — never a provider SDK directly.
"""

from __future__ import annotations

import os

import pytest

from core.gateway_port import NullGateway
from core.handlers import Handler
from core.model import (
    Step,
    StepKind,
    StepStatus,
    WorkflowKind,
    WorkflowSpec,
    WorkflowStatus,
)
from core.namespaces import NamespaceRegistry
from core.runtime import Engine
from support import FakeClock, FakeGateway, slow_run

_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FORBIDDEN_PROVIDERS = ("anthropic", "deepseek", "openai", "gemini", "ollama")


def test_engine_core_never_imports_a_provider():
    """Structural guard: no provider name appears in any engine/core source."""
    for name in sorted(os.listdir(_CORE_DIR)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(_CORE_DIR, name)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for provider in _FORBIDDEN_PROVIDERS:
            assert provider not in text.lower(), f"{name} mentions provider {provider}"


def test_task_step_dispatches_through_injected_gateway_only():
    calls: list = []
    gateway = FakeGateway(calls=calls)
    reg = NamespaceRegistry()
    reg.create("acme")
    engine = Engine(namespaces=reg, gateway=gateway)
    spec = WorkflowSpec(
        name="one-task",
        kind=WorkflowKind.TASK_EXECUTION,
        steps=[
            Step(step_id="t", kind=StepKind.TASK, handler="core.task",
                 args={"agent_id": "worker", "task_type": "classify"})
        ],
    )
    execution = engine.run_workflow("acme", spec, inputs={"text": "hello"})
    assert execution.status is WorkflowStatus.SUCCEEDED
    assert calls == [("acme", "worker", "classify", {"text": "hello"})]
    # The step output persisted is the gateway content (typed output).
    assert execution.state_for("t").output == {"echo": "classify"}


def test_task_step_without_gateway_fails_closed():
    reg = NamespaceRegistry()
    reg.create("acme")
    engine = Engine(namespaces=reg)  # no gateway -> NullGateway behaviour
    engine.gateway = NullGateway()
    spec = WorkflowSpec(
        name="no-gateway",
        steps=[Step(step_id="t", kind=StepKind.TASK, handler="core.task",
                    args={"agent_id": "w", "task_type": "x"})],
    )
    execution = engine.run_workflow("acme", spec)
    assert execution.status is WorkflowStatus.FAILED
    assert execution.state_for("t").status is StepStatus.FAILED


def test_gateway_outcome_not_success_fails_step():
    reg = NamespaceRegistry()
    reg.create("acme")
    engine = Engine(namespaces=reg, gateway=FakeGateway(outcome="cannot_assess"))
    spec = WorkflowSpec(
        name="bad-outcome",
        steps=[Step(step_id="t", kind=StepKind.TASK, handler="core.task",
                    args={"agent_id": "w", "task_type": "x"})],
    )
    execution = engine.run_workflow("acme", spec)
    assert execution.status is WorkflowStatus.FAILED
    assert "outcome" in (execution.state_for("t").last_error or "")


def test_per_workflow_cost_is_recorded_and_aggregated():
    gateway = FakeGateway(cost=0.25)
    reg = NamespaceRegistry()
    reg.create("acme")
    engine = Engine(namespaces=reg, gateway=gateway, clock=FakeClock())
    spec = WorkflowSpec(
        name="costly",
        steps=[
            Step(step_id="t1", kind=StepKind.TASK, handler="core.task",
                 args={"agent_id": "w", "task_type": "a"}),
            Step(step_id="t2", kind=StepKind.TASK, handler="core.task",
                 args={"agent_id": "w", "task_type": "b"}),
            Step(step_id="c1", kind=StepKind.NOOP, cost_unit=1.5),
        ],
    )
    execution = engine.run_workflow("acme", spec)
    assert execution.status is WorkflowStatus.SUCCEEDED
    assert len(execution.cost_entries) == 3
    report = engine.namespaces.cost_report("acme")
    assert report["total_cost"] == pytest.approx(0.25 + 0.25 + 1.5)
    assert report["breakdown"]["api_calls"] == pytest.approx(0.5)
    assert report["breakdown"]["compute"] == pytest.approx(1.5)
    assert report["workflow_count"] == 1


def test_sla_is_tracked_per_workflow():
    reg = NamespaceRegistry()
    reg.create("acme")
    engine = Engine(namespaces=reg, gateway=FakeGateway(), clock=FakeClock())
    engine.register_handler("slow", Handler(run=slow_run(5.0)))

    slow_spec = WorkflowSpec(
        name="slow-wf",
        sla_seconds=1.0,  # the step advances the clock 5s -> breach
        steps=[Step(step_id="s", kind=StepKind.NOOP, handler="slow")],
    )
    fast_spec = WorkflowSpec(
        name="fast-wf",
        sla_seconds=60.0,
        steps=[Step(step_id="f", kind=StepKind.NOOP)],
    )
    slow_wf = engine.run_workflow("acme", slow_spec)
    fast_wf = engine.run_workflow("acme", fast_spec)
    assert slow_wf.status is WorkflowStatus.SUCCEEDED
    assert fast_wf.status is WorkflowStatus.SUCCEEDED
    assert slow_wf.elapsed_seconds() == pytest.approx(5.0)
    sla = engine.namespaces.sla_report("acme")
    assert sla["sla_breached"] == 1
    assert sla["sla_met"] == 1
