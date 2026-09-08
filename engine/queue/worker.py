"""engine/queue.worker — a small worker that exercises the lifecycle loop.

Issue #22's worker side plus the *never a false PASS* doctrine (leaderboard
fanout-bug doctrine: a task that died under ``set -e`` was once recorded as
done). Here the worker:

1. ``claim``s a PENDING task (acquiring a lease),
2. ``start``s it (CLAIMED -> RUNNING),
3. runs a user ``handler``, then
4. ``ack``s ONLY on an independently verified success — otherwise ``fail``s.

An exception — including ``SystemExit``, the Python analogue of a shell
``set -e`` abort — or a veto from the optional ``verifier`` results in
``fail``: the queue records FAILED (or retries), never SUCCEEDED. A worker
killed outright (no ack, no fail) leaves a leased task whose lease lapses;
the orphan reaper then requeues or dead-letters it — again, never SUCCEEDED.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from engine.queue.model import Task
from engine.queue.queue import JobQueue, QueueError


@dataclass(frozen=True)
class Outcome:
    """Independent outcome of one handler run (never assumed successful)."""

    ok: bool
    error: Optional[str] = None


class Worker:
    """A claim -> run -> ack/fail worker bound to one queue and agent id."""

    def __init__(
        self,
        queue: JobQueue,
        agent_id: str,
        handler: Callable[[Task], object],
        verifier: Optional[Callable[[Task, Outcome], bool]] = None,
    ) -> None:
        self._queue = queue
        self._agent = agent_id
        self._handler = handler
        self._verifier = verifier

    def work_once(self, tenant: Optional[str] = None) -> Optional[str]:
        """Claim, run and settle one task.

        Returns the task id when one was processed, or ``None`` when the queue
        held nothing claimable.
        """
        queue = self._queue
        task = queue.claim(self._agent, tenant=tenant)
        if task is None:
            return None
        try:
            queue.start(task.task_id, self._agent)
        except QueueError:
            # Lease lost between claim and start (already reaped elsewhere):
            # at-least-once delivery means someone else owns it now.
            return task.task_id

        outcome = self._execute(task)
        settled = outcome.ok and (
            self._verifier is None or self._verifier(task, outcome)
        )
        try:
            if settled:
                queue.ack(task.task_id, self._agent)
            else:
                queue.fail(
                    task.task_id,
                    self._agent,
                    reason=outcome.error or "verifier vetoed the result",
                )
        except QueueError:
            # Lease lost while settling (reaper raced us) — the task was
            # already requeued/reaped elsewhere; never fabricate a success.
            pass
        return task.task_id

    def work_loop(
        self, limit: Optional[int] = None, tenant: Optional[str] = None
    ) -> int:
        """Keep working until the queue is drained (or ``limit`` tasks done)."""
        processed = 0
        while limit is None or processed < limit:
            task_id = self.work_once(tenant=tenant)
            if task_id is None:
                break
            processed += 1
        return processed

    def _execute(self, task: Task) -> Outcome:
        try:
            self._handler(task)
        except SystemExit as exc:  # shell `set -e`-style abort -> FAILED
            return Outcome(
                ok=False, error=f"SystemExit({exc.code}) while executing"
            )
        except Exception as exc:  # noqa: BLE001 - any failure is a FAILED task
            return Outcome(ok=False, error=f"{type(exc).__name__}: {exc}")
        return Outcome(ok=True)
