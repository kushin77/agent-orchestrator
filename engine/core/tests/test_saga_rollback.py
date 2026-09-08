"""Saga/compensation tests: reverse-order rollback, no-retry-in-saga, failure paths.

Adapted from the Temporal saga pattern (``shared-temporal/patterns/
advanced_patterns.go``): a saga registers a compensation for each step only
*after* that step succeeds, never retries a step, and on a failure runs the
registered compensations in reverse completion order before ending
ROLLED_BACK.  A saga whose compensation itself fails ends FAILED with the
failures recorded.
"""

from __future__ import annotations

from core.errors import StepFailure
from core.handlers import Handler
from core.model import (
    Compensation,
    EventKind,
    RetryPolicy,
    Step,
    StepKind,
    StepStatus,
    WorkflowKind,
    WorkflowSpec,
    WorkflowStatus,
)
from core.namespaces import NamespaceRegistry
from core.runtime import Engine
from support import failing_run, record_compensate, record_run


def _saga_engine(calls, ship_fails=True):
    reg = NamespaceRegistry()
    reg.create("acme")
    engine = Engine(namespaces=reg)
    engine.register_handler("reserve", Handler(run=record_run("reserve", calls),
                                               compensate=record_compensate("release", calls)))
    engine.register_handler("pay", Handler(run=record_run("pay", calls),
                                           compensate=record_compensate("refund", calls)))
    engine.register_handler(
        "ship",
        Handler(run=failing_run("ship", calls, "no stock") if ship_fails
                else record_run("ship", calls)),
    )
    return engine


def _order_saga_spec() -> WorkflowSpec:
    """reserve -> pay -> ship; each step carries a compensation."""
    steps = [
        Step(step_id="reserve", kind=StepKind.NOOP, handler="reserve",
             compensate=Compensation(name="release", handler="reserve", args={})),
        Step(step_id="pay", kind=StepKind.NOOP, handler="pay",
             compensate=Compensation(name="refund", handler="pay", args={})),
        Step(step_id="ship", kind=StepKind.NOOP, handler="ship"),
    ]
    return WorkflowSpec(name="order-saga", kind=WorkflowKind.SAGA, steps=steps, saga=True)


def test_saga_failure_compensates_in_reverse_order():
    calls: list = []
    engine = _saga_engine(calls)
    execution = engine.run_workflow("acme", _order_saga_spec())
    # reserve, pay, ship(attempt) then compensations in reverse: refund, release.
    assert calls == ["reserve", "pay", "ship", "refund", "release"]
    assert execution.status is WorkflowStatus.ROLLED_BACK
    assert execution.state_for("reserve").status is StepStatus.COMPENSATED
    assert execution.state_for("pay").status is StepStatus.COMPENSATED
    assert execution.state_for("ship").status is StepStatus.FAILED
    assert execution.events[-1].kind is EventKind.WORKFLOW_ROLLED_BACK
    assert engine.namespaces.require("acme").rolled_back_workflows == 1


def test_saga_success_runs_no_compensations():
    calls: list = []
    engine = _saga_engine(calls, ship_fails=False)
    execution = engine.run_workflow("acme", _order_saga_spec())
    assert calls == ["reserve", "pay", "ship"]
    assert execution.status is WorkflowStatus.SUCCEEDED
    compensated_events = [e for e in execution.events
                          if e.kind in (EventKind.COMPENSATION_STARTED,
                                        EventKind.STEP_COMPENSATED)]
    assert compensated_events == []
    assert engine.namespaces.require("acme").completed_workflows == 1


