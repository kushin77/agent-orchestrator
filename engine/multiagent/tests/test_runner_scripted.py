"""Injected runner seam tests: ScriptedRunner determinism + contract coercion."""

from __future__ import annotations

import pytest

from engine.multiagent.model import AgentResult, AgentTask, ResultStatus, ok_result
from engine.multiagent.runner import (
    RunnerContractError,
    ScriptedRunner,
    coerce_agent_result,
    default_fail,
    is_agent_runner,
)


def _task(task_id="t1", objective="do the thing", agent_id="a"):
    return AgentTask(task_id=task_id, objective=objective, agent_id=agent_id)


class TestScriptedRunner:
    def test_exact_script_is_deterministic_and_ordered(self):
        runner = ScriptedRunner()
        runner.on("a", "t1", ok_result("a", "t1", output="one"))
        runner.on("a", "t2", ok_result("a", "t2", output="two"))
        assert runner.run_agent("a", _task("t1")).output == "one"
        assert runner.run_agent("a", _task("t2")).output == "two"
        assert runner.calls == [("a", "t1"), ("a", "t2")]

    def test_agent_wildcard(self):
        runner = ScriptedRunner().on_agent("a", ok_result("a", "*", output="wild"))
        assert runner.run_agent("a", _task("t9")).output == "wild"

    def test_default_callable_covers_unscripted(self):
        runner = ScriptedRunner(default=default_fail)
        result = runner.run_agent("a", _task("t-unknown"))
        assert result.status is ResultStatus.FAILED

    def test_unscripted_call_raises_fail_closed(self):
        runner = ScriptedRunner()
        with pytest.raises(RunnerContractError, match="no scripted result"):
            runner.run_agent("a", _task("t-unknown"))

    def test_is_agent_runner_structural(self):
        assert is_agent_runner(ScriptedRunner())
        assert not is_agent_runner(object())


class TestCoercion:
    def test_accepts_agent_result_unchanged(self):
        result = ok_result("a", "t1")
        assert coerce_agent_result(result, "a", "t1") is result

    def test_accepts_result_shaped_dict(self):
        coerced = coerce_agent_result(
            {"status": "succeeded", "output": {"answer": 42}, "confidence": 0.9},
            "a",
            "t1",
        )
        assert isinstance(coerced, AgentResult)
        assert coerced.ok
        assert coerced.output == {"answer": 42}

    def test_none_return_is_contract_violation_never_silent_success(self):
        with pytest.raises(RunnerContractError, match="returned NoneType"):
            coerce_agent_result(None, "a", "t1")
