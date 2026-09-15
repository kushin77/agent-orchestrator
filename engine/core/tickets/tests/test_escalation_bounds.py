"""Escalation bounds: decomposition always terminates.

Acceptance criterion (issue #634): decomposition runs "through the injected
decomposer/planner with ``max_escalation_rounds`` so missions always
terminate".  These tests drive the real
:class:`engine.multiagent.planner.HierarchicalPlanner` through the ticket
lifecycle and prove:

* a stubborn mission stops at the bound instead of looping, and the ticket
  reports the unresolved work honestly (never a silent pass);
* the round count is exactly ``1 + max_escalation_rounds``;
* the bound is *load-bearing* — ``max_escalation_rounds=0`` stops after the
  first round (this is what the mutation proof mutates);
* an escalation that resolves is visible as an extra round with no unresolved
  work;
* a doomed mission still terminates the ticket;
* a planner that ignores its own bound is refused by the lane.
"""

from __future__ import annotations

from typing import Any, Tuple

import pytest

from core.events import InMemoryEventStore
from core.model import WorkflowStatus
from core.namespaces import NamespaceRegistry
from core.runtime import Engine
from core.tickets import (
    TicketRuntime,
    TicketState,
    register_ticket_handlers,
    ticket_workflow,
)

from engine.multiagent.model import (
    FanOutPlan,
    HierarchyConfig,
    Subtask,
    fail_result,
    ok_result,
)
from engine.multiagent.planner import HierarchicalPlanner
from engine.multiagent.runner import ScriptedRunner

TENANT = "acme"
TICKET_ID = "TCK-BOUND"
MISSION_ID = "mission-bound"


def _plan(mission: Any, subtasks: Tuple[Subtask, ...]) -> FanOutPlan:
    return FanOutPlan(
        mission_id=mission.mission_id, planner_lane_id="planner", subtasks=subtasks
    )


def _stubborn_plan(ctx: Any) -> FanOutPlan:
    """Always the same failing required subtask: only the bound can stop it."""
    return _plan(
        ctx.mission,
        (
            Subtask(
                subtask_id="t1",
                objective="resolve the unresolved blocker",
                lane_id="analyst",
                agent_id="analyst-1",
                required=True,
            ),
        ),
    )


def _round_keyed_plan(ctx: Any) -> FanOutPlan:
    """A plan whose subtask id names the round, so escalation is observable."""
    return _plan(
        ctx.mission,
        (
            Subtask(
                subtask_id=f"t{ctx.round + 1}",
                objective="resolve the blocker",
                lane_id="analyst",
                agent_id="analyst-1",
                required=True,
            ),
        ),
    )


def _world(
    runner: Any,
    *,
    max_escalation_rounds: int,
    specialist_lanes: Any,
    planner_lane: Any,
) -> Tuple[Engine, TicketRuntime]:
    registry = NamespaceRegistry()
    registry.create(TENANT, plan="standard")
    engine = Engine(store=InMemoryEventStore(), namespaces=registry)
    planner = HierarchicalPlanner(
        runner,
        planner_lane=planner_lane,
        specialist_lanes=specialist_lanes,
        config=HierarchyConfig(max_escalation_rounds=max_escalation_rounds),
    )
    register_ticket_handlers(
        engine, decomposer=planner, max_escalation_rounds=max_escalation_rounds
    )
    return engine, TicketRuntime(engine, tenant=TENANT)


def _run(tickets: TicketRuntime, plan_fn: Any, **kwargs: Any) -> Any:
    """Run one ticket with ``plan_fn`` as the planner's per-round decomposer."""
    spec = ticket_workflow(
        tenant=TENANT,
        ticket_id=TICKET_ID,
        mission_id=MISSION_ID,
        objective="resolve the blocker",
        decomposer=plan_fn,
        dispatch_lane="analyst",
        **kwargs,
    )
    return tickets.run(TICKET_ID, spec)


