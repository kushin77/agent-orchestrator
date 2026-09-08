"""engine.multiagent — the injected agent-runner seam.

Multi-agent orchestration never talks to an agent provider directly: every
agent call goes through a duck-typed runner that satisfies
``run_agent(agent_id, task) -> AgentResult``.  The same seam is how
``engine/loop`` (issue #23, merged in parallel in this wave) plugs in later —
see :mod:`engine.multiagent.loop_seam`.  Tests inject a deterministic
:class:`ScriptedRunner` whose results are fully scripted, so orchestration
logic is exercised without any real model calls (fully offline).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from .model import AgentResult, AgentTask, ResultStatus, fail_result

# The runner protocol is deliberately duck-typed: any object exposing
# ``run_agent(agent_id, task) -> AgentResult`` satisfies it.  Nothing here
# imports engine.loop or any provider package.


def is_agent_runner(obj: Any) -> bool:
    """Structural check: does ``obj`` expose the ``run_agent`` seam?"""
    return obj is not None and callable(getattr(obj, "run_agent", None))


class RunnerContractError(RuntimeError):
    """Raised when a runner violates the ``run_agent`` return contract."""


def coerce_agent_result(result: Any, agent_id: str, task_id: str) -> AgentResult:
    """Normalize a runner return to an :class:`AgentResult`.

    Accepts an :class:`AgentResult` or a JSON-safe dict shaped like one.  A
    ``None``/malformed return is a *contract violation* and raises — an
    unreadable result must never be treated as a silent success.
    """
    if isinstance(result, AgentResult):
        return result
    if isinstance(result, dict):
        status = result.get("status", ResultStatus.SUCCEEDED.value)
        status = (
            ResultStatus(status)
            if isinstance(status, str)
            else ResultStatus.SUCCEEDED
        )
        return AgentResult(
            agent_id=str(result.get("agent_id", agent_id)),
            task_id=str(result.get("task_id", task_id)),
            status=status,
            output=result.get("output"),
            reasoning=str(result.get("reasoning", "")),
            confidence=float(result.get("confidence", 1.0)),
            error=str(result.get("error", "")),
        )
    raise RunnerContractError(
        f"runner returned {type(result).__name__} for {agent_id}/{task_id}; "
        "expected AgentResult or a JSON-safe dict"
    )


class ScriptedRunner:
    """Deterministic injected runner: exact (agent, task) -> result scripts.

    ``on(agent_id, task_id, result)`` pins one call; ``on_agent(agent_id,
    result)`` pins every call for an agent; a ``default`` callable covers the
    rest.  An unscripted call with no default raises (fail-closed) so a test
    can never silently pass on a call it forgot to script.  ``calls`` records
    every (agent_id, task_id) invocation in order for assertions.
    """

    def __init__(
        self,
        default: Optional[Callable[[str, AgentTask], AgentResult]] = None,
    ) -> None:
        self._table: Dict[Tuple[str, str], AgentResult] = {}
        self._agents: Dict[str, AgentResult] = {}
        self._default = default
        self.calls: list = []

    def on(self, agent_id: str, task_id: str, result: AgentResult) -> "ScriptedRunner":
        self._table[(agent_id, task_id)] = result
        return self

    def on_agent(self, agent_id: str, result: AgentResult) -> "ScriptedRunner":
        self._agents[agent_id] = result
        return self

    def on_all(self, result: AgentResult) -> "ScriptedRunner":
        self._agents["*"] = result
        return self

    def run_agent(self, agent_id: str, task: AgentTask) -> AgentResult:
        self.calls.append((agent_id, task.task_id))
        key = (agent_id, task.task_id)
        if key in self._table:
            return self._table[key]
        if agent_id in self._agents:
            return self._agents[agent_id]
        if "*" in self._agents:
            return self._agents["*"]
        if self._default is not None:
            return self._default(agent_id, task)
        raise RunnerContractError(
            f"no scripted result for {agent_id}/{task.task_id} "
            f"(objective={task.objective!r})"
        )

    @property
    def call_count(self) -> int:
        return len(self.calls)


def default_fail(agent_id: str, task: AgentTask) -> AgentResult:
    """Convenience default: every unscripted agent call fails (negative path)."""
    return fail_result(
        agent_id,
        task.task_id,
        error="unscripted call failed by default",
    )
