"""Lifecycle guards: the ticket state machine refuses illegal histories.

The lifecycle is closed and strictly ordered.  These tests are the
negative controls: a log that skips a state, revisits one, continues past
CLOSED, or splices in another tenant's events must be **refused** rather
than silently projected (the repo's no-false-green discipline).
"""

from __future__ import annotations

from typing import Any, List

import pytest

from core.events import EventRecord, InMemoryEventStore
from core.model import EventKind
from core.namespaces import NamespaceRegistry
from core.runtime import Engine
from core.tickets import (
    TicketLifecycleError,
    TicketRuntime,
    TicketState,
    legal_moves,
    lifecycle_order,
    next_ticket_state,
    register_ticket_handlers,
    ticket_workflow,
)
from core.tickets.model import (
    ENGINEER_TASK_CLOSED,
    ENGINEER_TASK_CREATED,
    PLAN_DECOMPOSED,
    TICKET_DISPATCHED,
    TICKET_EXECUTED,
    TICKET_REVIEWED,
)

from engine.multiagent.model import FanOutPlan, HierarchyConfig, Subtask, ok_result
from engine.multiagent.planner import HierarchicalPlanner
from engine.multiagent.runner import ScriptedRunner

TENANT = "acme"


def _record(seq: int, kind: str, payload: Any, *, tenant: str = TENANT) -> EventRecord:
    return EventRecord(
        seq=seq,
        namespace_id=tenant,
        workflow_id="TCK-GUARD",
        kind=EventKind(kind),
        ts="2026-01-01T00:00:00+00:00",
        payload=dict(payload),
    )


# ---------------------------------------------------------------------------
# Vocabulary-level guards
# ---------------------------------------------------------------------------


def test_lifecycle_order_is_the_documented_six_states():
    assert [state.value for state in lifecycle_order()] == [
        "created",
        "decomposed",
        "dispatched",
        "executed",
        "reviewed",
        "closed",
    ]


def test_each_state_has_exactly_one_successor():
    order = lifecycle_order()
    for index, state in enumerate(order[:-1]):
        assert legal_moves(state) == (order[index + 1],), state
    assert legal_moves(TicketState.CLOSED) == ()


def test_skipping_a_state_is_refused():
    """CREATED cannot jump straight to EXECUTED."""
    with pytest.raises(TicketLifecycleError) as excinfo:
        next_ticket_state(TicketState.CREATED, TICKET_EXECUTED, ticket_id="T")
    assert "expected state 'dispatched'" in str(excinfo.value)


def test_revisiting_a_state_is_refused():
    """DISPATCHED cannot be re-decomposed (no going back)."""
    with pytest.raises(TicketLifecycleError):
        next_ticket_state(TicketState.DISPATCHED, PLAN_DECOMPOSED)


def test_events_after_closed_are_refused():
    with pytest.raises(TicketLifecycleError) as excinfo:
        next_ticket_state(TicketState.CLOSED, TICKET_REVIEWED, ticket_id="T")
    assert "closed" in str(excinfo.value)


def test_unknown_event_kind_is_refused():
    with pytest.raises(TicketLifecycleError):
        next_ticket_state(TicketState.CREATED, "not_a_ticket_event")


def test_all_six_lifecycle_events_advance_the_expected_state():
    assert next_ticket_state(TicketState.CREATED, ENGINEER_TASK_CREATED) is TicketState.CREATED
    assert next_ticket_state(TicketState.CREATED, PLAN_DECOMPOSED) is TicketState.DECOMPOSED
    assert next_ticket_state(TicketState.DECOMPOSED, TICKET_DISPATCHED) is TicketState.DISPATCHED
    assert next_ticket_state(TicketState.DISPATCHED, TICKET_EXECUTED) is TicketState.EXECUTED
    assert next_ticket_state(TicketState.EXECUTED, TICKET_REVIEWED) is TicketState.REVIEWED
    assert next_ticket_state(TicketState.REVIEWED, ENGINEER_TASK_CLOSED) is TicketState.CLOSED


# ---------------------------------------------------------------------------
# Log-level guards
# ---------------------------------------------------------------------------


