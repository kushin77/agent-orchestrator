"""Integration with engine.queue: the loop resolves queue tasks (issue #22).

engine.queue is the job-queue authority.  The LoopTaskProcessor claims a
task, resolves its payload with the deterministic agent loop, and settles it:
ack **only** on a genuine SUCCEEDED decision (optionally independently
verified), otherwise fail — a loop that escalates or cannot assess is never
silently recorded done (the queue never-a-false-PASS doctrine).
"""

from __future__ import annotations

from engine.loop.queue_adapter import LoopTaskProcessor
from engine.queue.config import QueueConfig
from engine.queue.model import TaskSpec
from engine.queue.queue import JobQueue
from engine.queue.state import TaskState
from loop_support import (
    TENANT,
    DisallowedToolActor,
    FinalActor,
    ToolThenFinalActor,
    make_policy,
    make_profile,
    make_tools,
)

PAYLOAD = {"task_input": {"prompt": "resolve ticket T-1024"}}


def _queue(max_attempts: int = 5) -> JobQueue:
    return JobQueue(config=QueueConfig(max_attempts=max_attempts))


def _processor(queue: JobQueue, actor, *, verifier=None) -> LoopTaskProcessor:
    return LoopTaskProcessor(
        queue,
        "worker-1",
        actor=actor,
        tools=make_tools(),
        profile=make_profile(),
        verifier=verifier,
    )


def test_successful_loop_acks_the_task():
    queue = _queue()
    queue.enqueue(TaskSpec(task_id="t-ok", tenant=TENANT, payload=PAYLOAD))
    processor = _processor(queue, ToolThenFinalActor())
    assert processor.process_once(tenant=TENANT) == "t-ok"
    assert queue.get("t-ok").status == TaskState.SUCCEEDED


def test_escalated_loop_never_acks():
    queue = _queue(max_attempts=1)
    queue.enqueue(TaskSpec(task_id="t-esc", tenant=TENANT, payload=PAYLOAD))
    processor = _processor(queue, FinalActor(0.4))  # low confidence -> human
    processor.process_once(tenant=TENANT)
    task = queue.get("t-esc")
    assert task.status != TaskState.SUCCEEDED
    assert task.status == TaskState.FAILED  # dead-lettered, not silently done


def test_cannot_assess_loop_never_false_passes():
    queue = _queue(max_attempts=1)
    queue.enqueue(TaskSpec(task_id="t-ca", tenant=TENANT, payload=PAYLOAD))
    processor = LoopTaskProcessor(
        queue,
        "worker-1",
        actor=DisallowedToolActor(),
        tools=make_tools(),
        profile=make_profile(),
        policy=make_policy(on_allowlist_violation="cannot_assess"),
    )
    processor.process_once(tenant=TENANT)
    assert queue.get("t-ca").status == TaskState.FAILED


def test_verifier_veto_prevents_the_ack():
    queue = _queue(max_attempts=1)
    queue.enqueue(TaskSpec(task_id="t-veto", tenant=TENANT, payload=PAYLOAD))
    processor = _processor(queue, ToolThenFinalActor(), verifier=lambda task, d: False)
    processor.process_once(tenant=TENANT)
    task = queue.get("t-veto")
    assert task.status == TaskState.FAILED
    assert "verifier" in (task.last_error or "")


def test_processor_drains_a_batch_of_tasks():
    queue = _queue()
    for index in range(3):
        queue.enqueue(
            TaskSpec(
                task_id=f"t-{index}",
                tenant=TENANT,
                payload={"task_input": {"prompt": f"resolve {index}"}},
            )
        )
    processed = _processor(queue, ToolThenFinalActor()).process(tenant=TENANT)
    assert processed == 3
    assert all(queue.get(f"t-{i}").status == TaskState.SUCCEEDED for i in range(3))