def test_saga_never_retries_a_step_even_with_retry_policy():
    # A saga step carrying a retry policy still runs exactly once (the Temporal
    # saga pattern disables retries inside a saga).
    calls: list = []
    reg = NamespaceRegistry()
    reg.create("acme")
    engine = Engine(namespaces=reg)
    engine.register_handler("only", Handler(run=failing_run("only", calls, "always fails")))
    spec = WorkflowSpec(
        name="saga-one-shot",
        kind=WorkflowKind.SAGA,
        saga=True,
        steps=[
            Step(
                step_id="only",
                kind=StepKind.NOOP,
                handler="only",
                retry=RetryPolicy(max_attempts=5, initial_interval_seconds=1.0),
            )
        ],
    )
    execution = engine.run_workflow("acme", spec)
    assert calls == ["only"]  # one attempt, no retries
    assert execution.status is WorkflowStatus.ROLLED_BACK  # nothing to compensate
    retried = [e for e in execution.events if e.kind is EventKind.RETRY_SCHEDULED]
    assert retried == []
    assert execution.state_for("only").attempts == 1


def test_compensation_failure_ends_saga_failed_and_records_it():
    calls: list = []

    def failing_compensate(name):
        def _compensate(comp, ctx):
            calls.append(name)
            raise StepFailure("could not undo")

        return _compensate

    reg = NamespaceRegistry()
    reg.create("acme")
    engine = Engine(namespaces=reg)
    engine.register_handler("a", Handler(run=record_run("a", calls),
                                         compensate=failing_compensate("undo-a")))
    engine.register_handler("b", Handler(run=record_run("b", calls),
                                         compensate=failing_compensate("undo-b")))
    engine.register_handler("c", Handler(run=failing_run("c", calls, "boom")))
    spec = WorkflowSpec(
        name="comp-fail-saga",
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
    execution = engine.run_workflow("acme", spec)
    # b failed then c failed -> compensations attempted in reverse: undo-b, undo-a.
    assert calls == ["a", "b", "c", "undo-b", "undo-a"]
    assert execution.status is WorkflowStatus.FAILED
    assert len(execution.compensation_failures) == 2
    assert execution.events[-1].kind is EventKind.WORKFLOW_FAILED
    assert engine.namespaces.require("acme").failed_workflows == 1


def test_compensation_failure_still_runs_remaining_compensations():
    calls: list = []

    def failing_b_compensate(comp, ctx):
        calls.append("undo-b")
        raise StepFailure("could not undo b")

    reg = NamespaceRegistry()
    reg.create("acme")
    engine = Engine(namespaces=reg)
    engine.register_handler("a", Handler(run=record_run("a", calls),
                                         compensate=record_compensate("undo-a", calls)))
    engine.register_handler("b", Handler(run=record_run("b", calls),
                                         compensate=failing_b_compensate))
    engine.register_handler("c", Handler(run=failing_run("c", calls)))
    spec = WorkflowSpec(
        name="partial-undo",
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
    execution = engine.run_workflow("acme", spec)
    assert calls == ["a", "b", "c", "undo-b", "undo-a"]
    assert execution.status is WorkflowStatus.FAILED
    assert len(execution.compensation_failures) == 1
    # a's compensation still ran even though b's failed.
    assert execution.state_for("a").status is StepStatus.COMPENSATED
    assert execution.state_for("b").status is StepStatus.SUCCEEDED  # undo failed


def test_non_saga_failure_does_not_compensate():
    calls: list = []
    reg = NamespaceRegistry()
    reg.create("acme")
    engine = Engine(namespaces=reg)
    engine.register_handler("a", Handler(run=record_run("a", calls),
                                         compensate=record_compensate("undo-a", calls)))
    engine.register_handler("b", Handler(run=failing_run("b", calls)))
    spec = WorkflowSpec(
        name="not-a-saga",
        kind=WorkflowKind.TASK_EXECUTION,
        saga=False,
        steps=[
            Step(step_id="a", kind=StepKind.NOOP, handler="a",
                 compensate=Compensation(name="undo-a", handler="a", args={})),
            Step(step_id="b", kind=StepKind.NOOP, handler="b"),
        ],
    )
    execution = engine.run_workflow("acme", spec)
    assert calls == ["a", "b"]
    assert execution.status is WorkflowStatus.FAILED
    assert execution.state_for("a").status is StepStatus.SUCCEEDED  # never compensated
    comp_events = [e for e in execution.events
                   if e.kind in (EventKind.COMPENSATION_STARTED, EventKind.STEP_COMPENSATED)]
    assert comp_events == []
