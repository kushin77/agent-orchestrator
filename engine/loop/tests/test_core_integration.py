"""Integration with engine.core: the loop as a durable, resumable step.

engine.core (issue #21) declares the AGENT_LOOP step kind and the handler
seam; engine/loop registers ``loop.agent_loop`` on the seam.  These tests
prove the loop runs *as a durable workflow step*, that an interrupted
workflow resumes over the same JSONL event log to a byte-identical decision
recorded in the step output, and that a failing loop marks the step FAILED
(never a false pass at the engine layer).
"""

from __future__ import annotations

import os
import tempfile

from engine.core.events import FileJsonlEventStore, InMemoryEventStore
from engine.core.model import Step, StepKind, WorkflowKind, WorkflowSpec
from engine.core.namespaces import NamespaceRegistry
from engine.core.runtime import Engine
from engine.loop.core_adapter import HANDLER_KEY, register_agent_loop_handler
from loop_support import (
    TENANT,
    FakeClock,
    ToolThenFinalActor,
    make_policy,
    make_profile,
    make_tools,
)

INPUTS = {"prompt": "resolve ticket T-1024"}


def _spec() -> WorkflowSpec:
    return WorkflowSpec(
        name="agent-run",
        kind=WorkflowKind.AGENT_LOOP,
        steps=[
            Step(step_id="s0", kind=StepKind.NOOP),
            Step(
                step_id="loop-1",
                kind=StepKind.AGENT_LOOP,
                handler=HANDLER_KEY,
                args={"task_input": dict(INPUTS)},
            ),
        ],
    )


def _registry(clock) -> NamespaceRegistry:
    registry = NamespaceRegistry()
    registry.create(namespace_id=TENANT, created_at=clock.now_iso())
    return registry


def _engine(store, registry, clock, *, policy=None) -> Engine:
    engine = Engine(store=store, namespaces=registry, clock=clock)
    register_agent_loop_handler(
        engine,
        actor=ToolThenFinalActor(),
        tools=make_tools(),
        profile=make_profile(),
        policy=policy,
    )
    return engine


def _loop_output(execution):
    for event in execution.events:
        if event.kind.value == "step_succeeded" and event.payload.get("step_id") == "loop-1":
            return event.payload.get("output")
    return None


def test_loop_runs_as_a_durable_workflow_step():
    clock = FakeClock()
    registry = _registry(clock)
    engine = _engine(InMemoryEventStore(), registry, clock)
    workflow = engine.run_workflow(
        TENANT, _spec(), inputs=dict(INPUTS), workflow_id="wf-a"
    )
    assert workflow.status.value == "succeeded"
    output = _loop_output(workflow)
    assert output is not None
    assert output["outcome"] == "succeeded"
    assert output["confidence"] > 0.9
    assert output["agent_id"] == "support-bot"
    assert len(output["trace"]) == 2


def test_interrupted_and_resumed_workflow_produces_identical_decision():
    clock = FakeClock()
    registry = _registry(clock)
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "events.jsonl")
        store_a = FileJsonlEventStore(log)
        engine_a = _engine(store_a, registry, clock)
        started = engine_a.start_workflow(
            TENANT, _spec(), inputs=dict(INPUTS), workflow_id="wf-resume"
        )
        # Interrupt after the first (noop) step: the loop step has not run yet.
        engine_a.advance(started, max_handlers=1)
        assert started.status.value == "running"
        assert _loop_output(started) is None

        # A fresh engine over the SAME persisted log resumes mid-flight.
        engine_b = _engine(FileJsonlEventStore(log), registry, clock)
        resumed = engine_b.resume(TENANT, "wf-resume")
        engine_b.advance(resumed)
        assert resumed.status.value == "succeeded"

        # Control: an uninterrupted run over an in-memory store, same ids.
        control = _engine(InMemoryEventStore(), _registry(clock), clock).run_workflow(
            TENANT, _spec(), inputs=dict(INPUTS), workflow_id="wf-resume"
        )
        assert _loop_output(resumed) == _loop_output(control)


def test_completed_step_is_never_reexecuted_after_resume():
    clock = FakeClock()
    registry = _registry(clock)
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "events.jsonl")
        engine_a = _engine(FileJsonlEventStore(log), registry, clock)
        started = engine_a.start_workflow(
            TENANT, _spec(), inputs=dict(INPUTS), workflow_id="wf-once"
        )
        engine_a.advance(started, max_handlers=1)
        engine_b = _engine(FileJsonlEventStore(log), registry, clock)
        resumed = engine_b.resume(TENANT, "wf-once")
        engine_b.advance(resumed)
        before = len(resumed.events)
        engine_b.advance(resumed)  # already terminal: no new events
        assert len(resumed.events) == before
        step_starts = [
            e for e in resumed.events
            if e.kind.value == "step_started" and e.payload.get("step_id") == "loop-1"
        ]
        assert len(step_starts) == 1


def test_failing_loop_marks_the_step_failed():
    clock = FakeClock()
    registry = _registry(clock)
    # A tiny token budget makes even the first thought bust the cap -> the
    # loop ends BUDGET_EXHAUSTED and the handler raises StepFailure.
    policy = make_policy(token_budget=1, human_in_loop=False)
    engine = _engine(InMemoryEventStore(), registry, clock, policy=policy)
    workflow = engine.run_workflow(
        TENANT, _spec(), inputs=dict(INPUTS), workflow_id="wf-fail"
    )
    assert workflow.status.value == "failed"
    failures = [
        e for e in workflow.events
        if e.kind.value == "step_failed" and e.payload.get("step_id") == "loop-1"
    ]
    assert failures  # the loop failure is recorded, never a silent pass
