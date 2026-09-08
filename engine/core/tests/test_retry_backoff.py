"""Retry-with-backoff tests (non-saga steps) — issue #21 acceptance #1.

Backoff is deterministic from the RetryPolicy (no real sleeping): the delay
before retry #n is ``initial * coeff ** (n-2)`` capped at the max interval,
and it is recorded in the transcript as ``retry_scheduled`` events.
"""

from __future__ import annotations

from core.handlers import Handler
from core.model import (
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
from support import FakeClock


def test_backoff_formula():
    policy = RetryPolicy(
        max_attempts=5, initial_interval_seconds=1.0, backoff_coefficient=2.0
    )
    assert policy.delay_before_retry(1) == 0.0
    assert policy.delay_before_retry(2) == 1.0
    assert policy.delay_before_retry(3) == 2.0
    assert policy.delay_before_retry(4) == 4.0


def test_backoff_is_capped_at_max_interval():
    policy = RetryPolicy(
        max_attempts=10,
        initial_interval_seconds=1.0,
        backoff_coefficient=2.0,
        max_interval_seconds=3.0,
    )
    assert policy.delay_before_retry(2) == 1.0
    assert policy.delay_before_retry(3) == 2.0
    assert policy.delay_before_retry(4) == 3.0  # would be 4, capped
    assert policy.delay_before_retry(10) == 3.0


def _flaky_engine(calls, fail_first_n=2):
    reg = NamespaceRegistry()
    reg.create("acme")

    def flaky_run(step, ctx):
        calls.append("run")
        if len([c for c in calls if c == "run"]) <= fail_first_n:
            raise RuntimeError("transient")
        return {"ok": True}

    engine = Engine(namespaces=reg, clock=FakeClock())
    engine.register_handler("flaky", Handler(run=flaky_run))
    return engine


def test_step_retries_then_succeeds():
    calls: list = []
    engine = _flaky_engine(calls)
    spec = WorkflowSpec(
        name="flaky",
        kind=WorkflowKind.TASK_EXECUTION,
        steps=[
            Step(
                step_id="f",
                kind=StepKind.NOOP,
                handler="flaky",
                retry=RetryPolicy(
                    max_attempts=3, initial_interval_seconds=1.0, backoff_coefficient=2.0
                ),
            )
        ],
    )
    execution = engine.run_workflow("acme", spec)
    assert execution.status is WorkflowStatus.SUCCEEDED
    assert execution.state_for("f").status is StepStatus.SUCCEEDED
    assert execution.state_for("f").attempts == 3
    # Transcript: two failures, two scheduled retries with growing delays, then success.
    kinds = [e.kind for e in execution.events]
    assert kinds.count(EventKind.STEP_FAILED) == 2
    delays = [
        e.payload["delay_seconds"]
        for e in execution.events
        if e.kind is EventKind.RETRY_SCHEDULED
    ]
    assert delays == [1.0, 2.0]


def test_step_exhausting_attempts_fails_workflow():
    calls: list = []

    def always_fail(step, ctx):
        calls.append("run")
        raise RuntimeError("permanent")

    reg = NamespaceRegistry()
    reg.create("acme")
    engine = Engine(namespaces=reg, clock=FakeClock())
    engine.register_handler("bad", Handler(run=always_fail))
    spec = WorkflowSpec(
        name="always-fails",
        kind=WorkflowKind.TASK_EXECUTION,
        steps=[
            Step(
                step_id="b",
                kind=StepKind.NOOP,
                handler="bad",
                retry=RetryPolicy(max_attempts=2, initial_interval_seconds=1.0),
            )
        ],
    )
    execution = engine.run_workflow("acme", spec)
    assert execution.status is WorkflowStatus.FAILED
    assert execution.state_for("b").status is StepStatus.FAILED
    assert execution.state_for("b").attempts == 2
    assert calls == ["run", "run"]  # exactly max_attempts handler calls
    assert execution.state_for("b").last_error == "permanent"
    assert engine.namespaces.require("acme").failed_workflows == 1
