"""AgentDecision audit record per loop (gmail per-loop decision pattern).

Issue #23 acceptance #5: every loop produces one :class:`AgentDecision`
carrying the full deterministic audit trail (outcome, content, confidence,
tier, iteration/token accounting, reasons, escalation events, step trace) —
JSON-safe and round-trippable so it can be stored alongside the workflow
event log.
"""

from __future__ import annotations

import json

from engine.loop.model import (
    ActionKind,
    LoopOutcome,
    decision_from_dict,
    decision_to_dict,
    decisions_equal,
)
from loop_support import (
    AGENT,
    AlwaysToolActor,
    TASK,
    TENANT,
    ToolThenFinalActor,
    run_loop,
)


def test_audit_fields_are_populated():
    run = run_loop(ToolThenFinalActor())
    decision = run.decision
    assert decision.loop_id == "loop:test"
    assert decision.tenant_id == TENANT
    assert decision.agent_id == AGENT
    assert decision.task_id == TASK
    assert decision.outcome == LoopOutcome.SUCCEEDED.value
    assert decision.confidence > 0.9
    assert decision.tier == "tier1"
    assert decision.iterations_used == 2
    assert decision.tokens_used > 0
    assert decision.failure_count == 0
    assert "confirmed" in decision.reasons
    assert decision.reason
    assert decision.started_at and decision.finished_at


def test_decision_round_trips_byte_identically():
    decision = run_loop(ToolThenFinalActor()).decision
    restored = decision_from_dict(decision_to_dict(decision))
    assert decisions_equal(decision, restored)
    assert json.dumps(decision_to_dict(decision), sort_keys=True) == json.dumps(
        decision_to_dict(restored), sort_keys=True
    )


def test_trace_has_one_record_per_executed_step():
    decision = run_loop(ToolThenFinalActor()).decision
    assert len(decision.trace) == decision.iterations_used
    for row in decision.trace:
        assert row.iteration >= 1
        assert row.action in (ActionKind.TOOL_CALL.value, ActionKind.FINAL.value)
        assert row.tokens_used >= 0


def test_escalation_events_are_seq_ordered_and_monotonic():
    decision = run_loop(AlwaysToolActor()).decision
    seqs = [event.seq for event in decision.escalation_events]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))  # strictly increasing, no duplicates
    assert all(event.from_tier != event.to_tier for event in decision.escalation_events
               if event.to_tier is not None)


def test_failed_decision_records_reasons_and_content():
    from loop_support import DisallowedToolActor, make_policy

    run = run_loop(
        DisallowedToolActor(),
        policy=make_policy(on_allowlist_violation="cannot_assess"),
    )
    decision = run.decision
    assert decision.outcome == LoopOutcome.CANNOT_ASSESS.value
    assert decision.content is None
    assert "cannot_assess" in decision.reasons
    assert decision.reason  # a human-readable reason is always recorded
