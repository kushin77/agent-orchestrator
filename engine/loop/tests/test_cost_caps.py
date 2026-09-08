"""Loop cost caps: token budget + iteration cap are hard, never silent.

Issue #23 acceptance #4: the loop enforces a whole-loop token budget and a
per-tier iteration cap.  A SUCCEEDED outcome can never exceed the token
budget; when a budget is exhausted the loop ends in a terminal outcome
(BUDGET_EXHAUSTED, or ESCALATED to a human when one is in the loop) — never
by silently continuing or silently passing.
"""

from __future__ import annotations

from engine.loop.model import EscalationTargetKind, LoopOutcome
from loop_support import AlwaysToolActor, FinalActor, make_policy, run_loop


def test_token_budget_is_a_hard_cap_without_human():
    policy = make_policy(token_budget=5, human_in_loop=False)
    run = run_loop(AlwaysToolActor(), policy=policy)
    decision = run.decision
    assert run.finished
    assert decision.outcome == LoopOutcome.BUDGET_EXHAUSTED.value
    assert "budget_exhausted" in decision.reasons
    # the cap is enforced: spent tokens exceed the budget, but the loop ended
    assert decision.tokens_used > 5
    assert decision.outcome != LoopOutcome.SUCCEEDED.value


def test_token_budget_escalates_to_human_when_in_loop():
    policy = make_policy(token_budget=5, human_in_loop=True)
    run = run_loop(AlwaysToolActor(), policy=policy)
    decision = run.decision
    assert decision.outcome == LoopOutcome.ESCALATED.value
    event = decision.escalation_events[-1]
    assert event.trigger == "token_budget_exceeded"
    assert event.target_kind == EscalationTargetKind.HUMAN.value
    assert decision.tokens_used > 5


def test_over_budget_high_confidence_final_is_not_accepted():
    """Cost cap beats confidence: a final that busts the budget is not SUCCEEDED."""
    policy = make_policy(token_budget=5, human_in_loop=False)
    run = run_loop(FinalActor(0.99, tokens=100), policy=policy)
    decision = run.decision
    assert decision.outcome == LoopOutcome.BUDGET_EXHAUSTED.value
    assert decision.confidence == 0.0  # the final was never accepted
    assert decision.content is None


def test_iteration_cap_is_hard_without_human():
    policy = make_policy(max_iterations=1, human_in_loop=False)
    run = run_loop(AlwaysToolActor(), policy=policy)
    decision = run.decision
    assert run.finished
    assert decision.outcome == LoopOutcome.FAILED.value
    assert "iteration_budget_exhausted" in decision.reasons
    assert decision.iterations_used <= policy.hard_step_bound()
    assert decision.iterations_used == 3  # one executed step per tier


def test_iteration_and_token_usage_are_accounted_on_the_decision():
    policy = make_policy(token_budget=100_000)
    run = run_loop(AlwaysToolActor(), policy=policy)
    decision = run.decision
    assert decision.tokens_used >= decision.iterations_used  # every step costs tokens
    assert decision.tokens_used > 0
    assert decision.iterations_used <= policy.hard_step_bound()