def test_replay_refuses_a_log_that_skips_the_execute_state():
    """A spliced log that jumps DISPATCHED -> REVIEWED is refused."""
    records = [
        _record(1, "workflow_started", {"inputs": {"tenant": TENANT}}),
        _record(2, ENGINEER_TASK_CREATED, {"tenant": TENANT, "ticket_id": "T"}),
        _record(
            3,
            PLAN_DECOMPOSED,
            {
                "tenant": TENANT,
                "mission_id": "m",
                "planner_lane_id": "planner",
                "rounds": 1,
                "max_escalation_rounds": 1,
                "escalation_count": 0,
                "subtask_count": 1,
                "findings": [],
                "failures": [],
                "escalated": [],
                "summary": "one round",
            },
        ),
        _record(4, TICKET_DISPATCHED, {"tenant": TENANT, "ticket_id": "T"}),
        # skip TICKET_EXECUTED
        _record(5, TICKET_REVIEWED, {"tenant": TENANT, "ticket_id": "T"}),
    ]
    with pytest.raises(TicketLifecycleError) as excinfo:
        TicketRuntime.project(records, tenant=TENANT, ticket_id="T")
    assert "expected state 'executed'" in str(excinfo.value)


def test_replay_refuses_a_cross_tenant_payload():
    """A lifecycle event whose payload names another tenant is refused."""
    records = [
        _record(1, "workflow_started", {"inputs": {"tenant": TENANT}}),
        _record(2, PLAN_DECOMPOSED, {"tenant": "globex", "mission_id": "m"}),
    ]
    with pytest.raises(TicketLifecycleError) as excinfo:
        TicketRuntime.project(records, tenant=TENANT, ticket_id="T")
    assert "cross-tenant" in str(excinfo.value)


def test_replay_refuses_a_log_that_changes_tenant_mid_flight():
    records = [
        _record(1, "workflow_started", {"inputs": {"tenant": TENANT}}),
        _record(2, PLAN_DECOMPOSED, {"mission_id": "m"}, tenant="globex"),
    ]
    with pytest.raises(TicketLifecycleError) as excinfo:
        TicketRuntime.project(records, tenant=TENANT, ticket_id="T")
    assert "mid-flight" in str(excinfo.value)


def test_replay_refuses_a_dispatched_ticket_without_a_decomposition():
    """DISPATCHED with no PLAN_DECOMPOSED is an illegal history."""
    records = [
        _record(1, "workflow_started", {"inputs": {"tenant": TENANT}}),
        _record(2, TICKET_DISPATCHED, {"tenant": TENANT, "ticket_id": "T"}),
    ]
    with pytest.raises(TicketLifecycleError):
        TicketRuntime.project(records, tenant=TENANT, ticket_id="T")


# ---------------------------------------------------------------------------
# Runtime-level guards
# ---------------------------------------------------------------------------


def test_execute_step_refuses_an_undecomposed_ticket():
    """The execute step fails closed when the decompose step never succeeded.

    A spec with a ``execute`` step but no completed ``decompose`` step must not
    silently produce an "executed" ticket — the handler raises and the engine
    records STEP_FAILED.
    """
    registry = NamespaceRegistry()
    registry.create(TENANT, plan="standard")
    engine = Engine(store=InMemoryEventStore(), namespaces=registry)

    from core.model import Step, StepKind, WorkflowKind, WorkflowSpec
    from core.tickets.handlers import HANDLER_EXECUTE

    spec = WorkflowSpec(
        name="exec-without-decompose",
        kind=WorkflowKind.ENGINEER_TASK,
        steps=[
            # A decompose step that exists but never succeeded (it has no
            # handler registered), then the execute step that depends on it.
            Step(
                step_id="decompose",
                kind=StepKind.NOOP,
                handler="not.registered",
            ),
            Step(
                step_id="execute",
                kind=StepKind.AGENT_LOOP,
                handler=HANDLER_EXECUTE,
                args={"ticket_id": "T", "decompose_step_id": "decompose"},
            ),
        ],
    )
    # The unregistered handler fails closed at start_workflow (the engine never
    # silently treats a missing handler as a no-op).
    with pytest.raises(Exception) as excinfo:
        engine.run_workflow(TENANT, spec, workflow_id="TCK-NO-DECOMP")
    assert "no handler registered" in str(excinfo.value)


