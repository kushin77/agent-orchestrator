"""Review gates + C-suite escalation on the task lifecycle (issue #635).

Acceptance criteria exercised here:

* a **red gate can never yield a closed task** — proven twice: at the gate
  (``ReviewGateOutcome.open_gate is False``) *and* end-to-end through the real
  workbook-3 lifecycle (the gate's verdict is injected as the ticket's review,
  and the ticket's own close step fails closed, so the ticket ends at REVIEWED);
* the **COO -> CEO escalation terminates** — the declared chain ends at the
  terminal rung and escalating past it raises rather than looping.

The gate consumes the real ``model.merge_verdict`` / ``gate.VerifyOutcome`` from
this package and the real ``engine.core.tickets`` handlers from workbook-3 —
neither is mocked.  ``governance/merge/tests/conftest.py`` puts this package
directory on ``sys.path``; the ticket suite's own bootstrap (putting ``engine/``
and ``engine/core`` on ``sys.path``) is replicated here so the two halves can be
joined in one process.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import pytest

# --- the workbook-3 bootstrap (engine/ + engine/core on sys.path) -----------
_MERGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _MERGE_DIR not in sys.path:
    sys.path.insert(0, _MERGE_DIR)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(  # .../<repo root>
    os.path.dirname(os.path.dirname(_TESTS_DIR))
)
_ENGINE_ROOT = os.path.join(_REPO_ROOT, "engine")
for _path in (_ENGINE_ROOT, _REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import gates  # noqa: E402
from gate import outcome_from_exit_code  # noqa: E402
from model import BlockReason  # noqa: E402


def _load_engine_package():
    """Import the ``engine/`` package, disambiguating the name collision.

    NAME COLLISION (real, measured): this package ships
    ``governance/merge/engine.py`` and the merge suite's conftest puts
    ``governance/merge`` on ``sys.path`` (it must — the sibling suite imports
    ``from engine import MergeGovernanceEngine``).  So a bare ``import engine``
    inside this joined suite resolves to *that* module and the ``engine/``
    namespace package becomes unreachable ("'engine' is not a package").

    The two packages this test joins genuinely intersect on the name, so the
    namespace is imported **explicitly** and the previous binding is restored
    as soon as the workbook-3 objects we need are captured — the sibling
    merge tests keep their ``governance/merge/engine.py`` binding untouched.
    """
    import types as _types

    previous = sys.modules.get("engine")
    namespace = _types.ModuleType("engine")
    namespace.__path__ = [_ENGINE_ROOT]  # type: ignore[attr-defined]
    sys.modules["engine"] = namespace
    try:
        from engine.multiagent.model import (  # noqa: PLC0415
            FanOutPlan,
            HierarchyConfig,
            Lane,
            LaneRole,
            Mission,
            Subtask,
            ok_result,
        )
        from engine.multiagent.planner import HierarchicalPlanner  # noqa: PLC0415
        from engine.multiagent.runner import ScriptedRunner  # noqa: PLC0415
    finally:
        if previous is not None:
            sys.modules["engine"] = previous
        else:  # pragma: no cover - only when nothing bound ``engine`` yet
            del sys.modules["engine"]
    return {
        "FanOutPlan": FanOutPlan,
        "HierarchyConfig": HierarchyConfig,
        "Lane": Lane,
        "LaneRole": LaneRole,
        "Mission": Mission,
        "Subtask": Subtask,
        "ok_result": ok_result,
        "HierarchicalPlanner": HierarchicalPlanner,
        "ScriptedRunner": ScriptedRunner,
    }


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

EXECUTOR = "coder"


def _gate(rc: int, commit: str | None = "abc123", **kwargs: Any):
    """Run the review gate with a verify outcome built from a raw exit code."""
    verify = outcome_from_exit_code(rc, commit, evidence=f"rc={rc}")
    params: dict[str, Any] = dict(
        task_id="TCK-1",
        verify=verify,
        reviewer_id="reviewer",
        reviewer_approved=True,
        executor=EXECUTOR,
    )
    params.update(kwargs)
    return gates.review_gate(**params)


# --------------------------------------------------------------------------- #
# the gate consumes the merge-verdict semantics
# --------------------------------------------------------------------------- #


class TestGreenGate:
    def test_green_verify_named_to_a_commit_plus_independent_reviewer_opens(self):
        outcome = _gate(0, "abc123")
        assert outcome.open_gate is True
        assert outcome.reasons == ()
        assert outcome.verify_commit == "abc123"
        assert outcome.verify_status == "OK"
        assert outcome.reviewer_id == "reviewer"
        assert outcome.escalated == ()

    def test_green_but_unattested_verify_cannot_open_the_gate(self):
        # an attestation names a COMMIT: OK with no commit is not green
        outcome = _gate(0, commit=None)
        assert outcome.open_gate is False
        assert BlockReason.VERIFY_NO_COMMIT.value in outcome.reasons

    def test_the_gate_is_the_same_rule_as_the_merge_verdict(self):
        # the exact BlockReason vocabulary is reused, not re-invented
        outcome = _gate(1, "abc123")
        assert BlockReason.VERIFY_NOT_GREEN.value in outcome.reasons
        assert set(outcome.reasons) <= {member.value for member in BlockReason}


class TestRedGateNeverOpens:
    @pytest.mark.parametrize("rc", [1, 2, 124])
    def test_non_green_verify_closes_the_gate(self, rc: int):
        outcome = _gate(rc, "abc123")
        assert outcome.open_gate is False
        assert outcome.blocked is True

    def test_verify_not_ok_reason(self):
        assert BlockReason.VERIFY_NOT_GREEN.value in _gate(1).reasons

    def test_cannot_assess_is_never_a_pass(self):
        # the honesty tri-state: CANNOT-ASSESS (rc=2) never reads green
        outcome = _gate(2, "abc123")
        assert outcome.verify_status == "CANNOT-ASSESS"
        assert outcome.open_gate is False
        assert BlockReason.VERIFY_CANNOT_ASSESS.value in outcome.reasons

    def test_a_red_gate_is_closed_even_with_a_perfect_review(self):
        # no reviewer verdict rescues a red gate (evidence beats approval)
        outcome = _gate(1, "abc123", reviewer_id="reviewer", reviewer_approved=True)
        assert outcome.open_gate is False
        assert BlockReason.VERIFY_NOT_GREEN.value in outcome.reasons


class TestIndependentReviewer:
    def test_a_reviewer_who_is_the_executor_is_self_review(self):
        outcome = _gate(0, "abc123", reviewer_id=EXECUTOR)
        assert outcome.open_gate is False
        assert BlockReason.SELF_REVIEW.value in outcome.reasons
        assert outcome.reviewer_id is None

    def test_no_reviewer_blocks(self):
        outcome = _gate(0, "abc123", reviewer_id=None)
        assert outcome.open_gate is False
        assert BlockReason.REVIEWER_NOT_ASSIGNED.value in outcome.reasons

    def test_a_non_reviewer_posture_cannot_review(self):
        # the reviewer posture class is rigid (issue #11 semantics)
        outcome = _gate(0, "abc123", reviewer_posture="executor")
        assert outcome.open_gate is False
        assert BlockReason.SELF_REVIEW.value in outcome.reasons

    def test_reviewer_rejection_blocks(self):
        outcome = _gate(0, "abc123", reviewer_approved=False)
        assert outcome.open_gate is False
        assert BlockReason.REVIEW_REJECTED.value in outcome.reasons

    def test_self_merge_without_the_carve_out_blocks(self):
        outcome = _gate(0, "abc123", concluder=EXECUTOR, owner_carve_out=False)
        assert outcome.open_gate is False
        assert BlockReason.SELF_MERGE_WITHOUT_CARVE_OUT.value in outcome.reasons

    def test_self_merge_with_the_carve_out_and_green_evidence_opens(self):
        outcome = _gate(0, "abc123", concluder=EXECUTOR, owner_carve_out=True)
        assert outcome.open_gate is True


# --------------------------------------------------------------------------- #
# step_review: the verdict the lifecycle's review step consumes
# --------------------------------------------------------------------------- #


class TestStepReviewTranslation:
    def test_open_gate_maps_to_an_approved_ticket_review(self):
        step = _gate(0, "abc123").step_review()
        assert step["approved"] is True
        assert step["reviewed_by"] == "reviewer"
        assert "OPEN" in step["rationale"]

    def test_closed_gate_maps_to_a_rejected_ticket_review(self):
        step = _gate(1, "abc123").step_review()
        assert step["approved"] is False
        assert "CLOSED" in step["rationale"]

    def test_the_mapping_is_accepted_by_the_lifecycle_review_handler(self):
        # the gate hands the lifecycle exactly the shape TicketReview.from_dict
        # accepts — proven against the real workbook-3 model
        from core.tickets.model import TicketReview  # noqa: PLC0415

        for rc in (0, 1):
            step = _gate(rc, "abc123").step_review()
            review = TicketReview.from_dict(step)
            assert review.approved is (rc == 0)

    def test_require_open_fails_closed_on_a_closed_gate(self):
        with pytest.raises(gates.GateBlocked):
            gates.require_open(_gate(1, "abc123"))
        # an open gate is not an error
        gates.require_open(_gate(0, "abc123"))


# --------------------------------------------------------------------------- #
# escalation: declared edges, and termination
# --------------------------------------------------------------------------- #


class TestEscalationEdges:
    def test_a_blocked_task_escalates_to_coo_then_ceo(self):
        outcome = _gate(1, "abc123")
        assert [step.role.value for step in outcome.escalated] == ["COO", "CEO"]
        assert [step.trigger.value for step in outcome.escalated] == [
            "pacing",
            "board-escalation",
        ]
        assert outcome.escalated_to is gates.EscalationRole.CEO

    def test_an_open_gate_escalates_nowhere(self):
        assert _gate(0, "abc123").escalated == ()
        assert _gate(0, "abc123").escalated_to is None

    def test_escalation_can_be_opt_out(self):
        assert _gate(1, escalate_on_block=False).escalated == ()

    def test_the_declared_chain_starts_at_the_root_and_ends_terminal(self):
        chain = gates.escalation_chain(reason="blocked")
        assert chain[0].role is gates.ESCALATION_ROOT
        assert chain[-1].role is gates.ESCALATION_TERMINAL
        assert len(chain) == gates.ESCALATION_DEPTH

    def test_the_edge_table_has_exactly_one_successor_per_rung(self):
        for role, (successor, _trigger) in gates.ESCALATION_EDGES.items():
            assert successor is not role
            if successor is not None:
                assert successor in gates.ESCALATION_EDGES


class TestEscalationTerminates:
    def test_escalating_past_the_terminal_rung_raises(self):
        terminal = gates.root_escalation(reason="blocked").escalate(reason="still blocked")
        assert terminal.role is gates.ESCALATION_TERMINAL
        with pytest.raises(gates.EscalationTerminated):
            terminal.escalate(reason="blocked again")

    def test_the_chain_len_is_bounded_by_the_declared_depth(self):
        seen = [gates.root_escalation()]
        for _ in range(10):  # a caller that refuses to stop
            try:
                seen.append(seen[-1].escalate())
            except gates.EscalationTerminated:
                break
        assert len(seen) == gates.ESCALATION_DEPTH

    def test_depths_are_monotonic_and_terminal(self):
        chain = gates.escalation_chain()
        assert [step.depth for step in chain] == list(
            range(1, gates.ESCALATION_DEPTH + 1)
        )
        assert gates.ESCALATION_EDGES[gates.ESCALATION_TERMINAL][0] is None


# --------------------------------------------------------------------------- #
# end-to-end: a red gate can NEVER yield a closed task
# --------------------------------------------------------------------------- #


def _ticket_run(*, reviewer_approved: bool, rc: int, commit: str | None = "abc123"):
    """Drive a real ticket through the workbook-3 lifecycle with a gate verdict.

    Builds the full workbook-3 world (real ``Engine``, real
    ``HierarchicalPlanner``, real ticket handlers) and injects the *gate's own*
    ``step_review()`` mapping as the ticket's review verdict.  The only thing
    this test decides is the gate outcome — everything downstream is the real
    lifecycle.
    """
    from core.events import InMemoryEventStore  # noqa: PLC0415
    from core.namespaces import NamespaceRegistry  # noqa: PLC0415
    from core.runtime import Engine  # noqa: PLC0415
    from core.tickets import (  # noqa: PLC0415
        TicketRuntime,
        register_ticket_handlers,
        ticket_workflow,
    )

    ma = _load_engine_package()
    FanOutPlan = ma["FanOutPlan"]
    HierarchyConfig = ma["HierarchyConfig"]
    Lane = ma["Lane"]
    LaneRole = ma["LaneRole"]
    Mission = ma["Mission"]
    Subtask = ma["Subtask"]
    ok_result = ma["ok_result"]
    HierarchicalPlanner = ma["HierarchicalPlanner"]
    ScriptedRunner = ma["ScriptedRunner"]

    tenant, ticket_id, mission_id = "acme", "TCK-635", "mission-635"
    registry = NamespaceRegistry()
    registry.create(tenant, plan="standard")
    engine = Engine(store=InMemoryEventStore(), namespaces=registry)

    planner_lane = Lane(lane_id="planner", role=LaneRole.PLANNER, agent_ids=("planner-1",))
    specialist_lanes = (
        Lane(lane_id="analyst", role=LaneRole.SPECIALIST, agent_ids=("analyst-1",)),
        Lane(lane_id="reviewer", role=LaneRole.SPECIALIST, agent_ids=("reviewer-1",)),
    )
    planner = HierarchicalPlanner(
        ScriptedRunner()
        .on("analyst-1", "t1", ok_result("analyst-1", "t1", output={"cause": "stale"})),
        planner_lane=planner_lane,
        specialist_lanes=specialist_lanes,
        config=HierarchyConfig(max_escalation_rounds=2),
    )
    register_ticket_handlers(engine, decomposer=planner, max_escalation_rounds=2)

    def _plan(ctx: Any) -> FanOutPlan:
        mission: Mission = ctx.mission
        return FanOutPlan(
            mission_id=mission.mission_id,
            planner_lane_id="planner",
            subtasks=(
                Subtask(
                    subtask_id="t1",
                    objective="diagnose",
                    lane_id="analyst",
                    agent_id="analyst-1",
                ),
            ),
        )

    outcome = gates.review_gate(
        task_id=ticket_id,
        verify=outcome_from_exit_code(rc, commit, evidence=f"rc={rc}"),
        reviewer_id="reviewer",
        reviewer_approved=reviewer_approved,
        executor="coder",
    )
    spec = ticket_workflow(
        tenant=tenant,
        ticket_id=ticket_id,
        mission_id=mission_id,
        objective="restore the nightly feed",
        decomposer=_plan,
        dispatch_lane="analyst",
        review=outcome.step_review(),
    )
    tickets = TicketRuntime(engine, tenant=tenant)
    projection = tickets.run(ticket_id, spec, title="feed down")
    return outcome, projection


class TestRedGateNeverClosesATask:
    def test_a_red_gate_leaves_the_task_unclosed(self):
        outcome, projection = _ticket_run(reviewer_approved=True, rc=1)
        # the gate itself is closed
        assert outcome.open_gate is False
        assert BlockReason.VERIFY_NOT_GREEN.value in outcome.reasons
        # and the real lifecycle refuses to close the task
        assert projection.closed is False
        assert projection.state.value == "reviewed"
        assert projection.approved is False

    def test_a_cannot_assess_gate_leaves_the_task_unclosed(self):
        outcome, projection = _ticket_run(reviewer_approved=True, rc=2)
        assert outcome.open_gate is False
        assert projection.closed is False
        assert projection.state.value == "reviewed"

    def test_an_unattested_green_gate_leaves_the_task_unclosed(self):
        outcome, projection = _ticket_run(reviewer_approved=True, rc=0, commit=None)
        assert outcome.open_gate is False
        assert projection.closed is False

    def test_a_reviewer_rejection_leaves_the_task_unclosed(self):
        outcome, projection = _ticket_run(reviewer_approved=False, rc=0)
        assert outcome.open_gate is False
        assert projection.closed is False
        assert projection.state.value == "reviewed"

    def test_a_fully_green_gate_closes_the_task(self):
        outcome, projection = _ticket_run(reviewer_approved=True, rc=0)
        assert outcome.open_gate is True
        assert projection.closed is True
        assert projection.state.value == "closed"
        assert projection.lifecycle() == (
            "created",
            "decomposed",
            "dispatched",
            "executed",
            "reviewed",
            "closed",
        )

    def test_a_blocked_task_escalates_and_a_closed_task_never_does(self):
        blocked, _ = _ticket_run(reviewer_approved=True, rc=1)
        closed, _ = _ticket_run(reviewer_approved=True, rc=0)
        assert [step.role.value for step in blocked.escalated] == ["COO", "CEO"]
        assert closed.escalated == ()
