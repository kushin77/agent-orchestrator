"""engine/loop/queue_adapter — resolve engine.queue tasks with the agent loop.

``engine.queue`` (issue #22) is the job-queue authority: tasks are claimed
(with leases), worked, then acked or failed.  This adapter connects that
queue to the deterministic agent loop: each claimed task's payload is a
session input that the bounded loop resolves, and the loop outcome drives the
settle decision.

Never-a-false-PASS doctrine (leaderboard / ``engine.queue.worker``): the
processor acks **only** when the loop genuinely SUCCEEDED (a final answer
met the confidence threshold and its schema, independently verified when a
``verifier`` is supplied).  Every other terminal outcome — ESCALATED (a
human/higher-tier hand-off, which is *not* a resolution), CANNOT_ASSESS,
BUDGET_EXHAUSTED, FAILED — fails the task with the decision's reason, so the
task lands in the queue's retry/dead-letter surface instead of being
silently marked done.  A task whose worker dies mid-loop is handled by the
queue's lease + orphan-reaper machinery (again never recorded SUCCEEDED).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .model import AgentDecision, LoopOutcome
from .policy import LoopPolicy, Profile
from .runtime import AgentLoop
from .tools import ToolRegistry
from engine.queue.model import Task
from engine.queue.queue import JobQueue, QueueError


@dataclass(frozen=True)
class Outcome:
    """Independent result of one task run (never assumed successful)."""

    ok: bool
    error: Optional[str] = None
    decision: Optional[AgentDecision] = None


class LoopTaskProcessor:
    """Claim -> resolve-with-agent-loop -> ack/fail for one queue + agent.

    Parameters mirror :class:`engine.loop.runtime.AgentLoop` (``actor``,
    ``tools``, ``profile``, ``policy``) plus an optional ``verifier`` that
    independently vetoes a decision before it is acked.
    """

    def __init__(
        self,
        queue: JobQueue,
        agent_id: str,
        *,
        actor: Any,
        tools: ToolRegistry,
        profile: Profile,
        policy: Optional[LoopPolicy] = None,
        verifier: Optional[Callable[[Task, AgentDecision], bool]] = None,
        clock: Any = None,
    ) -> None:
        self._queue = queue
        self._agent = agent_id
        self._actor = actor
        self._tools = tools
        self._profile = profile
        self._policy = policy or LoopPolicy()
        self._verifier = verifier
        self._clock = clock

    # -- lifecycle ---------------------------------------------------------

    def process_once(self, tenant: Optional[str] = None) -> Optional[str]:
        """Claim, run and settle one task; ``None`` when nothing was claimable."""
        queue = self._queue
        task = queue.claim(self._agent, tenant=tenant)
        if task is None:
            return None
        try:
            queue.start(task.task_id, self._agent)
        except QueueError:
            # Lease lost between claim and start (already reaped elsewhere):
            # at-least-once delivery means another worker owns it now.
            return task.task_id

        outcome = self._resolve(task)
        try:
            if outcome.ok:
                queue.ack(task.task_id, self._agent)
            else:
                queue.fail(
                    task.task_id,
                    self._agent,
                    reason=outcome.error or "agent loop did not resolve the task",
                )
        except QueueError:
            # Lease lost while settling (the reaper raced us) — never fabricate.
            pass
        return task.task_id

    def process(self, limit: Optional[int] = None, tenant: Optional[str] = None) -> int:
        """Keep processing until drained or ``limit`` tasks done."""
        processed = 0
        while limit is None or processed < limit:
            task_id = self.process_once(tenant=tenant)
            if task_id is None:
                break
            processed += 1
        return processed

    # -- internals ---------------------------------------------------------

    def _resolve(self, task: Task) -> Outcome:
        payload = task.payload if isinstance(task.payload, dict) else {}
        task_input = payload.get("task_input", payload)
        loop = AgentLoop(
            actor=self._actor,
            tools=self._tools,
            profile=self._profile,
            policy=self._policy,
            clock=self._clock,
        )
        run = loop.run(
            task_input,
            loop_id=f"queue:{task.task_id}",
            tenant_id=task.tenant,
            agent_id=self._profile.agent_id,
            task_id=task.task_id,
        )
        decision = run.decision
        if decision.outcome != LoopOutcome.SUCCEEDED.value:
            return Outcome(
                ok=False,
                error=(f"agent loop ended {decision.outcome}: {decision.reason}"),
                decision=decision,
            )
        if self._verifier is not None and not self._verifier(task, decision):
            return Outcome(ok=False, error="verifier vetoed the agent decision", decision=decision)
        return Outcome(ok=True, decision=decision)
