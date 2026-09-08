"""Escalation policy: complexity/uncertainty -> higher tier or human-in-loop.

Issue #23 acceptance #2: complexity/uncertainty routes to a higher-tier
persona (hermes pattern) and confidence below the human floor hands the loop
to a human-in-the-loop (gmail 0.9/0.6 triage pattern).  All escalation is
deterministic, monotonic upward, and recorded on the decision.
"""

from __future__ import annotations

from engine.loop.escalate import ComplexityScorer
from engine.loop.model import (
    EscalationTargetKind,
    LoopOutcome,
    ModelTier,
)
from loop_support import (
    AlwaysToolActor,
    FinalActor,
    TierAwareActor,
    make_policy,
    make_profile,
    run_loop,
)

SIMPLE_INPUT = {"prompt": "answer yes or no"}

# Deliberately high-signal input: many lists + structure/depth/breadth markers.
MAX_INPUT = {
    "prompt": (
        "Architect and design a multi-service platform: compare and contrast "
        "the trade-offs, evaluate why a decomposed strategy is required, plan "
        "the integration across repositories, analyse the impact on all "
        "services and components, and explain the root cause of the current "
        "failures. Multiple different files must be changed and should be "
        "coordinated."
    ),
    "requirements": ["r1", "r2", "r3", "r4", "r5", "r6"],
    "constraints": [
        "must scale", "must comply", "must test", "should document",
        "required observability", "need rollback",
    ],
    "services": ["svc-a", "svc-b", "svc-c", "svc-d"],
    "owners": ["team-1", "team-2"],
}


def test_low_confidence_hands_to_human_in_loop():
    run = run_loop(FinalActor(0.4))
    decision = run.decision
    assert decision.outcome == LoopOutcome.ESCALATED.value
    assert decision.content is None  # nothing was silently accepted
    assert len(decision.escalation_events) == 1
    event = decision.escalation_events[0]
    assert event.trigger == "low_confidence"
    assert event.target_kind == EscalationTargetKind.HUMAN.value
    assert event.to_tier is None


def test_mid_confidence_escalates_to_higher_tier_then_succeeds():
    run = run_loop(TierAwareActor())
    decision = run.decision
    assert decision.outcome == LoopOutcome.SUCCEEDED.value
    assert decision.tier == ModelTier.TIER2.value
    assert decision.content == {"answer": "verified"}
    assert len(decision.escalation_events) == 1
    event = decision.escalation_events[0]
    assert event.trigger == "low_confidence"
    assert event.target_kind == EscalationTargetKind.TIER.value
    assert event.to_tier == ModelTier.TIER2.value


def test_high_confidence_succeeds_without_escalation():
    run = run_loop(FinalActor(0.95))
    decision = run.decision
    assert decision.outcome == LoopOutcome.SUCCEEDED.value
    assert decision.escalation_events == ()
    assert decision.content == {"answer": "done"}


def test_unconfirmable_without_escalation_fails_honestly():
    run = run_loop(
        FinalActor(0.7),
        policy=make_policy(tiered_escalation=False, human_in_loop=False),
    )
    decision = run.decision
    assert decision.outcome == LoopOutcome.FAILED.value
    assert "uncertain_cannot_confirm" in decision.reasons
    assert decision.content is None


def test_complexity_routes_to_a_deeper_starting_tier():
    scorer = ComplexityScorer()
    routed = scorer.route(MAX_INPUT)
    assert routed is ModelTier.TIER3  # guard: the fixture really is "deep"
    run = run_loop(
        FinalActor(0.98),
        input_=MAX_INPUT,
        profile=make_profile(tier=ModelTier.TIER1),
        complexity=scorer,
    )
    decision = run.decision
    assert decision.tier == routed.value
    assert decision.escalation_events
    first = decision.escalation_events[0]
    assert first.trigger == "complexity"
    assert first.target_kind == EscalationTargetKind.TIER.value


def test_simple_task_stays_on_the_default_tier():
    scorer = ComplexityScorer()
    assert scorer.route(SIMPLE_INPUT) is ModelTier.TIER1
    run = run_loop(
        FinalActor(0.98),
        input_=SIMPLE_INPUT,
        complexity=scorer,
    )
    decision = run.decision
    assert decision.tier == ModelTier.TIER1.value
    assert all(e.trigger != "complexity" for e in decision.escalation_events)


def test_step_budget_exceeded_trigger_is_recorded():
    run = run_loop(
        AlwaysToolActor(), policy=make_policy(max_iterations=2)
    )
    decision = run.decision
    assert any(
        e.trigger == "step_budget_exceeded" for e in decision.escalation_events
    )
