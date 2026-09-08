"""Real integration with engine/loop (issue #23), now merged on master.

``engine/loop`` merged in this wave (PR #72) and owns the per-session
deterministic actor loop (its ``AgentDecision`` audit-record vocabulary).
This suite wires it to the multi-agent runner seam through the *real*
modules: an ``engine.loop.AgentLoop`` drives a multi-agent specialist via
``AgentLoopRunner``, and real ``AgentDecision`` records map onto
``AgentResult`` via ``decision_to_agent_result``.  Guarded with
``pytest.importorskip`` so the suite stays green regardless of merge order.
"""

from __future__ import annotations

import pytest

from engine.multiagent.loop_seam import (
    AgentLoopRunner,
    LoopSeamError,
    decision_to_agent_result,
)
from engine.multiagent.model import AgentResult, AgentTask, ResultStatus


def test_real_engine_loop_agent_loop_drives_the_runner_seam():
    loop = pytest.importorskip("engine.loop")
    from engine.loop.model import ModelTier
    from engine.loop.policy import Profile
    from engine.loop.runtime import Action, AgentLoop, DecisionContext
    from engine.loop.tools import FunctionExecutor, ToolRegistry

    class FinalActor:
        """Deterministic engine/loop actor: answer confidently on step one."""

        def decide(self, ctx: DecisionContext) -> Action:
            del ctx
            return Action.final({"answer": "from-real-loop"}, confidence=0.99)

    def _never_run(_name, _args):
        raise AssertionError("no tool call should execute for a final-only actor")

    profile = Profile(
        agent_id="loop-specialist",
        default_tier=ModelTier.TIER1,
        tool_allowlist=(),
        final_schema=None,
    )
    loop_engine = AgentLoop(
        actor=FinalActor(),
        tools=ToolRegistry(executor=FunctionExecutor(_never_run), allowlist=()),
        profile=profile,
    )
    runner = AgentLoopRunner(loop_engine)

    task = AgentTask(
        task_id="t1", objective="answer the question", agent_id="loop-specialist"
    )
    result = runner.run_agent("loop-specialist", task)
    assert isinstance(result, AgentResult)
    assert result.ok
    assert result.output == {"answer": "from-real-loop"}
    assert result.confidence == 0.99


def test_real_agent_decision_maps_succeeded_to_agent_result():
    loop = pytest.importorskip("engine.loop")
    from engine.loop.model import AgentDecision, LoopOutcome

    decision = AgentDecision(
        loop_id="l1",
        tenant_id="tenant-acme",
        agent_id="loop-specialist",
        task_id="t1",
        outcome=LoopOutcome.SUCCEEDED.value,
        content={"answer": "verified"},
        confidence=0.97,
        reason="",
        reasons=("final answer passed confidence + schema",),
    )
    result = decision_to_agent_result(decision)
    assert result.ok
    assert result.output == {"answer": "verified"}
    assert result.confidence == 0.97
    assert "passed confidence" in result.reasoning


def test_real_non_succeeded_agent_decision_is_never_a_silent_pass():
    loop = pytest.importorskip("engine.loop")
    from engine.loop.model import AgentDecision, LoopOutcome

    for outcome in (
        LoopOutcome.ESCALATED,
        LoopOutcome.CANNOT_ASSESS,
        LoopOutcome.BUDGET_EXHAUSTED,
        LoopOutcome.FAILED,
    ):
        decision = AgentDecision(
            loop_id="l1",
            tenant_id="tenant-acme",
            agent_id="loop-specialist",
            task_id="t1",
            outcome=outcome.value,
            content=None,
            confidence=0.0,
            reason=f"loop {outcome.value}",
        )
        result = decision_to_agent_result(decision)
        assert result.ok is False
        assert result.status is ResultStatus.FAILED
        assert "loop" in result.error


def test_agent_loop_runner_refuses_a_non_terminal_run():
    loop = pytest.importorskip("engine.loop")

    class PartialRun:
        """A run object with no terminal decision (needs resume)."""

        decision = None

    class PartialLoop:
        def run(self, **kwargs):
            return PartialRun()

    runner = AgentLoopRunner(PartialLoop())
    task = AgentTask(task_id="t1", objective="x", agent_id="a")
    with pytest.raises(LoopSeamError, match="not terminal"):
        runner.run_agent("a", task)
