"""Durable persistence tests: resume mid-flight, deterministic replay, no re-execution.

A workflow's only state is its append-only event log.  Simulating an
interruption = advancing a bounded number of handlers then dropping the
engine; resuming = opening a fresh engine over the same JSONL log and calling
``resume()`` — the projection is rebuilt from the log and the scheduler
continues exactly where it stopped.  Completed steps are never re-executed.
"""

from __future__ import annotations

import pytest

from core.errors import PersistenceError, UnknownWorkflowError
from core.events import FileJsonlEventStore
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
)
from core.namespaces import NamespaceRegistry
from core.runtime import Engine
from core.workflow import WorkflowExecution
from support import FakeClock, FakeGateway, failing_run, record_compensate, record_run


def _task_spec() -> WorkflowSpec:
    return WorkflowSpec(
        name="three-tasks",
        kind=WorkflowKind.TASK_EXECUTION,
        steps=[
            Step(step_id="s1", kind=StepKind.TASK, name="one", handler="core.task"),
            Step(step_id="s2", kind=StepKind.TASK, name="two", handler="core.task"),
            Step(step_id="s3", kind=StepKind.TASK, name="three", handler="core.task"),
        ],
    )


def _world(store, clock, gateway):
    reg = NamespaceRegistry()
    reg.create("acme")
    return Engine(store=store, namespaces=reg, gateway=gateway, clock=clock)


def test_resume_mid_flight_completes_without_reexecution(tmp_path):
    path = str(tmp_path / "log.jsonl")
    gateway = FakeGateway()
    clock = FakeClock()
    engine1 = _world(FileJsonlEventStore(path), clock, gateway)

    execution = engine1.start_workflow("acme", _task_spec(), workflow_id="durable-1")
    engine1.advance(execution, max_handlers=1)  # s1 only, then the "process dies"
    assert execution.status is WorkflowStatus.RUNNING
    assert len(gateway.calls) == 1

    # A brand-new engine over the same log resumes to completion.
    engine2 = _world(FileJsonlEventStore(path), clock, gateway)
    resumed = engine2.resume("acme", "durable-1")
    assert resumed.status is WorkflowStatus.RUNNING
    assert resumed.resumed_count == 1
    engine2.advance(resumed)
    assert resumed.status is WorkflowStatus.SUCCEEDED
    # Each task ran exactly once across both engines (no re-execution of s1).
    assert len(gateway.calls) == 3
    for step_id in ("s1", "s2", "s3"):
        assert resumed.state_for(step_id).status is StepStatus.SUCCEEDED


def test_resume_replay_matches_live_and_is_deterministic(tmp_path):
    """An interrupted+resumed run and an uninterrupted control run produce the
    exact same event log (modulo the resume marker) — deterministic replay."""
    path = str(tmp_path / "log.jsonl")
    clock = FakeClock()  # fixed: both runs share identical timestamps

    # Control: uninterrupted run, in-memory store.
    control_gateway = FakeGateway()
    control = Engine(store=FileJsonlEventStore(str(tmp_path / "control.jsonl")),
                     namespaces=_reg_with("acme"),
                     gateway=control_gateway, clock=clock)
    control_events = control.run_workflow("acme", _task_spec(), workflow_id="control").events

    # Interrupted + resumed run over a JSONL file.
    gateway = FakeGateway()
    engine1 = _world(FileJsonlEventStore(path), clock, gateway)
    execution = engine1.start_workflow("acme", _task_spec(), workflow_id="durable-2")
    engine1.advance(execution, max_handlers=1)  # s1
    engine1.advance(execution, max_handlers=1)  # s2
    engine2 = _world(FileJsonlEventStore(path), clock, gateway)
    resumed = engine2.resume("acme", "durable-2")
    engine2.advance(resumed)  # s3 -> terminal

    def normalized(records):
        return [
            (e.kind, e.payload)
            for e in records
            if e.kind is not EventKind.WORKFLOW_RESUMED
        ]

    assert normalized(resumed.events) == normalized(control_events)
    assert len(resumed.events) == len(control_events) + 1  # only the resume marker


def _reg_with(ns):
    reg = NamespaceRegistry()
    reg.create(ns)
    return reg


