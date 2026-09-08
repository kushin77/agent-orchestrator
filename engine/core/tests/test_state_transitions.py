"""State-machine transition tests: legal moves, illegal moves, fail-closed start."""

from __future__ import annotations

import pytest

from core.errors import (
    InvalidTransitionError,
    NamespaceExistsError,
    PersistenceError,
    UnknownNamespaceError,
    UnknownStepHandlerError,
    WorkflowValidationError,
)
from core.machine import legal_moves, next_status
from core.model import EventKind, Step, StepKind, WorkflowSpec, WorkflowStatus
from core.namespaces import NamespaceRegistry
from core.runtime import Engine
from core.workflow import WorkflowExecution


def _spec() -> WorkflowSpec:
    return WorkflowSpec(
        name="two-noops",
        steps=[
            Step(step_id="a", kind=StepKind.NOOP),
            Step(step_id="b", kind=StepKind.NOOP),
        ],
    )


def _engine() -> Engine:
    reg = NamespaceRegistry()
    reg.create("acme")
    return Engine(namespaces=reg)


# --------------------------------------------------------------------------
# machine.transition — legal moves
# --------------------------------------------------------------------------


def test_legal_workflow_moves():
    assert next_status(WorkflowStatus.PENDING, EventKind.WORKFLOW_STARTED) is WorkflowStatus.RUNNING
    assert next_status(WorkflowStatus.RUNNING, EventKind.WORKFLOW_COMPLETED) is WorkflowStatus.SUCCEEDED
    assert next_status(WorkflowStatus.RUNNING, EventKind.WORKFLOW_FAILED) is WorkflowStatus.FAILED
    assert (
        next_status(WorkflowStatus.RUNNING, EventKind.WORKFLOW_ROLLED_BACK)
        is WorkflowStatus.ROLLED_BACK
    )


def test_legal_moves_exposure_is_sorted_and_complete():
    moves = legal_moves(WorkflowStatus.PENDING)
    assert moves == ((EventKind.WORKFLOW_STARTED, WorkflowStatus.RUNNING),)
    running = dict(legal_moves(WorkflowStatus.RUNNING))
    assert set(running) == {
        EventKind.WORKFLOW_COMPLETED,
        EventKind.WORKFLOW_FAILED,
        EventKind.WORKFLOW_ROLLED_BACK,
    }


# --------------------------------------------------------------------------
# machine.transition — illegal moves (negative)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,event",
    [
        (WorkflowStatus.PENDING, EventKind.WORKFLOW_COMPLETED),
        (WorkflowStatus.PENDING, EventKind.WORKFLOW_FAILED),
        (WorkflowStatus.RUNNING, EventKind.WORKFLOW_STARTED),
        (WorkflowStatus.SUCCEEDED, EventKind.WORKFLOW_STARTED),
        (WorkflowStatus.SUCCEEDED, EventKind.WORKFLOW_COMPLETED),
        (WorkflowStatus.FAILED, EventKind.WORKFLOW_ROLLED_BACK),
        (WorkflowStatus.ROLLED_BACK, EventKind.WORKFLOW_FAILED),
    ],
)
def test_illegal_workflow_moves_raise(status, event):
    with pytest.raises(InvalidTransitionError):
        next_status(status, event)


def test_terminal_statuses_accept_no_further_events():
    for terminal in (
        WorkflowStatus.SUCCEEDED,
        WorkflowStatus.FAILED,
        WorkflowStatus.ROLLED_BACK,
    ):
        for event in EventKind:
            if event in (
                EventKind.WORKFLOW_COMPLETED,
                EventKind.WORKFLOW_FAILED,
                EventKind.WORKFLOW_ROLLED_BACK,
            ):
                with pytest.raises(InvalidTransitionError):
                    next_status(terminal, event)


# --------------------------------------------------------------------------
# Engine end-to-end status flow
# --------------------------------------------------------------------------


def test_engine_runs_to_succeeded_with_terminal_events():
    engine = _engine()
    execution = engine.run_workflow("acme", _spec())
    assert execution.status is WorkflowStatus.SUCCEEDED
    kinds = [e.kind for e in execution.events]
    assert kinds[0] is EventKind.WORKFLOW_STARTED
    assert kinds[-1] is EventKind.WORKFLOW_COMPLETED
    assert EventKind.STEP_SUCCEEDED in kinds


def test_engine_status_progresses_pending_to_running_to_succeeded():
    engine = _engine()
    execution = engine.start_workflow("acme", _spec())
    assert execution.status is WorkflowStatus.RUNNING  # started implies running
    report = engine.advance(execution)
    assert report.terminal is True
    assert execution.status is WorkflowStatus.SUCCEEDED


# --------------------------------------------------------------------------
# Fail-closed start validation
# --------------------------------------------------------------------------


def test_start_in_unknown_namespace_fails_closed():
    engine = Engine()  # only "system" exists
    with pytest.raises(UnknownNamespaceError):
        engine.start_workflow("nope", _spec())


def test_duplicate_namespace_is_refused():
    reg = NamespaceRegistry()
    reg.create("acme")
    with pytest.raises(NamespaceExistsError):
        reg.create("acme")


def test_workflow_with_unregistered_step_handler_fails_at_start():
    engine = _engine()
    spec = WorkflowSpec(
        name="bad",
        steps=[Step(step_id="x", kind=StepKind.AGENT_LOOP)],  # no default handler
    )
    with pytest.raises(UnknownStepHandlerError):
        engine.start_workflow("acme", spec)


def test_workflow_with_unknown_step_kind_fails_at_start():
    engine = _engine()
    spec = WorkflowSpec(
        name="bad",
        steps=[Step(step_id="x", kind="teleport")],  # not a StepKind member
    )
    with pytest.raises(WorkflowValidationError):
        engine.start_workflow("acme", spec)


def test_workflow_with_unregistered_compensation_handler_fails_at_start():
    from core.model import Compensation

    engine = _engine()
    spec = WorkflowSpec(
        name="bad",
        steps=[
            Step(
                step_id="a",
                kind=StepKind.NOOP,
                compensate=Compensation(name="undo", handler="does-not-exist", args={}),
            )
        ],
    )
    with pytest.raises(WorkflowValidationError):
        engine.start_workflow("acme", spec)


# --------------------------------------------------------------------------
# Corrupt-log refusal on replay
# --------------------------------------------------------------------------


def test_replay_refuses_duplicate_workflow_started():
    engine = _engine()
    execution = engine.start_workflow("acme", _spec())
    records = engine.store.events("acme", execution.workflow_id)
    # Simulate a hand-edited log with a second workflow_started.
    duplicated = [records[0], records[0]]
    with pytest.raises(PersistenceError):
        WorkflowExecution.from_events(duplicated)


def test_replay_refuses_event_for_unknown_step():
    engine = _engine()
    execution = engine.start_workflow("acme", _spec())
    records = engine.store.events("acme", execution.workflow_id)
    bogus = [
        records[0],
        type(records[0])(
            seq=99,
            namespace_id="acme",
            workflow_id=execution.workflow_id,
            kind=EventKind.STEP_SUCCEEDED,
            ts="2026-01-01T00:00:00+00:00",
            payload={"step_id": "ghost", "attempt": 1},
        ),
    ]
    with pytest.raises(PersistenceError):
        WorkflowExecution.from_events(bogus)


def test_replay_requires_workflow_started_first():
    engine = _engine()
    execution = engine.run_workflow("acme", _spec())
    records = engine.store.events("acme", execution.workflow_id)
    # Drop the workflow_started record.
    with pytest.raises(PersistenceError):
        WorkflowExecution.from_events(records[1:])
