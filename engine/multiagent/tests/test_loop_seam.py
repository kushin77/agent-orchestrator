"""engine/loop (issue #23) seam: duck-typed adapter, no hard dependency.

``engine/loop`` merges in parallel in this wave and is not on master when
this suite runs, so the seam is documented + exercised with a stand-in module
that mimics a merged ``engine.loop`` exposing a duck-typed ``run_agent``
runner.  The real loop, when it lands, plugs in unchanged.
"""

from __future__ import annotations

import sys
import types

import pytest

from engine.multiagent.loop_seam import (
    AgentLoopRunner,
    LoopAgentRunner,
    LoopSeamError,
    decision_to_agent_result,
    loop_runner_if_present,
)
from engine.multiagent.model import AgentResult, AgentTask, ResultStatus, ok_result
from engine.multiagent.runner import is_agent_runner


def _task(task_id="t1", objective="x"):
    return AgentTask(task_id=task_id, objective=objective)


class FakeLoopRunner:
    """A stand-in for the engine/loop per-agent runner (issue #23 shape)."""

    def __init__(self, results):
        self._results = results
        self.calls = []

    def run_agent(self, agent_id, task):
        self.calls.append((agent_id, task.task_id))
        return self._results[(agent_id, task.task_id)]


class TestLoopAgentRunner:
    def test_adapter_delegates_to_loop_runner(self):
        fake = FakeLoopRunner({("planner-1", "t1"): ok_result("planner-1", "t1", output="loop-done")})
        adapter = LoopAgentRunner(fake)
        result = adapter.run_agent("planner-1", _task())
        assert result.ok and result.output == "loop-done"
        assert fake.calls == [("planner-1", "t1")]

    def test_adapter_coerces_result_shaped_dict(self):
        fake = FakeLoopRunner({("a", "t1"): {"status": "succeeded", "output": "coerced"}})
        adapter = LoopAgentRunner(fake)
        assert adapter.run_agent("a", _task()).output == "coerced"

    def test_missing_result_is_a_loop_seam_error_not_a_silent_pass(self):
        class _Broken:
            def run_agent(self, _agent_id, _task):
                return None

        adapter = LoopAgentRunner(_Broken())
        with pytest.raises(LoopSeamError):
            adapter.run_agent("a", _task())

    def test_constructor_rejects_non_runner(self):
        with pytest.raises(LoopSeamError, match="must expose run_agent"):
            LoopAgentRunner(object())


class TestLoopRunnerDiscovery:
    def test_graceful_when_loop_absent(self):
        """engine/loop is not on master yet: discovery returns None or a valid
        runner — never raises, never a broken adapter."""
        found = loop_runner_if_present()
        if found is not None:
            assert isinstance(found, LoopAgentRunner)
            assert is_agent_runner(found)

    def test_discovers_a_duck_typed_merged_loop(self, monkeypatch):
        import engine  # ensure the engine namespace package is importable

        fake = types.ModuleType("engine.loop")
        fake.runner = FakeLoopRunner({("a", "t1"): ok_result("a", "t1", output="from-loop")})
        monkeypatch.setitem(sys.modules, "engine.loop", fake)

        adapter = loop_runner_if_present()
        assert adapter is not None
        assert isinstance(adapter, LoopAgentRunner)
        result = adapter.run_agent("a", _task())
        assert result.output == "from-loop"

    def test_ignores_uninstantiated_runner_type(self, monkeypatch):
        import engine

        fake = types.ModuleType("engine.loop")

        class RunnerType:
            def run_agent(self, agent_id, task):
                return ok_result(agent_id, task.task_id)

        fake.runner = RunnerType  # a class, not an instance
        monkeypatch.setitem(sys.modules, "engine.loop", fake)
        assert loop_runner_if_present() is None


class TestDecisionMapping:
    def test_succeeded_decision_maps_to_ok_result(self):
        decision = _Decision(
            outcome="succeeded", content={"answer": 42}, confidence=0.98,
            reasons=("good",),
        )
        result = decision_to_agent_result(decision)
        assert isinstance(result, AgentResult)
        assert result.ok
        assert result.output == {"answer": 42}
        assert result.confidence == 0.98
        assert result.reasoning == "good"

    def test_non_succeeded_decision_maps_to_failed_never_silent(self):
        for outcome in ("escalated", "cannot_assess", "budget_exhausted", "failed"):
            decision = _Decision(outcome=outcome, content=None, confidence=0.0, reasons=())
            result = decision_to_agent_result(decision)
            assert result.status is ResultStatus.FAILED
            assert "loop outcome" in result.error


class TestAgentLoopRunner:
    def test_delegates_through_the_loop_run_seam(self):
        fake_loop = _FakeLoop({"answer": "looped"})
        runner = AgentLoopRunner(fake_loop)
        result = runner.run_agent("a", _task("t1"))
        assert result.ok
        assert result.output == {"answer": "looped"}
        assert fake_loop.last_task_id == "t1"

    def test_constructor_rejects_object_without_run(self):
        with pytest.raises(LoopSeamError, match="must expose run"):
            AgentLoopRunner(object())


def _Decision(outcome, content, confidence, reasons):
    # A plain namespace with the engine/loop AgentDecision attribute shape.
    return types.SimpleNamespace(
        outcome=outcome,
        content=content,
        confidence=confidence,
        reasons=reasons,
        reason="",
        agent_id="a",
        task_id="t1",
    )


class _FakeLoop:
    """Stand-in for engine/loop AgentLoop: run() -> run with .decision."""

    def __init__(self, content):
        self._content = content
        self.last_task_id = None

    def run(self, task_input, **kwargs):
        self.last_task_id = kwargs.get("task_id")
        return _FakeRun(self._content)


class _FakeRun:
    def __init__(self, content):
        self.decision = _Decision(
            outcome="succeeded", content=content, confidence=0.99, reasons=()
        )