def _stubborn_runner() -> ScriptedRunner:
    """A runner that fails the fixed subtask id ``t1`` for ``analyst-1``."""
    return ScriptedRunner().on_agent(
        "analyst-1", fail_result("analyst-1", "t1", error="blocked")
    )


@pytest.mark.parametrize("bound", [0, 1, 2, 3])
def test_stubborn_mission_runs_exactly_bound_plus_one_rounds(
    bound, planner_lane, specialist_lanes
):
    """The planner stops at the bound and reports the unresolved work."""
    _engine, tickets = _world(
        _stubborn_runner(),
        max_escalation_rounds=bound,
        specialist_lanes=specialist_lanes,
        planner_lane=planner_lane,
    )
    projection = _run(tickets, _stubborn_plan)

    decomposition = projection.decomposition
    assert decomposition is not None
    # 1 initial dispatch + `bound` re-plans — never more, never fewer.
    assert decomposition.rounds == 1 + bound
    assert decomposition.escalation_count == bound
    assert decomposition.max_escalation_rounds == bound
    # Honest reporting: the unresolved required subtask is named, not hidden.
    assert decomposition.failures == ("t1",)
    assert decomposition.escalated == ("t1",)
    assert decomposition.findings == ()
    assert decomposition.resolved is False
    assert decomposition.exhausted_escalation_budget is True


def test_bound_zero_means_no_escalation_at_all(planner_lane, specialist_lanes):
    """The degenerate bound is honoured: one round, zero escalations.

    This is the assertion the mutation proof targets — removing the bound in
    the planner makes this mission escalate past one round.
    """
    _engine, tickets = _world(
        _stubborn_runner(),
        max_escalation_rounds=0,
        specialist_lanes=specialist_lanes,
        planner_lane=planner_lane,
    )
    decomposition = _run(tickets, _stubborn_plan).decomposition
    assert decomposition is not None
    assert decomposition.rounds == 1
    assert decomposition.escalation_count == 0
    assert decomposition.max_escalation_rounds == 0
    assert decomposition.failures == ("t1",)


def test_bound_is_strictly_monotonic_in_round_count(planner_lane, specialist_lanes):
    """Increasing the bound increases the work, by exactly one round each time."""
    rounds = []
    for bound in (0, 1, 2, 4):
        _engine, tickets = _world(
            _stubborn_runner(),
            max_escalation_rounds=bound,
            specialist_lanes=specialist_lanes,
            planner_lane=planner_lane,
        )
        decomposition = _run(tickets, _stubborn_plan).decomposition
        assert decomposition is not None
        rounds.append(decomposition.rounds)
    assert rounds == [1, 2, 3, 5]


def test_resolved_escalation_needs_no_unresolved_reporting(
    planner_lane, specialist_lanes
):
    """Re-planning that *works* shows an extra round and no unresolved work.

    The plan is round-keyed (``t1`` on round 0, ``t2`` on round 1, ...) and the
    runner fails only ``t1`` — so the second round resolves the mission and the
    escalation count is exactly 1.
    """
    runner = (
        ScriptedRunner()
        .on("analyst-1", "t1", fail_result("analyst-1", "t1", error="no data access"))
        .on("analyst-1", "t2", ok_result("analyst-1", "t2", output={"resolved": True}))
    )
    _engine, tickets = _world(
        runner,
        max_escalation_rounds=3,
        specialist_lanes=specialist_lanes,
        planner_lane=planner_lane,
    )
    decomposition = _run(tickets, _round_keyed_plan).decomposition
    assert decomposition is not None
    assert decomposition.rounds == 2
    assert decomposition.escalation_count == 1
    assert decomposition.failures == ()
    assert decomposition.escalated == ()
    assert decomposition.resolved is True
    assert [f["subtask_id"] for f in decomposition.findings] == ["t2"]