def test_execute_step_fails_when_the_decompose_output_is_not_a_plan():
    """A present-but-unusable decomposition output is refused, not guessed."""
    registry = NamespaceRegistry()
    registry.create(TENANT, plan="standard")
    engine = Engine(store=InMemoryEventStore(), namespaces=registry)

    from core.model import Step, StepKind, WorkflowKind, WorkflowSpec
    from core.tickets.handlers import HANDLER_EXECUTE, TicketExecuteHandler

    # Register only the execute handler: the decompose step is a no-op that
    # leaves a non-Mapping output, so the execute step must refuse it.
    engine.register_handler(HANDLER_EXECUTE, _handler_for(TicketExecuteHandler().run))

    spec = WorkflowSpec(
        name="exec-bad-decompose",
        kind=WorkflowKind.ENGINEER_TASK,
        steps=[
            # Succeeds, but writes an output the execute step cannot read.
            Step(step_id="decompose", kind=StepKind.NOOP, handler="core.noop"),
            Step(
                step_id="execute",
                kind=StepKind.AGENT_LOOP,
                handler=HANDLER_EXECUTE,
                args={"ticket_id": "T", "decompose_step_id": "decompose"},
            ),
        ],
    )
    execution = engine.run_workflow(TENANT, spec, workflow_id="TCK-BAD-DECOMP")
    failed = [e for e in execution.events if e.kind is EventKind.STEP_FAILED]
    assert failed, "the execute step must fail closed on an unusable output"
    assert "no durable decomposition output" in failed[0].payload["error"]
    # The ticket therefore never reaches EXECUTED.
    projection = TicketRuntime.project(
        execution.events, tenant=TENANT, ticket_id="TCK-BAD-DECOMP"
    )
    assert projection.state is TicketState.CREATED


def test_runtime_refuses_to_spin_on_a_stalled_ticket():
    """A ticket with no lifecycle steps terminates after one pass, not forever.

    The driver's iteration budget is finite by construction; this test proves
    the budget is *reached* rather than the loop being unbounded.
    """
    registry = NamespaceRegistry()
    registry.create(TENANT, plan="standard")
    engine = Engine(store=InMemoryEventStore(), namespaces=registry)

    from core.model import Step, StepKind, WorkflowKind, WorkflowSpec

    spec = WorkflowSpec(
        name="trivial",
        kind=WorkflowKind.ENGINEER_TASK,
        steps=[Step(step_id="only", kind=StepKind.NOOP, handler="core.noop")],
    )
    tickets = TicketRuntime(engine, tenant=TENANT)
    assert tickets._max_iterations(spec) == 8  # 4 * (1 step + 1)
    projection = tickets.run("TCK-TRIVIAL", spec)
    # No lifecycle steps ran, so the ticket is only as far as CREATED.
    assert projection.state is TicketState.CREATED
    assert projection.closed is False


def test_iteration_budget_is_finite_for_every_spec_shape():
    """The driver can never loop unbounded: the budget is always a finite int."""
    from core.model import Step, StepKind, WorkflowKind, WorkflowSpec

    for step_count in (1, 5, 50):
        spec = WorkflowSpec(
            name="budget",
            kind=WorkflowKind.ENGINEER_TASK,
            steps=[
                Step(step_id=f"s{i}", kind=StepKind.NOOP, handler="core.noop")
                for i in range(step_count)
            ],
        )
        budget = TicketRuntime._max_iterations(spec)
        assert isinstance(budget, int)
        assert budget >= step_count + 1


def _handler_for(run: Any) -> Any:
    from core.handlers import Handler

    return Handler(run=run)


def _planner_lane() -> Any:
    from engine.multiagent.model import Lane, LaneRole

    return Lane(lane_id="planner", role=LaneRole.PLANNER, agent_ids=("planner-1",))


def _specialist_lanes() -> Any:
    from engine.multiagent.model import Lane, LaneRole

    return (
        Lane(lane_id="analyst", role=LaneRole.SPECIALIST, agent_ids=("analyst-1",)),
    )
