"""engine.multiagent — integration seam onto engine/queue (issue #22).

Proves the "fan-out tasks are enqueued and claimed" integration: a
:class:`QueueFanOut` turns each subtask of a planner plan into a queue task
(``TaskSpec`` with an idempotency key), then runs a queue
:class:`~engine.queue.worker.Worker` over that tenant so every subtask flows
through the real lifecycle — ``enqueue -> claim(lease) -> start -> run ->
ack/fail`` — with the injected runner as the task handler.  A subtask whose
agent fails is *never* recorded SUCCEEDED: the worker fails it (never-a-false
PASS doctrine), and the report reflects the queue's terminal state.

Imported as ``engine.multiagent.queue_seam`` with the repo root on
``sys.path`` (mirrors ``engine.queue`` itself).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple, Union

from engine.queue.model import Priority, TaskSpec, coerce_priority
from engine.queue.queue import JobQueue
from engine.queue.worker import Worker

from .fanout import FanOutLimitError
from .model import AgentTask, FanOutPlan, Subtask
from .runner import coerce_agent_result


@dataclass(frozen=True)
class QueueFanOutReport:
    """Result of one queue-backed fan-out dispatch."""

    plan: FanOutPlan
    tenant: str
    worker_agent: str
    processed: int  # claims the worker settled
    outcomes: Tuple[Dict[str, Any], ...]  # per-subtask final queue status

    @property
    def succeeded(self) -> int:
        return sum(1 for o in self.outcomes if o.get("status") == "succeeded")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.get("status") != "succeeded")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tenant": self.tenant,
            "worker_agent": self.worker_agent,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "outcomes": list(self.outcomes),
        }


class QueueFanOut:
    """Dispatch a fan-out plan through an engine.queue ``JobQueue``."""

    def __init__(
        self,
        queue: JobQueue,
        runner: object,
        *,
        tenant: Optional[str] = None,
        worker_agent: str = "ma-worker",
        priority: Union[str, Priority] = Priority.NORMAL,
        max_fan_out: int = 16,
    ) -> None:
        if not callable(getattr(runner, "run_agent", None)):
            raise TypeError("runner must expose run_agent(agent_id, task)")
        if max_fan_out < 1:
            raise ValueError("max_fan_out must be >= 1")
        self._queue = queue
        self._runner = runner
        self._tenant = tenant
        self._worker_agent = worker_agent
        self._priority = coerce_priority(priority)
        self._max_fan_out = int(max_fan_out)

    def dispatch(self, plan: FanOutPlan) -> QueueFanOutReport:
        """Enqueue every subtask, then drain the tenant with a queue worker."""
        if len(plan.subtasks) > self._max_fan_out:
            raise FanOutLimitError(
                f"plan for mission {plan.mission_id!r} has "
                f"{len(plan.subtasks)} subtasks; bounded fan-out cap is "
                f"{self._max_fan_out}"
            )
        tenant = self._tenant or f"ma-{plan.mission_id}"
        for subtask in plan.subtasks:
            self._queue.enqueue(
                TaskSpec(
                    task_id=subtask.subtask_id,
                    tenant=tenant,
                    priority=self._priority,
                    payload={
                        "mission_id": plan.mission_id,
                        "subtask": subtask.as_dict(),
                    },
                    idempotency_key=subtask.subtask_id,
                )
            )
        worker = Worker(
            self._queue,
            agent_id=self._worker_agent,
            handler=_make_queue_handler(self._runner),
        )
        processed = worker.work_loop(tenant=tenant)
        outcomes = tuple(self._outcome(subtask) for subtask in plan.subtasks)
        return QueueFanOutReport(
            plan=plan,
            tenant=tenant,
            worker_agent=self._worker_agent,
            processed=processed,
            outcomes=outcomes,
        )

    def _outcome(self, subtask: Subtask) -> Dict[str, Any]:
        task = self._queue.get(subtask.subtask_id)
        # Normalize the queue's uppercase TaskState to the multi-agent model's
        # lowercase ResultStatus vocabulary (succeeded / failed / ...).
        status = task.status.value.lower() if task is not None else "missing"
        error = task.last_error if task is not None else None
        return {
            "subtask_id": subtask.subtask_id,
            "task_id": subtask.subtask_id,
            "status": status,
            "error": error,
        }


def _make_queue_handler(runner: object):
    """A queue Worker handler that runs the subtask through the runner.

    The runner's failure (or a raise) makes the Worker ``fail`` the task — a
    failed agent is never recorded SUCCEEDED (never-a-false-PASS doctrine).
    """

    def _handle(task: Any) -> Any:
        payload = task.payload or {}
        subtask = payload.get("subtask") or {}
        agent_id = subtask.get("agent_id") or task.lease_agent or ""
        agent_task = AgentTask(
            task_id=subtask.get("subtask_id") or task.task_id,
            objective=subtask.get("objective", ""),
            lane_id=subtask.get("lane_id", ""),
            agent_id=agent_id,
            context=dict(subtask.get("context") or {}),
        )
        result = coerce_agent_result(
            runner.run_agent(agent_id, agent_task), agent_id, agent_task.task_id
        )
        if not result.ok:
            raise RuntimeError(result.error or "agent failed")
        return result.output

    return _handle