def test_ticket_still_reaches_terminal_when_the_mission_never_resolves(
    planner_lane, specialist_lanes
):
    """A doomed mission terminates the ticket — it never runs forever."""
    engine, tickets = _world(
        _stubborn_runner(),
        max_escalation_rounds=2,
        specialist_lanes=specialist_lanes,
        planner_lane=planner_lane,
    )
    projection = _run(
        tickets,
        _stubborn_plan,
        review={
            "reviewed_by": "ops-lead",
            "approved": True,
            "rationale": "ship the diagnosis",
        },
    )

    # The lifecycle completed; the *execution* records the unresolved work.
    assert projection.state is TicketState.CLOSED
    execution = engine.get_workflow(TENANT, TICKET_ID)
    assert execution.status is WorkflowStatus.SUCCEEDED
    assert projection.execution is not None
    assert projection.execution["escalation_count"] == 2
    assert projection.execution["escalated"] == ["t1"]
    assert projection.execution["resolved"] is False


def test_escalation_bound_is_recorded_in_the_durable_event_payload(
    planner_lane, specialist_lanes
):
    """The bound is attested in the transcript, not just in memory."""
    engine, tickets = _world(
        _stubborn_runner(),
        max_escalation_rounds=3,
        specialist_lanes=specialist_lanes,
        planner_lane=planner_lane,
    )
    _run(tickets, _stubborn_plan)

    records = engine.store.events(TENANT, TICKET_ID)
    decomposed = [r for r in records if r.kind.value == "plan_decomposed"]
    assert len(decomposed) == 1
    payload = decomposed[0].payload
    assert payload["max_escalation_rounds"] == 3
    assert payload["rounds"] == 4
    assert payload["escalation_count"] == 3


# ---------------------------------------------------------------------------
# The lane refuses a planner that ignores its own bound
# ---------------------------------------------------------------------------


class _RoundProbe:
    """Stand-in for the planner's escalation context (only ``round`` matters)."""

    def __init__(self, mission: Any, round_no: int) -> None:
        self.mission = mission
        self.round = round_no


class _UnboundedPlanner:
    """A broken planner that reports more rounds than the bound permits."""

    def __init__(self, rounds: int) -> None:
        self._rounds = rounds

    def run(self, mission: Any, plan_fn: Any) -> Any:
        from engine.multiagent.model import fan_out_plan_to_dict

        probe_plan = plan_fn(_RoundProbe(mission, 0))
        return {
            "mission": mission.as_dict(),
            "planner_lane_id": "planner",
            "plans": [fan_out_plan_to_dict(probe_plan) for _ in range(self._rounds)],
            "reports": [{} for _ in range(self._rounds)],
            "escalation_count": self._rounds - 1,
            "max_escalation_rounds": 0,
            "aggregate": {"findings": [], "failures": [], "escalated": []},
        }


def _decompose_step() -> Any:
    from core.model import Step, StepKind

    return Step(
        step_id="decompose",
        kind=StepKind.AGENT_LOOP,
        handler="tickets.decompose",
        args={
            "ticket_id": TICKET_ID,
            "mission_id": MISSION_ID,
            "objective": "o",
            "plan_ref": "escalation-probe",
            "decomposer_ref": "unbounded-probe",
        },
    )


class _StepCtx:
    namespace_id = TENANT
    workflow_id = TICKET_ID
    step_id = "decompose"
    attempt = 1
    inputs: dict = {}
    engine = None


def test_decompose_step_fails_closed_when_the_planner_ignores_the_bound():
    """The lane validates the planner's own accounting; it never trusts it."""
    from core.tickets.handlers import (
        TicketDecomposeHandler,
        register_decomposer,
    )

    register_decomposer("escalation-probe", _stubborn_plan)
    register_decomposer("unbounded-probe", _UnboundedPlanner(rounds=9))

    handler = TicketDecomposeHandler(_UnboundedPlanner(rounds=9), max_escalation_rounds=1)
    with pytest.raises(Exception) as excinfo:
        handler.run(_decompose_step(), _StepCtx())
    assert "escalation bound was not honoured" in str(excinfo.value)