def test_resume_mid_rollback_continues_compensations(tmp_path):
    """A crash during saga compensation resumes and finishes the remaining
    compensations in reverse order without re-running completed ones."""
    path = str(tmp_path / "rollback.jsonl")
    calls: list = []
    clock = FakeClock()
    spec = WorkflowSpec(
        name="saga-resume",
        kind=WorkflowKind.SAGA,
        saga=True,
        steps=[
            Step(step_id="a", kind=StepKind.NOOP, handler="a",
                 compensate=Compensation(name="undo-a", handler="a", args={})),
            Step(step_id="b", kind=StepKind.NOOP, handler="b",
                 compensate=Compensation(name="undo-b", handler="b", args={})),
            Step(step_id="c", kind=StepKind.NOOP, handler="c"),
        ],
    )

    def _handler_registry(engine):
        engine.register_handler("a", Handler(run=record_run("a", calls),
                                             compensate=record_compensate("undo-a", calls)))
        engine.register_handler("b", Handler(run=record_run("b", calls),
                                             compensate=record_compensate("undo-b", calls)))
        engine.register_handler("c", Handler(run=failing_run("c", calls)))

    engine1 = _world(FileJsonlEventStore(path), clock, None)
    _handler_registry(engine1)
    execution = engine1.start_workflow("acme", spec, workflow_id="saga-resume-1")
    engine1.advance(execution, max_handlers=3)  # a, b, c(attempt -> fails, rollback begins)
    engine1.advance(execution, max_handlers=1)  # undo-b; crash before undo-a
    assert calls == ["a", "b", "c", "undo-b"]
    assert execution.status is WorkflowStatus.RUNNING

    engine2 = _world(FileJsonlEventStore(path), clock, None)
    _handler_registry(engine2)
    resumed = engine2.resume("acme", "saga-resume-1")
    engine2.advance(resumed)
    assert calls == ["a", "b", "c", "undo-b", "undo-a"]  # undo-a ran exactly once
    assert resumed.status is WorkflowStatus.ROLLED_BACK
    assert resumed.state_for("a").status is StepStatus.COMPENSATED
    assert resumed.state_for("b").status is StepStatus.COMPENSATED
    assert resumed.state_for("c").status is StepStatus.FAILED


def test_pure_replay_projection_equals_resumed_live(tmp_path):
    path = str(tmp_path / "log.jsonl")
    clock = FakeClock()
    gateway = FakeGateway()
    engine1 = _world(FileJsonlEventStore(path), clock, gateway)
    execution = engine1.start_workflow("acme", _task_spec(), workflow_id="replay-1")
    engine1.advance(execution, max_handlers=2)

    store = FileJsonlEventStore(path)
    records = store.events("acme", "replay-1")
    projection = WorkflowExecution.from_events(records)
    assert projection.status is WorkflowStatus.RUNNING
    assert projection.state_for("s1").status is StepStatus.SUCCEEDED
    assert projection.state_for("s2").status is StepStatus.SUCCEEDED
    assert projection.state_for("s3").status is StepStatus.PENDING


def test_file_store_refuses_corrupt_line(tmp_path):
    path = str(tmp_path / "bad.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"seq": 1, "this is": "not a valid record"\n')
    with pytest.raises(PersistenceError):
        FileJsonlEventStore(path)


def test_file_store_ignores_workflows_of_other_namespaces(tmp_path):
    path = str(tmp_path / "log.jsonl")
    clock = FakeClock()
    gateway = FakeGateway()
    engine1 = _world(FileJsonlEventStore(path), clock, gateway)
    execution = engine1.run_workflow("acme", _task_spec(), workflow_id="only-in-acme")
    assert execution.status is WorkflowStatus.SUCCEEDED

    # The raw file contains the records, but a store read scoped to another
    # namespace sees none of them.
    store = FileJsonlEventStore(path)
    assert store.events("acme", "only-in-acme")
    assert store.events("other", "only-in-acme") == []

    reg = NamespaceRegistry()
    reg.create("acme")
    reg.create("other")
    engine2 = Engine(store=FileJsonlEventStore(path), namespaces=reg,
                     gateway=gateway, clock=clock)
    with pytest.raises(UnknownWorkflowError):
        engine2.resume("other", "only-in-acme")
