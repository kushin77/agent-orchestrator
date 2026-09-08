"""Guaranteed termination of the bounded actor loop (issue #23 acceptance).

The negative cases are the point: an actor that *never* produces a final
answer (it would happily tool-call forever) must still terminate — the loop
budgets its iterations per tier, escalates monotonically, and reaches a
terminal outcome (ESCALATED to a human, or FAILED when no human is in the
loop) inside the hard step bound.  No infinite silent loop is possible.
"""

from __future__ import annotations

from engine.loop.model import EscalationTargetKind, LoopOutcome
from engine.loop.policy import LoopPolicy
from loop_support import (
    AlwaysToolActor,
    ToolThenFinalActor,
    make_policy,
    run_loop,
)


def test_success_loop_is_bounded_and_records_a_trace():
    run = run_loop(ToolThenFinalActor())
    assert run.finished
    decision = run.decision
    assert decision.outcome == LoopOutcome.SUCCEEDED.value
    assert decision.iterations_used == 2  # one tool call + one final
    assert len(decision.trace) == 2
    assert decision.escalation_events == ()
    assert decision.content == {"answer": "resolved"}


def test_never_final_actor_terminates_via_escalation():
    policy = LoopPolicy()
    run = run_loop(AlwaysToolActor(), policy=policy)
    assert run.finished  # the guarantee: it terminates
    decision = run.decision
    assert decision.outcome == LoopOutcome.ESCALATED.value
    # Never exceeds the hard step bound: max_iterations * (tiers + 1).
    assert decision.iterations_used <= policy.hard_step_bound()
    assert decision.iterations_used > policy.max_iterations  # it did escalate tiers
    assert len(decision.trace) == decision.iterations_used
    # Default policy has a human in the loop -> final hand-off is to a human.
    assert decision.escalation_events[-1].target_kind == EscalationTargetKind.HUMAN.value


def test_never_final_actor_without_human_fails_honestly():
    run = run_loop(
        AlwaysToolActor(), policy=make_policy(human_in_loop=False)
    )
    decision = run.decision
    assert decision.outcome == LoopOutcome.FAILED.value
    assert "iteration_budget_exhausted" in decision.reasons
    assert decision.escalation_events[-1].target_kind == EscalationTargetKind.TIER.value


def test_escalation_is_monotonic_upwards_never_cycles():
    run = run_loop(AlwaysToolActor())
    decision = run.decision
    tier_hops = [
        e.to_tier
        for e in decision.escalation_events
        if e.target_kind == EscalationTargetKind.TIER.value
    ]
    # Strictly upward through the tier order: tier1 -> tier2 -> tier3.
    assert tier_hops == sorted(tier_hops)
    assert tier_hops == list(dict.fromkeys(tier_hops))  # no repeated tier
    # Every human hand-off is terminal (no to_tier).
    assert decision.escalation_events[-1].to_tier is None


def test_small_iteration_budget_is_respected():
    policy = make_policy(max_iterations=2)
    run = run_loop(AlwaysToolActor(), policy=policy)
    decision = run.decision
    assert run.finished
    assert decision.outcome in (
        LoopOutcome.ESCALATED.value,
        LoopOutcome.FAILED.value,
    )
    # 2 iterations * 3 tiers = 6 executed steps at most.
    assert decision.iterations_used <= 6
    assert len(decision.trace) == decision.iterations_used


def test_hard_step_bound_is_an_upper_limit():
    policy = make_policy(max_iterations=3, human_in_loop=True)
    run = run_loop(AlwaysToolActor(), policy=policy)
    assert run.finished
    assert run.decision.iterations_used <= policy.hard_step_bound()
    assert run.decision.outcome == LoopOutcome.ESCALATED.value
