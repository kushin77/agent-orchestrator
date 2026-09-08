"""engine.multiagent — the engine/loop (issue #23) wiring seam.

``engine/loop`` (work item 19, "Deterministic agent-loop runtime") is the
per-session execution primitive and lands in the same engine wave as this
tree.  The multi-agent contract deliberately matches the loop lane's output
shape — its :class:`AgentDecision` audit record maps onto this package's
``AgentResult`` vocabulary via :func:`decision_to_agent_result`, and an
``engine/loop.AgentLoop`` plugs into the runner seam through
:class:`AgentLoopRunner`:

    run_agent(agent_id, task) -> AgentResult

Nothing here imports ``engine.loop`` eagerly: the base package works with any
runner (a scripted one in tests, a real agent-loop runner later).  When
``engine.loop`` is importable, :func:`loop_runner_if_present` adapts a
duck-typed runner if one is exposed at module level; otherwise the explicit
adapter classes are the wiring point.  A runnable that engine/loop exposes
may be handed to :class:`LoopAgentRunner` (or straight into
``MultiAgentOrchestrator(runner=...)``) unchanged.
"""

from __future__ import annotations

import sys
from typing import Any, Optional

from .model import AgentResult, AgentTask, ResultStatus, ok_result
from .runner import RunnerContractError, coerce_agent_result, is_agent_runner


class LoopSeamError(RuntimeError):
    """Raised when a loop runner violates the duck-typed agent seam."""


_LOOP_SUCCEEDED = "succeeded"  # engine.loop.LoopOutcome.SUCCEEDED.value


def decision_to_agent_result(decision: Any, agent_id: str = "", task_id: str = "") -> AgentResult:
    """Map an ``engine.loop.AgentDecision``-shaped audit record to an
    :class:`AgentResult` (the multi-agent runner vocabulary).

    Duck-typed over the engine/loop contract (issue #23): ``outcome`` /
    ``content`` / ``confidence`` / ``reason`` / ``reasons`` / ``agent_id`` /
    ``task_id``.  A loop that did not end SUCCEEDED (ESCALATED,
    CANNOT_ASSESS, BUDGET_EXHAUSTED, FAILED) maps to a FAILED result — a
    loop that handed off or ran out of budget is never a silent multi-agent
    success.
    """
    outcome = str(getattr(decision, "outcome", "") or "")
    content = getattr(decision, "content", None)
    confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
    reasons = getattr(decision, "reasons", None) or ()
    reason = str(getattr(decision, "reason", "") or "")
    if not reason and reasons:
        reason = str(reasons[0])
    agent = str(getattr(decision, "agent_id", "") or "") or agent_id
    task = str(getattr(decision, "task_id", "") or "") or task_id
    if outcome == _LOOP_SUCCEEDED:
        return ok_result(agent, task, output=content, reasoning=reason, confidence=confidence)
    return AgentResult(
        agent_id=agent,
        task_id=task,
        status=ResultStatus.FAILED,
        output=content,
        reasoning=reason,
        confidence=0.0,
        error=reason or f"loop outcome: {outcome}",
    )


class LoopAgentRunner:
    """Adapter over any object exposing the ``run_agent`` agent-loop seam.

    ``delegate.run_agent(agent_id, task)`` must return an
    :class:`AgentResult` or a JSON-safe dict shaped like one.  A malformed or
    missing return is a contract violation (``LoopSeamError``) — a broken
    loop result is never treated as a silent success.
    """

    def __init__(self, delegate: Any) -> None:
        if not is_agent_runner(delegate):
            raise LoopSeamError(
                "engine/loop runner must expose run_agent(agent_id, task) -> "
                "AgentResult"
            )
        self._delegate = delegate

    @property
    def delegate(self) -> Any:
        return self._delegate

    def run_agent(self, agent_id: str, task: AgentTask) -> AgentResult:
        try:
            raw = self._delegate.run_agent(agent_id, task)
        except Exception as exc:  # noqa: BLE001 - surfaced as a contract breach
            raise LoopSeamError(
                f"engine/loop runner raised for {agent_id}/{task.task_id}: {exc}"
            ) from exc
        try:
            return coerce_agent_result(raw, agent_id, task.task_id)
        except RunnerContractError as exc:
            raise LoopSeamError(str(exc)) from exc


class AgentLoopRunner:
    """Adapt an ``engine/loop.AgentLoop`` (issue #23) to the agent-runner seam.

    ``loop.run(task_input=..., agent_id=..., task_id=...)`` is the engine/loop
    entry point; the returned run exposes the terminal
    :class:`AgentDecision` audit record, which :func:`decision_to_agent_result`
    maps into the multi-agent :class:`AgentResult` vocabulary.  Constructing
    the loop (its actor/tools/profile) is the caller's job — this adapter only
    bridges the two contracts, duck-typed, with no import of ``engine.loop``.
    """

    def __init__(self, loop: Any) -> None:
        if not callable(getattr(loop, "run", None)):
            raise LoopSeamError("loop must expose run(task_input, **kwargs)")
        self._loop = loop

    @property
    def loop(self) -> Any:
        return self._loop

    def run_agent(self, agent_id: str, task: AgentTask) -> AgentResult:
        try:
            run = self._loop.run(
                task_input={
                    "objective": task.objective,
                    "context": dict(task.context),
                },
                agent_id=agent_id,
                task_id=task.task_id,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a contract breach
            raise LoopSeamError(
                f"engine/loop run raised for {agent_id}/{task.task_id}: {exc}"
            ) from exc
        decision = None
        if run is not None:
            getter = getattr(run, "decision", None)
            if callable(getter):
                decision = getter()
            elif getter is not None:
                decision = getter
        if decision is None:
            raise LoopSeamError(
                f"engine/loop run for {agent_id}/{task.task_id} is not terminal "
                "(no decision); resume the loop before fanning out"
            )
        return decision_to_agent_result(decision, agent_id=agent_id, task_id=task.task_id)


def loop_runner_if_present() -> Optional[LoopAgentRunner]:
    """Return a :class:`LoopAgentRunner` over ``engine.loop`` when available.

    Returns ``None`` while ``engine/loop`` (issue #23) has not merged, so
    callers degrade cleanly.  Discovery is duck-typed: any module-level
    *instance* exposing ``run_agent`` is accepted — no hard dependency on the
    loop lane's internal names, and classes are skipped so an uninstantiated
    runner type is never picked up.
    """
    try:
        import engine.loop  # type: ignore  # not on master yet (issue #23)
    except Exception:  # noqa: BLE001 - absent module is the expected state
        return None
    module = sys.modules.get("engine.loop")
    if module is None:
        return None
    for value in vars(module).values():
        if isinstance(value, type):  # an uninstantiated runner type: skip
            continue
        if is_agent_runner(value) and not isinstance(value, LoopAgentRunner):
            return LoopAgentRunner(value)
    return None

